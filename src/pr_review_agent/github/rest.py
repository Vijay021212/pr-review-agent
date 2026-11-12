"""`GitHub` implemented with httpx and GitHub's REST API (design section 3)."""

from __future__ import annotations

import re
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx

from pr_review_agent.domain import (
    ChangedFile,
    Deadline,
    FileStatus,
    LinkedIssue,
    PullRequest,
    PullRequestRef,
    PullRequestState,
    ReviewIncomplete,
    SetupError,
)
from pr_review_agent.github.diff import parse_patch
from pr_review_agent.github.snapshot import MAX_SNAPSHOT_BYTES, unpack, write_limited
from pr_review_agent.retry import is_temporary, retry_after, send_with_retries
from pr_review_agent.version import __version__

API_URL = "https://api.github.com"
REQUEST_SECONDS = 60.0
FILES_PER_PAGE = 100
MAX_LISTED_FILES = 3000  # GitHub lists at most this many changed files of a pull request
MAX_LINKED_ISSUES = 3

_ACCESS_FIX = (
    "Set PRRA_GITHUB_TOKEN (or GITHUB_TOKEN) to a fine-grained token with "
    "Contents: read and Pull requests: write on the repository."
)
_NOT_FOUND_FIX = (
    "Check the link. For a private repository, set PRRA_GITHUB_TOKEN (or GITHUB_TOKEN) "
    "to a token that can read it."
)

# GitHub's words for "this pull request fixes issue N", for example "Fixes #12".
_LINKED_ISSUE = re.compile(r"\b(?:close[sd]?|fix(?:e[sd])?|resolve[sd]?)\s*:?\s+#(\d+)\b", re.I)

# GitHub reports a few more statuses than the domain needs.
_FILE_STATUSES = {
    "added": FileStatus.ADDED,
    "copied": FileStatus.ADDED,
    "removed": FileStatus.REMOVED,
    "renamed": FileStatus.RENAMED,
    "modified": FileStatus.MODIFIED,
    "changed": FileStatus.MODIFIED,
    "unchanged": FileStatus.MODIFIED,  # only the file mode changed
}


class GitHubRest:
    """Reads pull requests from github.com. The token is optional for public repositories."""

    def __init__(
        self,
        token: str | None,
        *,
        client: httpx.Client | None = None,
        base_url: str = API_URL,
        sleep: Callable[[float], None] = time.sleep,
        clock: Callable[[], float] = time.time,
    ) -> None:
        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": f"pr-review-agent/{__version__}",
        }
        if token:
            headers["Authorization"] = f"Bearer {token}"
        self._client = client or httpx.Client()
        self._headers = headers
        self._base_url = base_url.rstrip("/")
        self._sleep = sleep
        self._clock = clock

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        repo_path = f"/repos/{ref.owner}/{ref.repo}"
        data = self._get(f"{repo_path}/pulls/{ref.number}", deadline, f"pull request {ref}")
        files = self._changed_files(ref, deadline)
        if len(files) < data.get("changed_files", 0):
            raise ReviewIncomplete(
                f"the pull request changes {data['changed_files']} files, but GitHub lists "
                f"only the first {len(files)}, so it can't be reviewed completely"
            )
        # The head repository is missing when a fork was deleted; GitHub then still serves
        # the head commit from the base repository.
        head_repo = data["head"].get("repo") or data["base"]["repo"]
        languages = self._get(f"{repo_path}/languages", deadline, "the repository's languages")
        description = data.get("body") or ""
        return PullRequest(
            owner=ref.owner,
            repo=ref.repo,
            number=ref.number,
            state=_state(data),
            draft=bool(data.get("draft")),
            title=data["title"],
            description=description,
            linked_issues=self._linked_issues(ref, description, deadline),
            head_sha=data["head"]["sha"],
            head_owner=head_repo["owner"]["login"],
            head_repo=head_repo["name"],
            base_branch=data["base"]["ref"],
            files=files,
            # GitHub lists languages by bytes of code, most first.
            languages=tuple(sorted(languages, key=lambda name: -languages[name])),
        )

    def download_snapshot(
        self, pull_request: PullRequest, folder: Path, deadline: Deadline
    ) -> Path:
        """Download and unpack the repository at the reviewed commit; return its root.

        The tarball comes from the head repository, which is the fork for a pull request
        from a fork (FR-20), at the exact head commit (FR-19).
        """
        path = (
            f"/repos/{pull_request.head_owner}/{pull_request.head_repo}"
            f"/tarball/{pull_request.head_sha}"
        )
        archive = folder / "snapshot.tar.gz"
        try:
            response = self._send(path, deadline, stream=True)
            try:
                if response.is_error:
                    self._check(response, "the repository snapshot")
                write_limited(response.iter_bytes(), archive, MAX_SNAPSHOT_BYTES)
            finally:
                response.close()
            return unpack(archive, folder / "repository")
        finally:
            archive.unlink(missing_ok=True)  # only the unpacked files are kept

    def _changed_files(self, ref: PullRequestRef, deadline: Deadline) -> tuple[ChangedFile, ...]:
        files: list[ChangedFile] = []
        path = f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/files"
        for page in range(1, MAX_LISTED_FILES // FILES_PER_PAGE + 1):
            params = {"per_page": FILES_PER_PAGE, "page": page}
            batch = self._get(path, deadline, "the changed files", params=params)
            files += [_changed_file(item) for item in batch]
            if len(batch) < FILES_PER_PAGE:
                break
        return tuple(files)

    def _linked_issues(
        self, ref: PullRequestRef, description: str, deadline: Deadline
    ) -> tuple[LinkedIssue, ...]:
        numbers = list(dict.fromkeys(int(n) for n in _LINKED_ISSUE.findall(description)))
        issues = []
        for number in numbers[:MAX_LINKED_ISSUES]:
            response = self._send(f"/repos/{ref.owner}/{ref.repo}/issues/{number}", deadline)
            if response.status_code in (404, 410):
                continue  # deleted, moved, or not visible to this token: nothing to read
            data = self._check(response, f"issue #{number}")
            issues.append(
                LinkedIssue(number=number, title=data["title"], body=data.get("body") or "")
            )
        return tuple(issues)

    def _get(
        self,
        path: str,
        deadline: Deadline,
        what: str,
        *,
        params: dict[str, int] | None = None,
    ) -> Any:
        return self._check(self._send(path, deadline, params=params), what)

    def _send(
        self,
        path: str,
        deadline: Deadline,
        *,
        params: dict[str, int] | None = None,
        stream: bool = False,
    ) -> httpx.Response:
        def send() -> httpx.Response:
            request = self._client.build_request(
                "GET",
                self._base_url + path,
                params=params,
                headers=self._headers,
                timeout=min(REQUEST_SECONDS, max(deadline.remaining(), 1.0)),
            )
            # Redirects are followed: GitHub sends downloads to another host, and httpx
            # drops the token when it leaves api.github.com.
            response = self._client.send(request, stream=stream, follow_redirects=True)
            if stream and response.is_error:
                response.read()  # error bodies are small; reading them lets the checks see them
            return response

        return send_with_retries(
            send,
            service="GitHub",
            deadline=deadline,
            temporary=self._is_temporary,
            wait_for=self._wait_for,
            sleep=self._sleep,
        )

    def _check(self, response: httpx.Response, what: str) -> Any:
        """The response's JSON, or a clear error saying what went wrong and how to fix it."""
        if response.status_code == 401:
            raise SetupError("GitHub rejected the token.", _ACCESS_FIX)
        if response.status_code == 403:
            raise SetupError(f"The GitHub token isn't allowed to read {what}.", _ACCESS_FIX)
        if response.status_code == 404:
            raise SetupError(f"GitHub couldn't find {what}.", _NOT_FOUND_FIX)
        if response.is_error:
            raise ReviewIncomplete(
                f"GitHub answered {response.status_code} when reading {what}: {_message(response)}"
            )
        return response.json()

    def _is_temporary(self, response: httpx.Response) -> bool:
        """5xx and 429, plus 403 when it's a rate limit rather than a missing permission."""
        if is_temporary(response):
            return True
        return response.status_code == 403 and (
            response.headers.get("x-ratelimit-remaining") == "0"
            or "retry-after" in response.headers
            or "rate limit" in _message(response).lower()
        )

    def _wait_for(self, response: httpx.Response) -> float | None:
        """`Retry-After`, or else the time until the rate limit resets."""
        asked = retry_after(response)
        if asked is not None:
            return asked
        reset = response.headers.get("x-ratelimit-reset", "")
        return max(0.0, float(reset) - self._clock()) if reset.isdigit() else None


def _state(data: dict[str, Any]) -> PullRequestState:
    if data.get("merged") or data.get("merged_at"):
        return PullRequestState.MERGED
    return PullRequestState.CLOSED if data["state"] == "closed" else PullRequestState.OPEN


def _changed_file(item: dict[str, Any]) -> ChangedFile:
    status = _FILE_STATUSES.get(item["status"], FileStatus.MODIFIED)
    patch = item.get("patch")
    return ChangedFile(
        path=item["filename"],
        status=status,
        previous_path=item.get("previous_filename") if status is FileStatus.RENAMED else None,
        lines=parse_patch(patch) if patch else (),
        # GitHub leaves the patch out for binary files and very large diffs. A file with
        # no line changes at all (a pure rename, a mode change) has nothing to leave out.
        diff_available=patch is not None or item.get("changes", 0) == 0,
    )


def _message(response: httpx.Response) -> str:
    try:
        data = response.json()
    except ValueError:
        return response.text
    return str(data.get("message", response.text)) if isinstance(data, dict) else response.text

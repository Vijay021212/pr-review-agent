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
    ReviewComment,
    ReviewIncomplete,
    SetupError,
    Side,
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

    def check_access(self, ref: PullRequestRef, *, write: bool, deadline: Deadline) -> None:
        """The token can read the repository and, when posting, may write to it.

        A classic token lists its scopes in a response header, so a missing write scope is
        caught here. A fine-grained token doesn't reveal its permissions in advance; a
        missing "Pull requests: write" then shows only when posting, with its own message.
        """
        response = self._send(f"/repos/{ref.owner}/{ref.repo}", deadline)
        self._check(response, f"the repository {ref.owner}/{ref.repo}")
        scopes_header = response.headers.get("x-oauth-scopes")
        if write and scopes_header is not None:
            scopes = {scope.strip() for scope in scopes_header.split(",") if scope.strip()}
            if not scopes & {"repo", "public_repo"}:
                raise SetupError(
                    "The GitHub token can't post reviews: it has "
                    f"{', '.join(sorted(scopes)) or 'no'} scopes.",
                    "Give the classic token the repo (or public_repo) scope, or use a "
                    "fine-grained token with Contents: read and Pull requests: write.",
                )

    def head_sha(self, ref: PullRequestRef, deadline: Deadline) -> str:
        path = f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}"
        sha: str = self._get(path, deadline, f"pull request {ref}")["head"]["sha"]
        return sha

    def post_review(
        self,
        ref: PullRequestRef,
        commit_sha: str,
        body: str,
        comments: tuple[ReviewComment, ...],
        deadline: Deadline,
    ) -> str:
        review = {
            "commit_id": commit_sha,
            "body": body,
            # Always a plain comment: the agent never approves or blocks (FR-12).
            "event": "COMMENT",
            "comments": [
                {
                    "path": comment.path,
                    "line": comment.line,
                    "side": "RIGHT" if comment.side is Side.NEW else "LEFT",
                    "body": comment.body,
                }
                for comment in comments
            ],
        }
        path = f"/repos/{ref.owner}/{ref.repo}/pulls/{ref.number}/reviews"
        url: str = self._write("POST", path, review, deadline, "the review")["html_url"]
        return url

    def upsert_summary(
        self, ref: PullRequestRef, body: str, marker: str, deadline: Deadline
    ) -> str:
        repo_path = f"/repos/{ref.owner}/{ref.repo}"
        earlier = self._earlier_summary(ref, marker, deadline)
        if earlier is not None:
            response = self._send(
                f"{repo_path}/issues/comments/{earlier}",
                deadline,
                method="PATCH",
                body={"body": body},
            )
            # Someone else's comment can carry the marker too; then a new one is posted.
            if response.status_code not in (403, 404):
                url: str = self._check(response, "the summary", doing="update")["html_url"]
                return url
        new: str = self._write(
            "POST",
            f"{repo_path}/issues/{ref.number}/comments",
            {"body": body},
            deadline,
            "the summary",
        )["html_url"]
        return new

    def _earlier_summary(self, ref: PullRequestRef, marker: str, deadline: Deadline) -> int | None:
        """The id of the newest comment that starts with `marker`, if there is one."""
        path = f"/repos/{ref.owner}/{ref.repo}/issues/{ref.number}/comments"
        found = None
        for page in range(1, MAX_LISTED_FILES // FILES_PER_PAGE + 1):
            params = {"per_page": FILES_PER_PAGE, "page": page}
            comments = self._get(path, deadline, "the pull request's comments", params=params)
            for comment in comments:
                if (comment.get("body") or "").startswith(marker):
                    found = int(comment["id"])
            if len(comments) < FILES_PER_PAGE:
                break
        return found

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
        method: str = "GET",
        body: dict[str, Any] | None = None,
    ) -> httpx.Response:
        def send() -> httpx.Response:
            request = self._client.build_request(
                method,
                self._base_url + path,
                params=params,
                json=body,
                headers=self._headers,
                timeout=min(REQUEST_SECONDS, max(deadline.remaining(), 1.0)),
            )
            # Redirects are followed: GitHub sends downloads to another host, and httpx
            # drops the token when it leaves api.github.com.
            response = self._client.send(request, stream=stream, follow_redirects=True)
            if stream and response.is_error:
                response.read()  # error bodies are small; reading them lets the checks see them
            return response

        # A write that failed with a server error or a dropped connection may still have
        # happened, so it's never sent again; only a rate limit (refused, so not done) is.
        write = method != "GET"
        return send_with_retries(
            send,
            service="GitHub",
            deadline=deadline,
            temporary=self._is_rate_limit if write else self._is_temporary,
            wait_for=self._wait_for,
            sleep=self._sleep,
            retry_network_errors=not write,
        )

    def _write(
        self, method: str, path: str, body: dict[str, Any], deadline: Deadline, what: str
    ) -> Any:
        return self._check(self._send(path, deadline, method=method, body=body), what, doing="post")

    def _check(self, response: httpx.Response, what: str, *, doing: str = "read") -> Any:
        """The response's JSON, or a clear error saying what went wrong and how to fix it."""
        if response.status_code == 401:
            raise SetupError("GitHub rejected the token.", _ACCESS_FIX)
        if response.status_code == 403:
            raise SetupError(f"The GitHub token isn't allowed to {doing} {what}.", _ACCESS_FIX)
        if response.status_code == 404:
            raise SetupError(f"GitHub couldn't find {what}.", _NOT_FOUND_FIX)
        if response.is_error:
            raise ReviewIncomplete(
                f"GitHub answered {response.status_code} when trying to {doing} {what}: "
                f"{_message(response)}"
            )
        return response.json()

    def _is_temporary(self, response: httpx.Response) -> bool:
        """5xx and 429, plus 403 when it's a rate limit rather than a missing permission."""
        return is_temporary(response) or self._is_rate_limit(response)

    def _is_rate_limit(self, response: httpx.Response) -> bool:
        """429, or a 403 that's a rate limit rather than a missing permission."""
        if response.status_code == 429:
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

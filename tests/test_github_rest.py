"""Tests for reading a pull request from GitHub, with replies recorded from this repository."""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_review_agent.domain import (
    Deadline,
    FileStatus,
    PullRequestRef,
    PullRequestState,
    ReviewComment,
    ReviewIncomplete,
    SetupError,
    Side,
)
from pr_review_agent.github.rest import GitHubRest
from tests.builders import SHA

FIXTURES = Path(__file__).parent / "fixtures" / "github"
REF = PullRequestRef(owner="Vijay021212", repo="pr-review-agent", number=32)
REPO = "/repos/Vijay021212/pr-review-agent"


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


class FakeApi:
    """Serves recorded replies by path, and records every request it gets."""

    def __init__(self, routes: dict[str, Any] | None = None) -> None:
        self.routes: dict[str, Any] = {
            f"{REPO}/pulls/32": recorded("pull_open.json"),
            f"{REPO}/pulls/32/files": recorded("pull_open_files.json"),
            f"{REPO}/languages": recorded("languages.json"),
            f"{REPO}/issues/2": recorded("issue.json"),
            **(routes or {}),
        }
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        route = self.routes.get(request.url.path)
        if callable(route):
            result: httpx.Response = route(request)
            return result
        if route is None:
            return httpx.Response(404, json={"message": "Not Found"})
        if isinstance(route, httpx.Response):
            return route
        return httpx.Response(200, json=route)


def make_github(
    api: Callable[[httpx.Request], httpx.Response],
    *,
    token: str | None = "ghp_test",
    waits: list[float] | None = None,
) -> GitHubRest:
    return GitHubRest(
        token,
        client=httpx.Client(transport=httpx.MockTransport(api)),
        sleep=(waits if waits is not None else []).append,
        clock=lambda: 1000.0,
    )


def read(github: GitHubRest, ref: PullRequestRef = REF) -> Any:
    return github.get_pull_request(ref, Deadline(600))


def with_pull(**changes: Any) -> dict[str, Any]:
    pull: dict[str, Any] = copy.deepcopy(recorded("pull_open.json"))
    pull.update(changes)
    return pull


def test_a_recorded_pull_request_is_read_completely() -> None:
    pull_request = read(make_github(FakeApi()))
    assert pull_request.ref == REF
    assert pull_request.state is PullRequestState.OPEN
    assert not pull_request.draft
    assert pull_request.title == "Add the domain types, interfaces and time limit"
    assert pull_request.description.startswith("Closes #2")
    assert pull_request.head_sha == "ccd3c701eea7747e3065294e19d09d2fd1fc659d"
    assert (pull_request.head_owner, pull_request.head_repo) == ("Vijay021212", "pr-review-agent")
    assert not pull_request.is_fork
    assert pull_request.base_branch == "main"
    assert pull_request.languages == ("Python",)
    assert [issue.number for issue in pull_request.linked_issues] == [2]
    assert pull_request.linked_issues[0].title == "Data types and interfaces (domain.py)"
    assert len(pull_request.files) == 8


def test_changed_files_keep_their_diffs_and_line_numbers() -> None:
    files = {file.path: file for file in read(make_github(FakeApi())).files}
    domain = files["src/pr_review_agent/domain.py"]
    assert domain.status is FileStatus.ADDED
    assert domain.added_lines == set(range(1, 556))
    pyproject = files["pyproject.toml"]
    assert (len(pyproject.added_lines), len(pyproject.removed_lines)) == (5, 1)
    # An empty new file has no patch, and nothing is missing from it.
    empty = files["tests/__init__.py"]
    assert empty.lines == ()
    assert empty.diff_available


def test_requests_carry_the_token_and_api_version() -> None:
    api = FakeApi()
    read(make_github(api))
    request = api.requests[0]
    assert request.headers["Authorization"] == "Bearer ghp_test"
    assert request.headers["X-GitHub-Api-Version"] == "2022-11-28"
    assert request.headers["User-Agent"].startswith("pr-review-agent/")


def test_public_repositories_can_be_read_without_a_token() -> None:
    api = FakeApi()
    read(make_github(api, token=None))
    assert "Authorization" not in api.requests[0].headers


def test_changed_files_are_read_page_by_page() -> None:
    def page(request: httpx.Request) -> httpx.Response:
        number = int(request.url.params["page"])
        count = 100 if number < 3 else 5
        items = [
            {
                "filename": f"f{number}_{i}.py",
                "status": "added",
                "changes": 1,
                "patch": "@@ -0,0 +1,1 @@\n+x",
            }
            for i in range(count)
        ]
        return httpx.Response(200, json=items)

    pull = with_pull(changed_files=205)
    api = FakeApi({f"{REPO}/pulls/32": pull, f"{REPO}/pulls/32/files": page})
    files = read(make_github(api)).files
    assert len(files) == 205
    pages = [r.url.params["page"] for r in api.requests if r.url.path.endswith("/files")]
    assert pages == ["1", "2", "3"]


def test_listing_stops_at_githubs_limit_of_3000_files() -> None:
    def full_page(request: httpx.Request) -> httpx.Response:
        page = request.url.params["page"]
        items = [
            {"filename": f"{page}_{i}.py", "status": "added", "changes": 0} for i in range(100)
        ]
        return httpx.Response(200, json=items)

    api = FakeApi(
        {f"{REPO}/pulls/32": with_pull(changed_files=3000), f"{REPO}/pulls/32/files": full_page}
    )
    assert len(read(make_github(api)).files) == 3000
    assert sum(1 for r in api.requests if r.url.path.endswith("/files")) == 30


def test_more_files_than_github_lists_makes_the_review_incomplete() -> None:
    api = FakeApi({f"{REPO}/pulls/32": with_pull(changed_files=5000)})
    with pytest.raises(ReviewIncomplete, match="changes 5000 files, but GitHub lists only"):
        read(make_github(api))


@pytest.mark.parametrize(
    ("item", "status", "available", "previous"),
    [
        (
            {"status": "renamed", "previous_filename": "old.py", "changes": 0},
            FileStatus.RENAMED,
            True,
            "old.py",
        ),
        ({"status": "added", "changes": 4000}, FileStatus.ADDED, False, None),  # too large
        ({"status": "added", "changes": 0}, FileStatus.ADDED, True, None),  # binary or empty
        (
            {"status": "copied", "changes": 1, "patch": "@@ -0,0 +1 @@\n+x"},
            FileStatus.ADDED,
            True,
            None,
        ),
        ({"status": "changed", "changes": 0}, FileStatus.MODIFIED, True, None),
        ({"status": "unknown-new-status", "changes": 0}, FileStatus.MODIFIED, True, None),
    ],
)
def test_file_statuses_and_missing_diffs(
    item: dict[str, Any], status: FileStatus, available: bool, previous: str | None
) -> None:
    files = [{"filename": "new.py", **item}]
    api = FakeApi({f"{REPO}/pulls/32": with_pull(changed_files=1), f"{REPO}/pulls/32/files": files})
    (file,) = read(make_github(api)).files
    assert (file.status, file.diff_available, file.previous_path) == (status, available, previous)


def test_a_pull_request_from_a_fork_points_to_the_fork() -> None:
    pull = with_pull()
    pull["head"]["repo"]["owner"]["login"] = "contributor"
    pull["head"]["repo"]["name"] = "pr-review-agent-fork"
    pull_request = read(make_github(FakeApi({f"{REPO}/pulls/32": pull})))
    assert pull_request.is_fork
    assert (pull_request.head_owner, pull_request.head_repo) == (
        "contributor",
        "pr-review-agent-fork",
    )


def test_a_deleted_fork_falls_back_to_the_base_repository() -> None:
    pull = with_pull()
    pull["head"]["repo"] = None
    pull_request = read(make_github(FakeApi({f"{REPO}/pulls/32": pull})))
    assert (pull_request.head_owner, pull_request.head_repo) == ("Vijay021212", "pr-review-agent")


@pytest.mark.parametrize(
    ("changes", "state"),
    [
        ({"state": "closed", "merged": True}, PullRequestState.MERGED),
        ({"state": "closed", "merged": False, "merged_at": None}, PullRequestState.CLOSED),
        ({"draft": True}, PullRequestState.OPEN),
    ],
)
def test_the_state_is_read(changes: dict[str, Any], state: PullRequestState) -> None:
    pull_request = read(make_github(FakeApi({f"{REPO}/pulls/32": with_pull(**changes)})))
    assert pull_request.state is state
    assert pull_request.draft is bool(changes.get("draft"))


def test_the_recorded_merged_pull_request_is_merged() -> None:
    ref = PullRequestRef(owner="Vijay021212", repo="pr-review-agent", number=31)
    api = FakeApi(
        {
            f"{REPO}/pulls/31": recorded("pull_merged.json"),
            f"{REPO}/pulls/31/files": [],
        }
    )
    merged = recorded("pull_merged.json")
    api.routes[f"{REPO}/pulls/31"] = {**merged, "changed_files": 0}
    assert read(make_github(api), ref).state is PullRequestState.MERGED


def test_linked_issues_are_read_once_and_missing_ones_skipped() -> None:
    body = "Fixes #2, closes #2. Resolves: #77. Also mentions #5 without a keyword."
    api = FakeApi(
        {f"{REPO}/pulls/32": with_pull(body=body), f"{REPO}/issues/77": httpx.Response(404)}
    )
    pull_request = read(make_github(api))
    assert [issue.number for issue in pull_request.linked_issues] == [2]
    issue_paths = [r.url.path for r in api.requests if "/issues/" in r.url.path]
    assert issue_paths == [f"{REPO}/issues/2", f"{REPO}/issues/77"]


def test_an_empty_description_has_no_linked_issues() -> None:
    pull_request = read(make_github(FakeApi({f"{REPO}/pulls/32": with_pull(body=None)})))
    assert pull_request.description == ""
    assert pull_request.linked_issues == ()


@pytest.mark.parametrize(
    ("status", "error", "problem", "fix"),
    [
        (
            404,
            SetupError,
            "GitHub couldn't find pull request Vijay021212/pr-review-agent#32.",
            "Check the link",
        ),
        (401, SetupError, "GitHub rejected the token.", "Contents: read and Pull requests: write"),
        (
            403,
            SetupError,
            "The GitHub token isn't allowed to read pull request",
            "fine-grained token",
        ),
    ],
)
def test_setup_problems_say_how_to_fix_them(
    status: int, error: type[SetupError], problem: str, fix: str
) -> None:
    api = FakeApi({f"{REPO}/pulls/32": httpx.Response(status, json={"message": "nope"})})
    with pytest.raises(error) as caught:
        read(make_github(api))
    assert caught.value.problem.startswith(problem)
    assert fix in caught.value.fix


def test_other_errors_make_the_review_incomplete() -> None:
    api = FakeApi({f"{REPO}/pulls/32": httpx.Response(422, json={"message": "Validation Failed"})})
    with pytest.raises(
        ReviewIncomplete,
        match=r"GitHub answered 422 when trying to read pull request .*: Validation Failed",
    ):
        read(make_github(api))


def test_an_error_without_json_shows_its_text() -> None:
    api = FakeApi({f"{REPO}/pulls/32": httpx.Response(418, text="teapot")})
    with pytest.raises(ReviewIncomplete, match=r"418 .*: teapot"):
        read(make_github(api))


def test_server_errors_are_retried() -> None:
    replies = [httpx.Response(502), httpx.Response(200, json=recorded("pull_open.json"))]
    waits: list[float] = []
    api = FakeApi({f"{REPO}/pulls/32": lambda request: replies.pop(0)})
    assert read(make_github(api, waits=waits)).number == 32
    assert waits == [1.0]


@pytest.mark.parametrize(
    ("headers", "body", "wait"),
    [
        ({"x-ratelimit-remaining": "0", "x-ratelimit-reset": "1030"}, {}, 30.0),
        ({"retry-after": "7"}, {}, 7.0),
        ({}, {"message": "You have exceeded a secondary rate limit."}, 1.0),
    ],
)
def test_rate_limits_wait_as_long_as_github_says(
    headers: dict[str, str], body: dict[str, str], wait: float
) -> None:
    replies = [
        httpx.Response(403, headers=headers, json=body),
        httpx.Response(200, json=recorded("pull_open.json")),
    ]
    waits: list[float] = []
    api = FakeApi({f"{REPO}/pulls/32": lambda request: replies.pop(0)})
    read(make_github(api, waits=waits))
    assert waits == [wait]


def test_a_rate_limit_longer_than_the_time_limit_stops_the_review() -> None:
    limited = httpx.Response(
        403, headers={"x-ratelimit-remaining": "0", "x-ratelimit-reset": "9999"}
    )
    api = FakeApi({f"{REPO}/pulls/32": limited})
    with pytest.raises(ReviewIncomplete, match="would pass the time limit"):
        read(make_github(api))


# --- Posting ---------------------------------------------------------------------------


def writer(api: Callable[[httpx.Request], httpx.Response]) -> GitHubRest:
    return make_github(api)


def test_the_head_commit_is_read_again() -> None:
    assert read_head() == "ccd3c701eea7747e3065294e19d09d2fd1fc659d"


def read_head() -> str:
    return make_github(FakeApi()).head_sha(REF, Deadline(60))


def test_a_review_is_one_request_with_every_comment_and_never_an_approval() -> None:
    sent: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json={"html_url": "https://github.com/x/pull/32#review-9"})

    comments = (
        ReviewComment(path="src/a.py", line=3, side=Side.NEW, body="New code."),
        ReviewComment(path="src/a.py", line=7, side=Side.OLD, body="Removed code."),
    )
    url = make_github(api).post_review(REF, SHA, "Found 2.", comments, Deadline(60))
    assert url == "https://github.com/x/pull/32#review-9"
    (request,) = sent
    assert (request.method, request.url.path) == ("POST", f"{REPO}/pulls/32/reviews")
    body = json.loads(request.content)
    assert body["event"] == "COMMENT"
    assert body["commit_id"] == SHA
    assert body["comments"] == [
        {"path": "src/a.py", "line": 3, "side": "RIGHT", "body": "New code."},
        {"path": "src/a.py", "line": 7, "side": "LEFT", "body": "Removed code."},
    ]


@pytest.mark.parametrize("failure", [httpx.Response(502), httpx.ReadTimeout("no answer")])
def test_a_failed_post_is_never_sent_twice(failure: httpx.Response | Exception) -> None:
    calls: list[httpx.Request] = []

    def api(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        if isinstance(failure, Exception):
            raise failure
        return failure

    with pytest.raises(ReviewIncomplete):
        make_github(api).post_review(REF, SHA, "b", (), Deadline(60))
    assert len(calls) == 1


def test_a_rate_limited_post_is_retried_because_it_was_refused() -> None:
    replies = [
        httpx.Response(429, headers={"retry-after": "2"}),
        httpx.Response(200, json={"html_url": "u"}),
    ]
    waits: list[float] = []
    github = make_github(lambda request: replies.pop(0), waits=waits)
    assert github.post_review(REF, SHA, "b", (), Deadline(60)) == "u"
    assert waits == [2.0]


def test_a_token_that_cant_post_says_how_to_fix_it() -> None:
    api = FakeApi({f"{REPO}/pulls/32/reviews": httpx.Response(403, json={"message": "no"})})
    with pytest.raises(SetupError, match="isn't allowed to post the review") as caught:
        make_github(api).post_review(REF, SHA, "b", (), Deadline(60))
    assert "Pull requests: write" in caught.value.fix


def test_a_comment_github_refuses_makes_the_review_incomplete() -> None:
    refused = httpx.Response(422, json={"message": "Line could not be resolved"})
    api = FakeApi({f"{REPO}/pulls/32/reviews": refused})
    with pytest.raises(ReviewIncomplete, match="422 when trying to post the review: Line could"):
        make_github(api).post_review(REF, SHA, "b", (), Deadline(60))


class CommentsApi:
    """GitHub's issue comments: listed in pages, edited, and created."""

    def __init__(self, *existing: dict[str, Any], edit_status: int = 200) -> None:
        self.existing = list(existing)
        self.edit_status = edit_status
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if request.method == "GET":
            page = int(request.url.params["page"])
            return httpx.Response(200, json=self.existing[(page - 1) * 100 : page * 100])
        if request.method == "PATCH":
            return httpx.Response(self.edit_status, json={"html_url": f"edited:{path}"})
        return httpx.Response(201, json={"html_url": "created"})


def upsert(api: CommentsApi) -> str:
    return make_github(api).upsert_summary(
        REF, "<!-- m -->\nNew summary", "<!-- m -->", Deadline(60)
    )


def test_a_first_summary_is_posted_as_a_new_comment() -> None:
    api = CommentsApi({"id": 1, "body": "Looks good to me!"})
    assert upsert(api) == "created"
    post_request = api.requests[-1]
    assert (post_request.method, post_request.url.path) == ("POST", f"{REPO}/issues/32/comments")
    assert json.loads(post_request.content) == {"body": "<!-- m -->\nNew summary"}


def test_an_earlier_summary_is_updated_instead_of_repeated() -> None:
    api = CommentsApi(
        {"id": 5, "body": "<!-- m -->\nOld summary"},
        {"id": 6, "body": "A person's comment"},
        {"id": 8, "body": "<!-- m -->\nA newer summary"},
        {"id": 9, "body": None},
    )
    assert upsert(api) == f"edited:{REPO}/issues/comments/8"
    assert [r.method for r in api.requests] == ["GET", "PATCH"]


def test_summaries_are_found_across_pages_of_comments() -> None:
    comments = [{"id": n, "body": "chat"} for n in range(150)]
    comments[120] = {"id": 120, "body": "<!-- m -->\nOld"}
    api = CommentsApi(*comments)
    assert upsert(api) == f"edited:{REPO}/issues/comments/120"


@pytest.mark.parametrize("status", [403, 404])
def test_a_summary_that_cant_be_edited_is_posted_anew(status: int) -> None:
    api = CommentsApi(
        {"id": 5, "body": "<!-- m -->\nSomeone quoted the marker"}, edit_status=status
    )
    assert upsert(api) == "created"
    assert [r.method for r in api.requests] == ["GET", "PATCH", "POST"]


def test_the_summary_search_stops_after_githubs_last_page() -> None:
    api = CommentsApi(*[{"id": n, "body": "chat"} for n in range(3000)])
    assert upsert(api) == "created"
    assert sum(1 for r in api.requests if r.method == "GET") == 30

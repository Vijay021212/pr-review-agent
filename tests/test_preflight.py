"""Tests for preflight: every setup problem is caught first, with its fix (FR-16)."""

from __future__ import annotations

import json
from collections.abc import Callable

import httpx
import pytest

from pr_review_agent import Settings
from pr_review_agent.domain import Deadline, PullRequestRef, ReviewIncomplete, SetupError
from pr_review_agent.github.rest import GitHubRest
from pr_review_agent.models.ollama import OllamaChatModel
from pr_review_agent.stages.preflight import preflight
from tests.fakes import FakeChatModel, FakeGitHub

REF = PullRequestRef(owner="octo", repo="shop", number=7)
TOKEN = "ghp_" + "t" * 36


def run(
    settings: Settings | None = None,
    github: FakeGitHub | None = None,
    model: FakeChatModel | None = None,
    *,
    dry_run: bool = True,
) -> FakeGitHub:
    github = github or FakeGitHub()
    preflight(
        REF,
        settings or Settings(),
        github,
        model or FakeChatModel([]),
        Deadline(60),
        dry_run=dry_run,
    )
    return github


def test_a_dry_run_checks_reading_and_the_model() -> None:
    github = run()
    assert github.access_checks == [("octo/shop#7", False)]


def test_posting_checks_writing_too() -> None:
    github = run(Settings(github_token=TOKEN), dry_run=False)
    assert github.access_checks == [("octo/shop#7", True)]


def test_posting_without_a_token_says_how_to_fix_it() -> None:
    github = FakeGitHub()
    with pytest.raises(SetupError, match="needs a GitHub token") as caught:
        run(github=github, dry_run=False)
    assert "PRRA_GITHUB_TOKEN" in caught.value.fix
    assert github.access_checks == []  # nothing else is tried first


def test_a_github_problem_stops_the_review_before_the_model_is_asked() -> None:
    github = FakeGitHub(access_error=SetupError("GitHub rejected the token.", "Make a new one."))
    model = FakeChatModel([], ready_error=AssertionError("must not be reached"))
    with pytest.raises(SetupError, match="rejected the token"):
        run(github=github, model=model)


def test_a_model_problem_is_passed_on() -> None:
    model = FakeChatModel([], ready_error=SetupError("Ollama isn't running.", "Start it."))
    with pytest.raises(SetupError, match="Ollama isn't running"):
        run(model=model)


# --- Ollama's check ---------------------------------------------------------------------


def ollama(
    handler: Callable[[httpx.Request], httpx.Response], model: str = "qwen2.5-coder:7b"
) -> OllamaChatModel:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    return OllamaChatModel(
        "http://localhost:11434", model, 16384, client=client, sleep=lambda s: None
    )


def models(*names: str) -> httpx.Response:
    return httpx.Response(200, json={"models": [{"name": name} for name in names]})


def test_ollama_with_the_model_is_ready() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return models("qwen3-coder:30b", "qwen2.5-coder:7b")

    ollama(handler).check_ready(Deadline(60))
    assert str(sent[0].url) == "http://localhost:11434/api/tags"


def test_a_model_named_without_a_tag_matches_latest() -> None:
    ollama(lambda request: models("llama3:latest"), model="llama3").check_ready(Deadline(60))


def test_ollama_without_the_model_says_how_to_get_it() -> None:
    with pytest.raises(SetupError, match="isn't downloaded") as caught:
        ollama(lambda request: models("qwen3-coder:30b")).check_ready(Deadline(60))
    assert "ollama pull qwen2.5-coder:7b" in caught.value.fix


def test_ollama_not_running_says_how_to_start_it() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    with pytest.raises(SetupError, match="isn't running"):
        ollama(refuse).check_ready(Deadline(60))


def test_an_ollama_error_stops_the_review() -> None:
    with pytest.raises(ReviewIncomplete, match="couldn't list its models"):
        ollama(lambda request: httpx.Response(400, json={"error": "bad"})).check_ready(Deadline(60))


# --- GitHub's check ---------------------------------------------------------------------


def github(response: httpx.Response) -> GitHubRest:
    client = httpx.Client(transport=httpx.MockTransport(lambda request: response))
    return GitHubRest(TOKEN, client=client, sleep=lambda s: None)


def repo(headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(200, json={"full_name": "octo/shop"}, headers=headers or {})


@pytest.mark.parametrize(
    "headers", [{}, {"x-oauth-scopes": "repo, workflow"}, {"x-oauth-scopes": "public_repo"}]
)
def test_a_token_that_may_post_passes(headers: dict[str, str]) -> None:
    github(repo(headers)).check_access(REF, write=True, deadline=Deadline(60))


def test_a_classic_token_without_a_write_scope_is_caught_before_posting() -> None:
    with pytest.raises(SetupError, match="can't post reviews: it has read:user scopes") as caught:
        github(repo({"x-oauth-scopes": "read:user"})).check_access(
            REF, write=True, deadline=Deadline(60)
        )
    assert "repo (or public_repo) scope" in caught.value.fix


def test_a_classic_token_without_any_scope_is_named_as_such() -> None:
    with pytest.raises(SetupError, match="it has no scopes"):
        github(repo({"x-oauth-scopes": ""})).check_access(REF, write=True, deadline=Deadline(60))


def test_reading_needs_no_write_scope() -> None:
    github(repo({"x-oauth-scopes": "read:user"})).check_access(
        REF, write=False, deadline=Deadline(60)
    )


@pytest.mark.parametrize(
    ("status", "message"),
    [(401, "GitHub rejected the token."), (404, "GitHub couldn't find the repository octo/shop.")],
)
def test_a_rejected_token_or_a_hidden_repository_is_caught(status: int, message: str) -> None:
    response = httpx.Response(status, json={"message": "nope"})
    with pytest.raises(SetupError) as caught:
        github(response).check_access(REF, write=False, deadline=Deadline(60))
    assert caught.value.problem == message
    assert json.dumps(caught.value.fix)

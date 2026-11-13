"""Tests for the public function `review_pull_request()` (FR-24, design 12.1)."""

from __future__ import annotations

import json

import pytest

import pr_review_agent
from pr_review_agent import Settings, SetupError, review_pull_request
from pr_review_agent.factory import Components
from pr_review_agent.secrets import Masker
from tests.builders import make_pull_request
from tests.fakes import EXPLORE_DONE, FakeChatModel, FakeGitHub

LINK = "https://github.com/octo/shop/pull/7"
REPLIES = [
    json.dumps(
        {
            "goal": "Add a discount.",
            "summary": "Adds discount().",
            "risky_parts": [],
            "goal_unclear": False,
            "goal_source": "description",
        }
    ),
    EXPLORE_DONE,
    json.dumps({"findings": []}),
    json.dumps({"findings": []}),
]


@pytest.fixture
def built(monkeypatch: pytest.MonkeyPatch) -> list[Settings]:
    """Replace the real parts with fakes, and record the settings the factory was given."""
    seen: list[Settings] = []

    def fake_build(settings: Settings) -> Components:
        seen.append(settings)
        return Components(
            github=FakeGitHub.serving(make_pull_request()),
            model=FakeChatModel(list(REPLIES)),
            masker=Masker(),
        )

    monkeypatch.setattr("pr_review_agent.factory.build", fake_build)
    return seen


def test_a_dry_run_returns_the_whole_review_as_data(built: list[Settings]) -> None:
    result = review_pull_request(LINK, dry_run=True)
    assert result.pull_request.number == 7
    assert result.recommendation.value == "Approve"
    assert result.dry_run


def test_without_settings_they_come_from_the_environment(
    built: list[Settings], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("PRRA_MODEL", "qwen3-coder:30b")
    review_pull_request(LINK, dry_run=True)
    assert built[0].model == "qwen3-coder:30b"


def test_settings_given_in_code_are_used(built: list[Settings]) -> None:
    review_pull_request(LINK, settings=Settings(model="from-code"), dry_run=True)
    assert built[0].model == "from-code"


def test_a_bad_link_is_a_setup_error(built: list[Settings]) -> None:
    with pytest.raises(SetupError, match="isn't a GitHub pull request link"):
        review_pull_request("octo/shop#7", dry_run=True)
    assert built == []


def test_posting_needs_a_token(built: list[Settings]) -> None:
    with pytest.raises(
        SetupError, match="Posting the review on GitHub needs a GitHub token"
    ) as caught:
        review_pull_request(LINK)
    assert "PRRA_GITHUB_TOKEN" in caught.value.fix
    assert "--dry-run" in caught.value.fix


def test_the_public_api_is_what_design_12_1_lists() -> None:
    assert set(pr_review_agent.__all__) == {
        "review_pull_request",
        "Settings",
        "ReviewResult",
        "Understanding",
        "Finding",
        "CheckedFindings",
        "Recommendation",
        "ReviewScope",
        "SetupError",
        "ReviewIncomplete",
        "__version__",
    }

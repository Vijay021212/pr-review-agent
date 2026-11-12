"""Integration tests: a whole review, from link to printed result, through the real pipeline.

Only the outside world is faked: `FakeGitHub` serves the pull request and `FakeChatModel`
answers in the order the stages ask (understand, then one find call per batch). Everything
in between is the real code: link parsing, settings, fetch, file selection, prompts,
structured answers, recommendation rules, masking, the summary and the command line
(plan 3.1, level 2).
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from pr_review_agent import ReviewIncomplete, Settings, cli, review_pull_request
from pr_review_agent.domain import FileStatus, RecommendationValue
from pr_review_agent.factory import Components
from pr_review_agent.secrets import Masker
from tests.builders import make_file, make_pull_request
from tests.fakes import EXPLORE_DONE, FakeChatModel, FakeGitHub, explore_step

LINK = "https://github.com/octo/shop/pull/7"


def understanding(**changes: Any) -> str:
    return json.dumps(
        {
            "goal": "Add a members' discount to the cart total.",
            "summary": "Adds discount() in src/cart.py and uses it in total().",
            "risky_parts": ["src/cart.py: every member total changes"],
            "goal_unclear": False,
            "goal_source": "description",
            **changes,
        }
    )


def found(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


BUG = {
    "file": "src/cart.py",
    "line": 3,
    "category": "bug",
    "level": 2,
    "severity": "high",
    "severity_reason": "Every member checkout with an empty cart fails.",
    "status": "confirmed",
    "explanation": "average() divides by len(items), which is 0 for an empty cart.",
    "trigger": "A member checks out with an empty cart.",
    "current_behaviour": "ZeroDivisionError, and the checkout page fails.",
    "expected_behaviour": "A total of 0.",
    "suggested_fix": "Return 0 when items is empty.",
}

CART = make_file(
    "src/cart.py",
    ["def total(items, member):", "    price = sum(items)", "    avg = price / len(items)"],
    status=FileStatus.ADDED,
)


class World:
    """The fake outside world for one review, plugged in where the factory builds parts."""

    def __init__(
        self,
        monkeypatch: pytest.MonkeyPatch,
        *replies: str,
        repository: dict[str, str] | None = None,
        **pull_request: Any,
    ):
        self.github = FakeGitHub.serving(
            make_pull_request(**{"files": (CART,), **pull_request}), repository=repository
        )
        self.model = FakeChatModel(list(replies))
        self.settings: list[Settings] = []

        def build(settings: Settings) -> Components:
            self.settings.append(settings)
            return Components(github=self.github, model=self.model, masker=Masker())

        monkeypatch.setattr("pr_review_agent.factory.build", build)


def test_a_pull_request_with_a_bug_gets_request_changes(monkeypatch: pytest.MonkeyPatch) -> None:
    world = World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG))
    result = review_pull_request(LINK, dry_run=True)

    assert result.understanding.goal.startswith("Add a members' discount")
    (finding,) = result.findings.kept
    assert (finding.file, finding.line, finding.severity) == ("src/cart.py", 3, "high")
    assert result.recommendation.value is RecommendationValue.REQUEST_CHANGES
    assert result.recommendation.reasons == ("1 High finding (`src/cart.py` line 3)",)
    # Each stage asked the model once, in order, and saw what it needed.
    understand_call, explore_call, find_call = world.model.calls
    assert "<<<BEGIN TITLE>>>\nAdd a discount" in understand_call.user
    assert "This is step 1 of at most 10." in explore_call.user
    assert "Goal: Add a members' discount" in find_call.user
    assert "     3 +     avg = price / len(items)" in find_call.user
    assert world.github.reads[0].number == 7


def test_a_clean_pull_request_is_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, found())
    result = review_pull_request(LINK, dry_run=True)
    assert result.recommendation.value is RecommendationValue.APPROVE
    assert result.findings.kept == ()


def test_a_lock_file_is_skipped_and_a_huge_file_is_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = make_file("src/generated_tables.py", [f"ROW_{n} = {n}" for n in range(4000)])
    files = (CART, make_file("uv.lock"), huge)
    World(monkeypatch, understanding(), EXPLORE_DONE, found(), files=files)
    result = review_pull_request(LINK, dry_run=True)
    assert [(s.path, s.reason) for s in result.scope.skipped] == [
        ("uv.lock", "lock file"),
        ("src/generated_tables.py", "too large"),
    ]
    assert result.recommendation.value is RecommendationValue.NEEDS_HUMAN_REVIEW
    assert result.recommendation.reasons == ("`src/generated_tables.py` was too large to review",)


def test_an_unclear_goal_needs_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch, understanding(goal_unclear=True), EXPLORE_DONE, found(), description="")
    result = review_pull_request(LINK, dry_run=True)
    assert result.understanding.goal_source == "code"
    assert result.recommendation.value is RecommendationValue.NEEDS_HUMAN_REVIEW


def test_a_model_that_fails_makes_the_review_incomplete(monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, "I can't answer in JSON.", "Still no JSON.")
    with pytest.raises(ReviewIncomplete, match="didn't match the required format"):
        review_pull_request(LINK, dry_run=True)


def test_the_command_line_prints_the_whole_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG))
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "**Recommendation: Request Changes** (rule 1 of the recommendation rules)" in out
    assert "**Goal** (from the description): Add a members' discount to the cart total." in out
    assert "| High | confirmed | bug, level 2 | `src/cart.py` line 3 |" in out
    assert "#### `src/cart.py` line 3" in out
    assert "- **What happens now:** ZeroDivisionError, and the checkout page fails." in out
    assert "- **Reviewed (1 file):** `src/cart.py`" in out
    assert "prompts: understand v1, explore v2, find_a v1" in out
    # Progress went to the error stream, so the review itself can be saved cleanly.
    assert "Reading pull request octo/shop#7..." in captured.err


def test_the_command_line_reports_an_incomplete_review(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, "no", "no")
    assert cli.main(["review", LINK, "--dry-run"]) == 1
    captured = capsys.readouterr()
    assert "The review is incomplete" in captured.err
    assert "Recommendation" not in captured.out


def test_exploring_finds_the_caller_and_the_finding_names_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A full explore with a scripted model: search, open the caller, done. The caller's
    code and the model's note reach the finding pass, and the summary lists what was read."""
    repository = {
        "src/cart.py": (
            "def total(items, member):\n    price = sum(items)\n    avg = price / len(items)\n"
        ),
        "src/checkout.py": (
            "from cart import total\n\ndef pay(items):\n    return total(items, True)\n"
        ),
    }
    steps = [
        explore_step("search", text="total(", reason="Find the callers."),
        explore_step(
            "open_file",
            path="src/checkout.py",
            reason="Read the caller.",
            notes="src/checkout.py:4 calls total() with every cart, empty ones included.",
        ),
        explore_step("done", reason="The caller is understood."),
    ]
    world = World(monkeypatch, understanding(), *steps, found(BUG), repository=repository)
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "- **Related files opened:** `src/checkout.py`" in out
    assert '- **Searches:** "total("' in out
    assert "- **Exploring stopped because** the model had seen enough." in out
    find_call = world.model.calls[-1]
    assert (
        "- src/checkout.py:4 calls total() with every cart, empty ones included." in find_call.user
    )
    assert "### src/checkout.py (from line 1)\n     1  from cart import total" in find_call.user
    # Every tool call is in the run log (FR-23).
    log = next((world.settings[0].state_dir / "runs").glob("*.jsonl"))
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    tools_used = [
        (e["action"], e["target"], e["result"]) for e in events if e["event"] == "tool call"
    ]
    assert tools_used == [
        ("search", "total(", "2 matches"),
        ("open_file", "src/checkout.py", "lines 1-4 of 4"),
        ("done", "", "done"),
    ]

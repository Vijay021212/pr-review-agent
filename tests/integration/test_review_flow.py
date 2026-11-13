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
from pr_review_agent.domain import FileStatus, RecommendationValue, SetupError
from pr_review_agent.factory import Components
from pr_review_agent.secrets import Masker
from tests.builders import make_file, make_pull_request
from tests.fakes import EXPLORE_DONE, KEEP_ONE, FakeChatModel, FakeGitHub, explore_step

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
    world = World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG), found(), KEEP_ONE)
    result = review_pull_request(LINK, dry_run=True)

    assert result.understanding.goal.startswith("Add a members' discount")
    (finding,) = result.findings.kept
    assert (finding.file, finding.line, finding.severity) == ("src/cart.py", 3, "high")
    assert result.recommendation.value is RecommendationValue.REQUEST_CHANGES
    assert result.recommendation.reasons == ("1 High finding (`src/cart.py` line 3)",)
    # Each stage asked the model once, in order, and saw what it needed.
    understand_call, explore_call, find_call, find_b_call, verify_call = world.model.calls
    assert "## Finding 1" in verify_call.user
    assert "<<<BEGIN TITLE>>>\nAdd a discount" in understand_call.user
    assert "This is step 1 of at most 10." in explore_call.user
    assert "Goal: Add a members' discount" in find_call.user
    assert "     3 +     avg = price / len(items)" in find_call.user
    assert "security and performance problems" in find_b_call.system
    assert find_b_call.user == find_call.user  # both passes review the same batch the same way
    assert world.github.reads[0].number == 7


def test_a_clean_pull_request_is_approved(monkeypatch: pytest.MonkeyPatch) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, found(), found())
    result = review_pull_request(LINK, dry_run=True)
    assert result.recommendation.value is RecommendationValue.APPROVE
    assert result.findings.kept == ()


def test_a_lock_file_is_skipped_and_a_huge_file_is_a_gap(monkeypatch: pytest.MonkeyPatch) -> None:
    huge = make_file("src/generated_tables.py", [f"ROW_{n} = {n}" for n in range(4000)])
    files = (CART, make_file("uv.lock"), huge)
    World(monkeypatch, understanding(), EXPLORE_DONE, found(), found(), files=files)
    result = review_pull_request(LINK, dry_run=True)
    assert [(s.path, s.reason) for s in result.scope.skipped] == [
        ("uv.lock", "lock file"),
        ("src/generated_tables.py", "too large"),
    ]
    assert result.recommendation.value is RecommendationValue.NEEDS_HUMAN_REVIEW
    assert result.recommendation.reasons == ("`src/generated_tables.py` was too large to review",)


def test_an_unclear_goal_needs_a_person(monkeypatch: pytest.MonkeyPatch) -> None:
    World(
        monkeypatch,
        understanding(goal_unclear=True),
        EXPLORE_DONE,
        found(),
        found(),
        description="",
    )
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
    World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG), found(), KEEP_ONE)
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    captured = capsys.readouterr()
    out = captured.out
    assert "**Recommendation: Request Changes** (rule 1 of the recommendation rules)" in out
    assert "**Goal** (from the description): Add a members' discount to the cart total." in out
    assert "| High | confirmed | bug, level 2 | `src/cart.py` line 3 |" in out
    assert "#### `src/cart.py` line 3" in out
    assert "- **What happens now:** ZeroDivisionError, and the checkout page fails." in out
    assert "- **Reviewed (1 file):** `src/cart.py`" in out
    assert "prompts: understand v1, explore v2, find_a v1, find_b v1" in out
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
    world = World(
        monkeypatch, understanding(), *steps, found(BUG), found(), KEEP_ONE, repository=repository
    )
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    out = capsys.readouterr().out
    assert "- **Related files opened:** `src/checkout.py`" in out
    assert '- **Searches:** "total("' in out
    assert "- **Exploring stopped because** the model had seen enough." in out
    find_call = world.model.calls[-2]
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


# --- Posting the review (v0.2.0) -------------------------------------------------------


def test_a_review_is_posted_when_not_a_dry_run(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "ghp_" + "t" * 36)
    world = World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG), found(), KEEP_ONE)
    assert cli.main(["review", LINK]) == 0
    (review,) = world.github.reviews
    assert [(c.path, c.line) for c in review.comments] == [("src/cart.py", 3)]
    (summary,) = world.github.summaries
    assert "**Recommendation: Request Changes**" in summary
    err = capsys.readouterr().err
    assert f"Findings posted: {LINK}#pullrequestreview-1" in err
    assert f"Summary posted: {LINK}#issuecomment-1" in err
    log = next((world.settings[0].state_dir / "runs").glob("*.jsonl"))
    events = [json.loads(line) for line in log.read_text(encoding="utf-8").splitlines()]
    posted = next(e for e in events if e["event"] == "review posted")
    assert posted["summary_url"] == f"{LINK}#issuecomment-1"


def test_the_public_function_returns_the_links_and_the_publish_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    World(monkeypatch, understanding(), EXPLORE_DONE, found(), found())
    result = review_pull_request(LINK, settings=Settings(github_token="ghp_" + "t" * 36))
    assert result.review_url is None  # nothing found, so no review, only the summary
    assert result.summary_url == f"{LINK}#issuecomment-1"
    assert result.timings[-1].stage == "publish"
    assert not result.dry_run


def test_a_new_commit_during_the_review_means_nothing_is_posted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "ghp_" + "t" * 36)
    world = World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG), found(), KEEP_ONE)
    world.github.moved_head = "a" * 40
    assert cli.main(["review", LINK]) == 1
    assert world.github.reviews == []
    assert world.github.summaries == []
    err = capsys.readouterr().err
    assert "got a new commit (aaaaaaa)" in err
    assert "Nothing was posted." in err


def test_a_failed_summary_after_the_findings_is_reported_as_such(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "ghp_" + "t" * 36)
    world = World(monkeypatch, understanding(), EXPLORE_DONE, found(BUG), found(), KEEP_ONE)
    world.github.summary_error = ReviewIncomplete("GitHub answered 502")
    assert cli.main(["review", LINK]) == 1
    assert len(world.github.reviews) == 1
    err = capsys.readouterr().err
    assert "the findings were posted" in err
    assert "Nothing was posted." not in err


def test_with_nothing_found_only_the_summary_link_is_printed(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "ghp_" + "t" * 36)
    World(monkeypatch, understanding(), EXPLORE_DONE, found(), found())
    assert cli.main(["review", LINK]) == 0
    err = capsys.readouterr().err
    assert "Summary posted:" in err
    assert "Findings posted" not in err


# --- Preflight and the lock (v0.2.0) ---------------------------------------------------


def test_a_setup_problem_is_found_before_any_review_work(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = World(monkeypatch)
    world.model.ready_error = SetupError("Ollama isn't running at x.", "Start the Ollama app.")
    assert cli.main(["review", LINK, "--dry-run"]) == 2
    assert world.github.reads == []  # the pull request wasn't even read
    assert "Setup problem: Ollama isn't running at x." in capsys.readouterr().err


def test_a_second_review_of_the_same_pull_request_is_refused(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    world = World(monkeypatch)
    locks = Settings().state_dir / "locks"
    locks.mkdir(parents=True)
    (locks / "octo-shop-7.lock").write_text("another run", encoding="utf-8")
    assert cli.main(["review", LINK, "--dry-run"]) == 2
    assert world.model.calls == []
    assert "is already running on this computer" in capsys.readouterr().err

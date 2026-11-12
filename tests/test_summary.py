"""Tests for the summary and comment text (FR-14): every part is there, and masked."""

from __future__ import annotations

import pytest

from pr_review_agent.domain import (
    Category,
    CheckedFindings,
    DroppedFinding,
    Excerpt,
    ExploredContext,
    ExploreStop,
    GoalSource,
    Recommendation,
    RecommendationValue,
    ReviewScope,
    Severity,
    SkippedFile,
    SkipReason,
    ToolAction,
    ToolCall,
)
from pr_review_agent.secrets import Masker
from pr_review_agent.summary import SUMMARY_MARKER, format_seconds, render_comment, render_summary
from tests.builders import (
    SHA,
    make_file,
    make_finding,
    make_pull_request,
    make_result,
    make_understanding,
)

HIGH = make_finding(file="src/claims.py", line=42, severity=Severity.HIGH)
LOW = make_finding(
    file="src/cart.py",
    line=7,
    severity=Severity.LOW,
    category=Category.CODE_QUALITY,
    trigger=None,
    current_behaviour=None,
    expected_behaviour=None,
    explanation="The message says 'eror'.",
    severity_reason="Only a message.",
)
REQUEST_CHANGES = Recommendation(
    value=RecommendationValue.REQUEST_CHANGES,
    rule=1,
    reasons=("1 High finding (`src/claims.py` line 42)",),
)


def summary(**overrides: object) -> str:
    return render_summary(make_result(**overrides), Masker())


def test_the_summary_starts_with_its_hidden_marker_and_the_recommendation() -> None:
    text = summary(recommendation=REQUEST_CHANGES, findings=CheckedFindings(kept=(HIGH,)))
    assert text.startswith(SUMMARY_MARKER + "\n\n## Review by pr-review-agent")
    assert "**Recommendation: Request Changes** (rule 1 of the recommendation rules)" in text
    assert "- 1 High finding (`src/claims.py` line 42)" in text
    assert "informational only: a person makes the final decision" in text


def test_the_summary_names_the_reviewed_commit_and_marks_drafts() -> None:
    assert f"**Reviewed commit:** `{SHA[:7]}`" in summary()
    assert "draft" not in summary()
    draft = summary(pull_request=make_pull_request(draft=True))
    assert "**This is a draft pull request.**" in draft


def test_the_summary_opens_with_what_the_agent_understood() -> None:
    text = summary(understanding=make_understanding(risky_parts=("cart.py: totals change",)))
    assert "**Goal** (from the description): Give members a 10% discount." in text
    assert "**What changed:** Adds discount() and uses it in the cart total." in text
    assert "**Risky parts:**\n- cart.py: totals change" in text
    assert "The goal is unclear" not in text


def test_a_goal_from_the_code_and_an_unclear_goal_are_said() -> None:
    understanding = make_understanding(goal_source=GoalSource.CODE, goal_unclear=True)
    text = summary(understanding=understanding)
    assert "worked out from the code, because the description doesn't state it" in text
    assert "**The goal is unclear,**" in text
    assert "Risky parts" not in text


def test_findings_are_listed_most_severe_first() -> None:
    text = summary(findings=CheckedFindings(kept=(LOW, HIGH)))
    assert "### Findings (2, shown as comments on their lines)" in text
    high_row = text.index("| High | confirmed | bug, level 2 | `src/claims.py` line 42 |")
    low_row = text.index("| Low | confirmed | code quality, level 2 | `src/cart.py` line 7 |")
    assert high_row < low_row


def test_no_findings_is_said_plainly() -> None:
    assert "No problems were found in the checks performed." in summary()


def test_the_terminal_version_holds_each_finding_in_full() -> None:
    result = make_result(findings=CheckedFindings(kept=(HIGH,)))
    text = render_summary(result, Masker(), with_details=True)
    assert "### Findings (1, shown below)" in text
    assert "#### `src/claims.py` line 42" in text
    assert "- **Triggered by:** A checkout with an empty cart." in text


def test_long_or_awkward_explanations_fit_one_table_cell() -> None:
    finding = make_finding(explanation="a | b\nc " + "x" * 300)
    text = summary(findings=CheckedFindings(kept=(finding,)))
    row = next(line for line in text.splitlines() if line.startswith("| High"))
    assert "a \\| b c x" in row
    assert row.endswith("… |")
    assert len(row) < 260


def test_reviewed_and_skipped_files_are_listed_with_reasons() -> None:
    reviewed = make_file("src/cart.py")
    scope = ReviewScope(
        batches=((reviewed,),),
        skipped=(
            SkippedFile(path="uv.lock", reason=SkipReason.LOCK_FILE),
            SkippedFile(path="big.py", reason=SkipReason.TOO_LARGE),
        ),
    )
    text = summary(scope=scope)
    assert "- **Reviewed (1 file):** `src/cart.py`" in text
    assert "- **Skipped (2 files):** `uv.lock` (lock file), `big.py` (too large)" in text


def test_nothing_reviewed_is_said() -> None:
    text = summary(scope=ReviewScope())
    assert "- **Reviewed (0 files):** none" in text
    assert "Skipped" not in text


def test_without_exploring_the_summary_says_so() -> None:
    assert "- **Related code:** not explored." in summary()


@pytest.mark.parametrize(
    ("stop", "words"),
    [
        (ExploreStop.DONE, "the model had seen enough"),
        (ExploreStop.STEP_LIMIT, "the step limit was reached"),
        (ExploreStop.TIME_LIMIT, "its share of the time limit ran out"),
        (ExploreStop.REPEATING, "the model kept repeating steps it had already taken"),
    ],
)
def test_exploring_lists_files_opened_and_searches_and_why_it_stopped(
    stop: ExploreStop, words: str
) -> None:
    explored = ExploredContext(
        tool_calls=(
            ToolCall(action=ToolAction.SEARCH, target="average("),
            ToolCall(action=ToolAction.OPEN_FILE, target="src/report.py"),
            ToolCall(action=ToolAction.OPEN_FILE, target="src/report.py"),
            ToolCall(action=ToolAction.LIST_DIR, target="src"),
            ToolCall(action=ToolAction.DONE),
        ),
        excerpts=(Excerpt(path="src/report.py", start_line=1, text="..."),),
        stopped_by=stop,
    )
    text = summary(explored=explored)
    assert "- **Related files opened:** `src/report.py`\n" in text
    assert '- **Searches:** "average("' in text
    assert f"- **Exploring stopped because** {words}." in text


def test_exploring_that_opened_nothing_says_none() -> None:
    text = summary(explored=ExploredContext(stopped_by=ExploreStop.DONE))
    assert "- **Related files opened:** none" in text
    assert "- **Searches:** none" in text


def test_dropped_and_repeated_findings_are_explained() -> None:
    findings = CheckedFindings(
        dropped=(DroppedFinding(finding=HIGH, reason="The caller already checks for None."),),
        repeated=(LOW,),
    )
    text = summary(findings=findings)
    assert "### Findings dropped (1)" in text
    assert "- `src/claims.py` line 42: The caller already checks for None." in text
    assert "**Not repeated:** 1 finding was already posted" in text


def test_the_footer_names_the_model_provider_prompts_and_time() -> None:
    text = summary()
    assert "`qwen2.5-coder:7b` (ollama)" in text
    assert "prompts: understand v1, find_a v1" in text
    assert "time: 45 s" in text
    assert summary(prompt_versions=()).count("prompts: none") == 1


def test_a_bug_comment_holds_every_part_of_the_finding() -> None:
    finding = HIGH.model_copy(
        update={"affected_places": ("src/report.py:12",), "suggested_fix": "Return 0 if empty."}
    )
    text = render_comment(finding, Masker())
    assert text.startswith("**High · bug · level 2 · confirmed**\n\n")
    assert "The average divides by the number of items, which can be zero." in text
    assert "- **Triggered by:** A checkout with an empty cart." in text
    assert "- **What happens now:** ZeroDivisionError." in text
    assert "- **What should happen:** An average of 0." in text
    assert "- **Also affects:** `src/report.py:12`" in text
    assert "- **Why High:** Every checkout with an empty cart fails." in text
    assert text.endswith("**Suggested fix:**\n\nReturn 0 if empty.")


def test_a_maintainability_comment_leaves_out_the_empty_parts() -> None:
    text = render_comment(LOW, Masker())
    assert "Triggered by" not in text
    assert "Also affects" not in text
    assert "Suggested fix" not in text
    assert "- **Why Low:** Only a message." in text


def test_secrets_quoted_by_the_model_are_masked_everywhere() -> None:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    leaky = make_finding(explanation=f"The key {key} is committed.", trigger=f"Reading {key}.")
    masker = Masker(["ghp_my_own_configured_token_123"])
    result = make_result(
        findings=CheckedFindings(kept=(leaky,)),
        understanding=make_understanding(summary="Uses ghp_my_own_configured_token_123 to log in."),
    )
    for text in (
        render_summary(result, masker, with_details=True),
        render_comment(leaky, masker),
    ):
        assert key not in text
        assert "ghp_my_own_configured_token_123" not in text
        assert "AKIA...MPLE" in text


@pytest.mark.parametrize(("seconds", "text"), [(0, "0 s"), (45.4, "45 s"), (200, "3 min 20 s")])
def test_times_are_written_in_minutes_and_seconds(seconds: float, text: str) -> None:
    assert format_seconds(seconds) == text

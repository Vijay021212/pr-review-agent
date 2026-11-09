"""Tests for the recommendation rules (FR-12, requirements 5.3), one by one and in order."""

from __future__ import annotations

from typing import Any

import pytest

from pr_review_agent.domain import (
    Category,
    CheckedFindings,
    DroppedFinding,
    Finding,
    FindingStatus,
    GoalSource,
    Recommendation,
    RecommendationValue,
    ReviewScope,
    Severity,
    SkippedFile,
    SkipReason,
    Understanding,
)
from pr_review_agent.recommendation import recommend
from pr_review_agent.sensitive import SensitiveMatch
from tests.builders import make_finding

CLEAR = Understanding(
    goal="Fix rounding.", summary="Rounds totals.", goal_unclear=False, goal_source=GoalSource.CODE
)
UNCLEAR = CLEAR.model_copy(update={"goal_unclear": True})
AUTH = (SensitiveMatch(path="src/auth/session.py", area="authentication"),)
GAP = ReviewScope(skipped=(SkippedFile(path="big_module.py", reason=SkipReason.TOO_LARGE),))


def finding(severity: Severity, status: str = "confirmed", **changes: Any) -> Finding:
    values: dict[str, Any] = {"severity": severity, "status": FindingStatus(status), **changes}
    return make_finding(**values)


def decide(
    *kept: Finding,
    scope: ReviewScope | None = None,
    understanding: Understanding = CLEAR,
    sensitive: tuple[SensitiveMatch, ...] = (),
    dropped: tuple[DroppedFinding, ...] = (),
) -> Recommendation:
    return recommend(
        CheckedFindings(kept=kept, dropped=dropped),
        scope or ReviewScope(),
        understanding,
        sensitive,
    )


# --- Rule 1 ------------------------------------------------------------------------------


@pytest.mark.parametrize("severity", [Severity.CRITICAL, Severity.HIGH])
def test_rule_1_a_confirmed_critical_or_high_finding_requests_changes(severity: Severity) -> None:
    result = decide(finding(severity, file="claims.py", line=42))
    assert (result.value, result.rule) == (RecommendationValue.REQUEST_CHANGES, 1)
    assert result.reasons == (f"1 {severity.value.title()} finding (`claims.py` line 42)",)


def test_rule_1_counts_each_severity() -> None:
    result = decide(
        finding(Severity.HIGH, file="a.py", line=1),
        finding(Severity.CRITICAL, file="b.py", line=2),
        finding(Severity.HIGH, file="c.py", line=3),
    )
    assert result.reasons == (
        "1 Critical finding (`b.py` line 2)",
        "2 High findings (`a.py` line 1, `c.py` line 3)",
    )


def test_rule_1_also_reports_what_rule_2_found() -> None:
    result = decide(finding(Severity.HIGH, file="a.py", line=1), scope=GAP, sensitive=AUTH)
    assert result.rule == 1
    assert result.reasons == (
        "1 High finding (`a.py` line 1)",
        "`big_module.py` was too large to review",
        "changes `src/auth/session.py` (sensitive area: authentication)",
    )


def test_rule_1_counts_maintainability_findings_too() -> None:
    quality = finding(Severity.HIGH, category=Category.CODE_QUALITY)
    assert decide(quality).value is RecommendationValue.REQUEST_CHANGES


def test_dropped_findings_dont_count() -> None:
    dropped = (DroppedFinding(finding=finding(Severity.CRITICAL), reason="Not real."),)
    assert decide(dropped=dropped).value is RecommendationValue.APPROVE


# --- Rule 2 ------------------------------------------------------------------------------


def test_rule_2a_files_that_couldnt_be_reviewed() -> None:
    result = decide(scope=GAP)
    assert (result.value, result.rule) == (RecommendationValue.NEEDS_HUMAN_REVIEW, 2)
    assert result.reasons == ("`big_module.py` was too large to review",)


def test_rule_2a_names_three_files_then_counts_the_rest() -> None:
    skipped = tuple(SkippedFile(path=f"f{n}.py", reason=SkipReason.TOO_LARGE) for n in range(5))
    result = decide(scope=ReviewScope(skipped=skipped))
    assert result.reasons == ("`f0.py`, `f1.py`, `f2.py` and 2 more were too large to review",)


def test_skips_that_arent_gaps_dont_need_a_person() -> None:
    skipped = (
        SkippedFile(path="uv.lock", reason=SkipReason.LOCK_FILE),
        SkippedFile(path="logo.png", reason=SkipReason.BINARY),
        SkippedFile(path="dist/a.js", reason=SkipReason.GENERATED),
        SkippedFile(path="fixtures/x", reason=SkipReason.IGNORED_BY_RULES),
    )
    assert decide(scope=ReviewScope(skipped=skipped)).value is RecommendationValue.APPROVE


def test_rule_2b_an_unclear_goal() -> None:
    result = decide(understanding=UNCLEAR)
    assert result.rule == 2
    assert result.reasons == ("the goal of the pull request is unclear",)


def test_rule_2c_a_sensitive_area() -> None:
    result = decide(sensitive=AUTH)
    assert result.rule == 2
    assert result.reasons == ("changes `src/auth/session.py` (sensitive area: authentication)",)


@pytest.mark.parametrize("severity", [Severity.CRITICAL, Severity.HIGH])
def test_rule_2d_a_serious_potential_risk(severity: Severity) -> None:
    result = decide(finding(severity, "potential risk", file="x.py", line=4))
    assert result.rule == 2
    assert result.reasons == (
        f"1 {severity.value.title()} potential risk that couldn't be confirmed (`x.py` line 4)",
    )


@pytest.mark.parametrize("category", [Category.BUG, Category.SECURITY, Category.PERFORMANCE])
def test_rule_2e_a_confirmed_medium_bug_type_finding(category: Category) -> None:
    result = decide(finding(Severity.MEDIUM, category=category, file="x.py", line=4))
    assert result.rule == 2
    assert result.reasons == ("1 Medium bug-type finding (`x.py` line 4)",)


def test_rule_2_lists_every_reason_that_applies_in_order() -> None:
    result = decide(
        finding(Severity.HIGH, "potential risk", file="a.py", line=1),
        finding(Severity.MEDIUM, file="b.py", line=2),
        finding(Severity.MEDIUM, file="c.py", line=3),
        scope=GAP,
        understanding=UNCLEAR,
        sensitive=AUTH,
    )
    assert result.reasons == (
        "`big_module.py` was too large to review",
        "the goal of the pull request is unclear",
        "changes `src/auth/session.py` (sensitive area: authentication)",
        "1 High potential risk that couldn't be confirmed (`a.py` line 1)",
        "2 Medium bug-type findings (`b.py` line 2, `c.py` line 3)",
    )


# --- Rule 3 ------------------------------------------------------------------------------


def test_rule_3_nothing_found_approves() -> None:
    result = decide()
    assert (result.value, result.rule) == (RecommendationValue.APPROVE, 3)
    assert result.reasons == (
        "Every changed file was reviewed, the goal is clear, and nothing was found",
    )


@pytest.mark.parametrize(
    "kept",
    [
        finding(Severity.LOW),
        finding(Severity.LOW, "potential risk"),
        finding(Severity.MEDIUM, "potential risk"),
        finding(Severity.MEDIUM, category=Category.DESIGN_FIT),
        finding(Severity.MEDIUM, category=Category.CODE_QUALITY),
    ],
)
def test_rule_3_low_findings_and_medium_maintainability_still_approve(kept: Finding) -> None:
    result = decide(kept)
    assert result.value is RecommendationValue.APPROVE
    assert result.reasons == (
        "Every changed file was reviewed, the goal is clear, and only Low findings or "
        "maintainability suggestions remain",
    )


def test_the_same_findings_always_give_the_same_recommendation() -> None:
    kept = (finding(Severity.MEDIUM, file="b.py", line=2), finding(Severity.LOW))
    assert decide(*kept) == decide(*kept)

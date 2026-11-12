"""Stage 7, recommend: Approve, Request Changes or Needs Human Review (FR-12, 5.3).

Worked out by fixed rules in plain code, never by the model, so the same findings always
give the same recommendation. The rules are checked in order and the first match wins:

1. Request Changes: at least one confirmed Critical or High finding.
2. Needs Human Review: (a) some changed files couldn't be reviewed; (b) the goal is unclear;
   (c) a sensitive area changed; (d) a Critical or High potential risk couldn't be
   confirmed; (e) a confirmed Medium bug-type finding.
3. Approve: none of the above.

The recommendation is informational only: the agent never approves or blocks a pull
request on GitHub, and a person always makes the final decision (FR-12).
"""

from __future__ import annotations

from collections.abc import Iterable

from pr_review_agent.domain import (
    CheckedFindings,
    Finding,
    FindingStatus,
    Recommendation,
    RecommendationValue,
    ReviewScope,
    Severity,
    Understanding,
)
from pr_review_agent.sensitive import SensitiveMatch

SERIOUS = (Severity.CRITICAL, Severity.HIGH)
LISTED = 3  # how many files or places a reason names before saying "and N more"


def recommend(
    findings: CheckedFindings,
    scope: ReviewScope,
    understanding: Understanding,
    sensitive: tuple[SensitiveMatch, ...],
) -> Recommendation:
    kept = findings.kept
    confirmed_serious = [
        f for f in kept if f.status is FindingStatus.CONFIRMED and f.severity in SERIOUS
    ]
    needs_a_person = _rule_2_reasons(kept, scope, understanding, sensitive)
    if confirmed_serious:
        # Rule 1 decides, but anything rule 2 found is reported as well (requirements 5.3).
        return Recommendation(
            value=RecommendationValue.REQUEST_CHANGES,
            rule=1,
            reasons=(*_count_by_severity(confirmed_serious, "finding"), *needs_a_person),
        )
    if needs_a_person:
        return Recommendation(
            value=RecommendationValue.NEEDS_HUMAN_REVIEW, rule=2, reasons=tuple(needs_a_person)
        )
    remaining = (
        "only Low findings or maintainability suggestions remain" if kept else "nothing was found"
    )
    return Recommendation(
        value=RecommendationValue.APPROVE,
        rule=3,
        reasons=(f"Every changed file was reviewed, the goal is clear, and {remaining}",),
    )


def _rule_2_reasons(
    kept: tuple[Finding, ...],
    scope: ReviewScope,
    understanding: Understanding,
    sensitive: tuple[SensitiveMatch, ...],
) -> list[str]:
    reasons = []
    gaps = [skip for skip in scope.skipped if skip.reason.is_gap]
    if gaps:  # (a)
        names = _listed(f"`{skip.path}`" for skip in gaps)
        reasons.append(f"{names} {'was' if len(gaps) == 1 else 'were'} too large to review")
    if understanding.goal_unclear:  # (b)
        reasons.append("the goal of the pull request is unclear")
    if sensitive:  # (c)
        places = _listed(f"`{match.path}` (sensitive area: {match.area})" for match in sensitive)
        reasons.append(f"changes {places}")
    unconfirmed = [
        f for f in kept if f.status is FindingStatus.POTENTIAL_RISK and f.severity in SERIOUS
    ]
    if unconfirmed:  # (d)
        reasons += _count_by_severity(unconfirmed, "potential risk", "couldn't be confirmed")
    medium_bugs = [
        f
        for f in kept
        if f.status is FindingStatus.CONFIRMED
        and f.severity is Severity.MEDIUM
        and f.category.is_bug_type
    ]
    if medium_bugs:  # (e)
        reasons += _count_by_severity(medium_bugs, "bug-type finding")
    return reasons


def _count_by_severity(findings: list[Finding], noun: str, note: str = "") -> list[str]:
    """For example "2 High findings (`a.py` line 3, `b.py` line 9)", one line per severity."""
    lines = []
    for severity in Severity:
        group = [finding for finding in findings if finding.severity is severity]
        if not group:
            continue
        plural = noun if len(group) == 1 else noun + "s"
        places = _listed(f"`{finding.file}` line {finding.line}" for finding in group)
        suffix = f" that {note}" if note else ""
        lines.append(f"{len(group)} {severity.value.title()} {plural}{suffix} ({places})")
    return lines


def _listed(items: Iterable[str]) -> str:
    """Up to three items joined, then "and N more"."""
    names = list(items)
    if len(names) <= LISTED:
        return ", ".join(names)
    return ", ".join(names[:LISTED]) + f" and {len(names) - LISTED} more"

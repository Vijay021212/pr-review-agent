"""Plain-code rules of the double-check (FR-10): anchoring, findings that describe no
failure, duplicates and style noise.

GitHub accepts review comments only on lines a pull request changes. So every kept
finding sits on a changed line; when the harm shows up elsewhere, the comment sits on the
changed line that causes it and names the other place (FR-10). These rules always behave
the same, which is where trust in the output comes from (design section 8).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from pr_review_agent.domain import (
    ChangedFile,
    DroppedFinding,
    Finding,
    Severity,
    Side,
)

ANCHOR_DISTANCE = 5  # how far a finding may be moved to reach a changed line
DUPLICATE_DISTANCE = 3  # findings this close, in the same file and of the same kind, are one
# Words that only change the tense or mood of a sentence, so "A receipt is sent" and "A
# receipt should be sent" say the same. Negations ("not", "no", "never") are never here.
_FILLER = frozenset(
    {
        # articles and joining words
        *("a", "an", "the", "and", "but", "then", "still", "also", "instead"),
        # forms of "be", and words of tense or mood
        *("is", "are", "was", "were", "be", "been", "being"),
        *("will", "would", "should", "shall", "can", "could", "must"),
    }
)
_WORD = re.compile(r"[a-z0-9_]+")


@dataclass(frozen=True)
class Sorted:
    """Findings split into those kept and those dropped, each dropped one with its reason."""

    kept: tuple[Finding, ...]
    dropped: tuple[DroppedFinding, ...]


def anchor_all(findings: tuple[Finding, ...], files: tuple[ChangedFile, ...]) -> Sorted:
    by_path = {file.path: file for file in files}
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in findings:
        result = anchor(finding, by_path.get(finding.file))
        if isinstance(result, Finding):
            kept.append(result)
        else:
            dropped.append(DroppedFinding(finding=finding, reason=result))
    return Sorted(tuple(kept), tuple(dropped))


def anchor(finding: Finding, file: ChangedFile | None) -> Finding | str:
    """The finding on a changed line, or the reason it can't be placed on one."""
    if file is None:
        return f"`{finding.file}` isn't changed by this pull request, so it can't be commented on"
    changed = file.added_lines if finding.side is Side.NEW else file.removed_lines
    if finding.line in changed:
        return finding
    nearby = [line for line in changed if abs(line - finding.line) <= ANCHOR_DISTANCE]
    if not nearby:
        return (
            f"line {finding.line} of `{finding.file}` isn't within {ANCHOR_DISTANCE} lines of "
            "a changed line"
        )
    # The closest changed line; between two equally close, the earlier one.
    target = min(nearby, key=lambda line: (abs(line - finding.line), line))
    original = f"{finding.file}:{finding.line}"
    places = finding.affected_places
    if original not in places:
        places = (*places, original)
    return finding.model_copy(update={"line": target, "affected_places": places})


def remove_duplicates(findings: tuple[Finding, ...]) -> Sorted:
    """One finding per problem: the most severe of those that look like the same one."""
    ordered = sorted(findings, key=lambda f: (f.severity.rank, f.file, f.line))
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in ordered:
        same = next((other for other in kept if _same_problem(finding, other)), None)
        if same is None:
            kept.append(finding)
        else:
            dropped.append(
                DroppedFinding(
                    finding=finding,
                    reason=f"the same problem as the finding on line {same.line}",
                )
            )
    return Sorted(tuple(sorted(kept, key=lambda f: (f.file, f.line))), tuple(dropped))


def drop_no_failure(findings: tuple[Finding, ...]) -> Sorted:
    """Bug-type findings whose "what happens now" is what should happen are dropped.

    A bug-type finding is a failure: what happens now, and what should happen instead
    (FR-9). When both say the same, apart from tense and mood, the finding describes no
    failure, as in "Returns the correct number of pages" twice. It runs before the model's
    re-check, so these findings cost no model call.
    """
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in findings:
        if finding.category.is_bug_type and _says_the_same(
            finding.current_behaviour, finding.expected_behaviour
        ):
            reason = "it describes no failure: what happens now is what should happen"
            dropped.append(DroppedFinding(finding=finding, reason=reason))
        else:
            kept.append(finding)
    return Sorted(tuple(kept), tuple(dropped))


def _says_the_same(now: str | None, expected: str | None) -> bool:
    words_now, words_expected = _meaning(now or ""), _meaning(expected or "")
    return bool(words_now) and words_now == words_expected


def _meaning(text: str) -> list[str]:
    """The words of a sentence without case, punctuation, and words of tense or mood."""
    return [word for word in _WORD.findall(text.lower()) if word not in _FILLER]


def drop_style_noise(findings: tuple[Finding, ...]) -> Sorted:
    """Low-severity maintainability suggestions are dropped (FR-10, requirements 5.2)."""
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = []
    for finding in findings:
        if finding.severity is Severity.LOW and not finding.category.is_bug_type:
            reason = "a Low-severity style or maintainability suggestion"
            dropped.append(DroppedFinding(finding=finding, reason=reason))
        else:
            kept.append(finding)
    return Sorted(tuple(kept), tuple(dropped))


def _same_problem(a: Finding, b: Finding) -> bool:
    same_kind = a.category is b.category or (a.category.is_bug_type and b.category.is_bug_type)
    return (
        a.file == b.file
        and a.side is b.side
        and abs(a.line - b.line) <= DUPLICATE_DISTANCE
        and same_kind
    )

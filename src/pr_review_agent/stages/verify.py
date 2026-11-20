"""Stage 6, double-check: re-check every finding before it's posted (FR-10, FR-21).

    anchor on a changed line -> drop findings that describe no failure
        -> re-check with the model -> remove duplicates -> drop style noise
        -> replace severity reasons that only repeat the definitions

The model gets the same context the finding passes had (what the pull request is for, its
title and description, the notes from exploring, the related code; #84), then each finding
with the code around its line at the reviewed commit, the change near it, and the code at
the other places the finding says it affects. It decides keep or drop with a reason; it may
make a finding more serious or confirm it, never weaker. Every dropped finding keeps its
reason for the summary. The other four steps are plain code (`anchoring.py`); the two
before the model cost nothing, so they save model calls.
"""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pr_review_agent.anchoring import (
    anchor_all,
    drop_no_failure,
    drop_style_noise,
    remove_duplicates,
)
from pr_review_agent.comments import hidden_by_file, hide
from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import (
    ChangedFile,
    ChatModel,
    CheckedFindings,
    Deadline,
    DroppedFinding,
    ExploredContext,
    Finding,
    FindingStatus,
    NonEmptyStr,
    PullRequest,
    Severity,
    Side,
    Understanding,
)
from pr_review_agent.prompts import data_block, load, severity_definitions
from pr_review_agent.stages import ModelCall, ask, review_context
from pr_review_agent.tools import ReadOnlyTools, ToolRefused, normalize_path

FINDINGS_PER_CALL = 4
CODE_LINES_AROUND = 15  # lines of the reviewed code shown before and after a finding's line
DIFF_LINES_AROUND = 12  # lines of the diff shown around it
PLACES_PER_FINDING = 2  # other places a finding names whose code is shown
PLACE_LINES_AROUND = 6  # lines shown before and after each of them
SERIOUS = (Severity.CRITICAL, Severity.HIGH)
NOTE_START = " (The double-check "  # how its notes on a severity reason begin
OWN_WORDS = 4  # a reason that repeats a definition needs this many words of its own besides
NO_REASON = "No reason of its own was given (the model repeated the severity definitions)."


class Decision(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    number: int = Field(ge=1)
    keep: bool
    reason: NonEmptyStr
    severity: Severity
    status: FindingStatus


class VerifyAnswer(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    decisions: tuple[Decision, ...]


def verify(
    findings: tuple[Finding, ...],
    dropped_before: tuple[DroppedFinding, ...],
    pull_request: PullRequest,
    understanding: Understanding,
    explored: ExploredContext,
    root: Path,
    model: ChatModel,
    deadline: Deadline,
) -> tuple[CheckedFindings, tuple[ModelCall, ...]]:
    """`dropped_before` holds findings an earlier stage already dropped, with their reasons."""
    anchored = anchor_all(findings, pull_request.files)
    failures = drop_no_failure(anchored.kept)
    context = review_context(pull_request, understanding, explored)
    hidden = hidden_by_file(understanding.left_out_lines)
    by_path = {file.path: file for file in pull_request.files}
    prompt = load("verify")
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = [*dropped_before, *anchored.dropped, *failures.dropped]
    calls: list[ModelCall] = []
    for start in range(0, len(failures.kept), FINDINGS_PER_CALL):
        group = failures.kept[start : start + FINDINGS_PER_CALL]
        deadline.check("the double-check")
        message = build_message(group, context, by_path, root, hidden)
        answer, call = ask(model, prompt, message, VerifyAnswer, deadline)
        calls.append(call)
        decisions = {decision.number: decision for decision in answer.decisions}
        for number, finding in enumerate(group, start=1):
            decision = decisions.get(number)
            if decision is None:
                kept.append(finding)  # no answer for it: kept as found, never silently lost
            elif decision.keep:
                kept.append(_corrected(finding, decision))
            elif finding.severity in SERIOUS:
                kept.append(_disputed(finding, decision))
            else:
                reason = f"the double-check found it doesn't hold: {decision.reason}"
                dropped.append(DroppedFinding(finding=finding, reason=reason))
    unique = remove_duplicates(tuple(kept))
    useful = drop_style_noise(unique.kept)
    checked = CheckedFindings(
        kept=tuple(_own_reason(finding) for finding in useful.kept),
        dropped=(*dropped, *unique.dropped, *useful.dropped),
    )
    return checked, tuple(calls)


def build_message(
    group: tuple[Finding, ...],
    context: list[str],
    files: dict[str, ChangedFile],
    root: Path,
    hidden: Mapping[str, Collection[int]] | None = None,
) -> str:
    """The context the finding passes had, then each finding with the code it's about.

    `files` are the pull request's files as the later stages review them, with the comments
    the screen left out hidden in their diffs, and `hidden` says which lines they are, so
    the code read from the snapshot hides them too (#109).
    """
    hidden = hidden or {}
    parts = [*context, "## The findings to check"]
    for number, finding in enumerate(group, start=1):
        text = "\n".join(
            [
                f"File: {finding.file}, line {finding.line} ({finding.side} code)",
                f"Category: {finding.category}, level {int(finding.level)}",
                f"Severity: {finding.severity} ({finding.severity_reason})",
                f"Status: {finding.status}",
                f"Explanation: {finding.explanation}",
                *(
                    f"{label}: {value}"
                    for label, value in (
                        ("Trigger", finding.trigger),
                        ("What happens now", finding.current_behaviour),
                        ("What should happen", finding.expected_behaviour),
                    )
                    if value
                ),
            ]
        )
        parts += [f"## Finding {number}", data_block(f"FINDING {number}", text)]
        code = _code_around(root, finding, hidden)
        if code:
            parts += [
                f"Code around line {finding.line} at the reviewed commit:",
                data_block(f"CODE {number}", code),
            ]
        file = files.get(finding.file)
        if file is not None:
            parts += [
                "The change near it:",
                data_block(f"DIFF {number}", _diff_around(file, finding)),
            ]
        for place, code in _places(root, finding, hidden):
            parts += [
                f"Code at {place}, which the finding says it affects:",
                data_block(f"PLACE {number}", code),
            ]
    parts.append(f"Give a decision for each of the {len(group)} findings above.")
    return "\n\n".join(parts)


def _places(
    root: Path, finding: Finding, hidden: Mapping[str, Collection[int]]
) -> list[tuple[str, str]]:
    """The code around the other places a finding names ("path:line"), for the first few.

    The places are the model's words, so they're read through the read-only tools: a place
    outside the repository, through a link, or that isn't a text file is refused and left
    out. A place already inside the code shown around the finding's line is left out too.
    """
    tools = ReadOnlyTools(root, hidden)
    shown = []
    for place in finding.affected_places[:PLACES_PER_FINDING]:
        path, _, number = place.rpartition(":")
        if not (path and number.isdigit()):
            path, number = place, "1"
        line = int(number)
        same_file = normalize_path(path) == normalize_path(finding.file)
        if same_file and abs(line - finding.line) <= CODE_LINES_AROUND:
            continue
        try:
            opened = tools.open_file(path, max(1, line - PLACE_LINES_AROUND))
        except ToolRefused:
            continue
        lines = opened.output.splitlines()[: 2 * PLACE_LINES_AROUND + 1]
        shown.append((place, "\n".join(lines)))
    return shown


def _code_around(root: Path, finding: Finding, hidden: Mapping[str, Collection[int]]) -> str:
    """Numbered lines of the reviewed file around the finding, or "" for removed code."""
    path = root / finding.file
    if finding.side is Side.OLD or not path.is_file():
        return ""
    text = path.read_bytes().decode("utf-8", errors="replace")
    lines = hide(finding.file, text.splitlines(), hidden.get(finding.file, ()))
    first = max(1, finding.line - CODE_LINES_AROUND)
    last = min(len(lines), finding.line + CODE_LINES_AROUND)
    return "\n".join(f"{number:>6}  {lines[number - 1]}" for number in range(first, last + 1))


def _diff_around(file: ChangedFile, finding: Finding) -> str:
    """The diff lines near the finding, in the format the finding passes saw."""

    def near(number: int | None) -> bool:
        return number is not None and abs(number - finding.line) <= DIFF_LINES_AROUND

    lines = tuple(
        line
        for line in file.lines
        if near(line.new_number if finding.side is Side.NEW else line.old_number)
    )
    return render_file(file.model_copy(update={"lines": lines}))


def _disputed(finding: Finding, decision: Decision) -> Finding:
    """A serious finding the double-check rejects is kept as a potential risk, not dropped.

    One small-model answer disagreeing with another isn't proof either way, and dropping a
    real Critical or High problem is the worst mistake a reviewer can make. As a potential
    risk it asks a person to decide (rule 2(d) of requirements 5.3) with both views shown.
    """
    note = f"{NOTE_START}disputed this finding: {decision.reason} A person should decide.)"
    return finding.model_copy(
        update={
            "status": FindingStatus.POTENTIAL_RISK,
            "severity_reason": finding.severity_reason + note,
        }
    )


def _corrected(finding: Finding, decision: Decision) -> Finding:
    """The finding with what the double-check raised: a higher severity, or confirmed.

    The double-check never makes a finding weaker: every finding it lowered in the
    evaluations was right (#72), and weakening a real problem is the costliest mistake, the
    reason `_disputed()` keeps serious findings too. A raise is noted with its reason.
    """
    severity = min(finding.severity, decision.severity, key=lambda level: level.rank)
    confirmed = FindingStatus.CONFIRMED in (finding.status, decision.status)
    status = FindingStatus.CONFIRMED if confirmed else finding.status
    if (severity, status) == (finding.severity, finding.status):
        return finding
    note = (
        f"{NOTE_START}changed this from {finding.severity.value.title()}, "
        f"{finding.status}: {decision.reason})"
    )
    return finding.model_copy(
        update={
            "severity": severity,
            "status": status,
            "severity_reason": finding.severity_reason + note,
        }
    )


def _own_reason(finding: Finding) -> Finding:
    """The finding, with a severity reason that only repeats the definitions replaced.

    Every finding gives the reason for its severity (requirements 5.2), but the model
    sometimes answers with a definition copied from the prompt, often the Critical one on a
    High finding, so the comment would claim harm that has nothing to do with the change
    (#86). A reason that repeats a definition or an example needs a few words of its own
    besides; if it hasn't, it's replaced by a plain note. A short reason that repeats none,
    like "SQL injection vulnerability", is the model's own and stays (#109). A note the
    double-check added stays too.
    """
    reason, note, rest = finding.severity_reason.partition(NOTE_START)
    remaining = reason
    for sentence in severity_definitions():
        remaining = re.sub(re.escape(sentence), " ", remaining, flags=re.IGNORECASE)
    repeats_a_definition = remaining != reason
    if not repeats_a_definition or len(re.findall(r"[A-Za-z0-9_']+", remaining)) >= OWN_WORDS:
        return finding
    return finding.model_copy(update={"severity_reason": NO_REASON + note + rest})

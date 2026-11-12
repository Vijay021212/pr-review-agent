"""Stage 6, double-check: re-check every finding before it's posted (FR-10, FR-21).

    anchor on a changed line -> re-check with the model -> remove duplicates -> drop style noise

The model sees each finding with the code around its line at the reviewed commit, read
from the snapshot, and the change near it, and decides keep or drop with a reason. Every
dropped finding keeps its reason for the summary. The other three steps are plain code
(`anchoring.py`).
"""

from __future__ import annotations

from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pr_review_agent.anchoring import anchor_all, drop_style_noise, remove_duplicates
from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import (
    ChangedFile,
    ChatModel,
    CheckedFindings,
    Deadline,
    DroppedFinding,
    Finding,
    FindingStatus,
    NonEmptyStr,
    Severity,
    Side,
)
from pr_review_agent.prompts import data_block, load
from pr_review_agent.stages import ModelCall, ask

FINDINGS_PER_CALL = 4
CODE_LINES_AROUND = 15  # lines of the reviewed code shown before and after a finding's line
DIFF_LINES_AROUND = 12  # lines of the diff shown around it
SERIOUS = (Severity.CRITICAL, Severity.HIGH)


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
    files: tuple[ChangedFile, ...],
    root: Path,
    model: ChatModel,
    deadline: Deadline,
) -> tuple[CheckedFindings, tuple[ModelCall, ...]]:
    """`dropped_before` holds findings an earlier stage already dropped, with their reasons."""
    anchored = anchor_all(findings, files)
    by_path = {file.path: file for file in files}
    prompt = load("verify")
    kept: list[Finding] = []
    dropped: list[DroppedFinding] = [*dropped_before, *anchored.dropped]
    calls: list[ModelCall] = []
    for start in range(0, len(anchored.kept), FINDINGS_PER_CALL):
        group = anchored.kept[start : start + FINDINGS_PER_CALL]
        deadline.check("the double-check")
        answer, call = ask(
            model, prompt, build_message(group, by_path, root), VerifyAnswer, deadline
        )
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
        kept=useful.kept, dropped=(*dropped, *unique.dropped, *useful.dropped)
    )
    return checked, tuple(calls)


def build_message(group: tuple[Finding, ...], files: dict[str, ChangedFile], root: Path) -> str:
    parts = []
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
        code = _code_around(root, finding)
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
    parts.append(f"Give a decision for each of the {len(group)} findings above.")
    return "\n\n".join(parts)


def _code_around(root: Path, finding: Finding) -> str:
    """Numbered lines of the reviewed file around the finding, or "" for removed code."""
    path = root / finding.file
    if finding.side is Side.OLD or not path.is_file():
        return ""
    lines = path.read_bytes().decode("utf-8", errors="replace").splitlines()
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
    note = f" (The double-check disputed this finding: {decision.reason} A person should decide.)"
    return finding.model_copy(
        update={
            "status": FindingStatus.POTENTIAL_RISK,
            "severity_reason": finding.severity_reason + note,
        }
    )


def _corrected(finding: Finding, decision: Decision) -> Finding:
    """The finding with the double-check's severity and status, noting any change."""
    if (decision.severity, decision.status) == (finding.severity, finding.status):
        return finding
    note = (
        f" (The double-check changed this from {finding.severity.value.title()}, "
        f"{finding.status}: {decision.reason})"
    )
    return finding.model_copy(
        update={
            "severity": decision.severity,
            "status": decision.status,
            "severity_reason": finding.severity_reason + note,
        }
    )

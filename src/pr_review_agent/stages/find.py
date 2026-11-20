"""Stage 5, find problems: focused passes over the diff (FR-6 to FR-9, design section 2).

Each pass looks for one kind of problem with its own prompt, and runs once per batch from
file selection, so every model call stays within the context window (design section 6).
Pass A (correctness and regressions, levels 1-3) comes first; later passes reuse the same
machinery with their own prompt and answer type.
"""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel, ConfigDict

from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import (
    ChangedFile,
    ChatModel,
    Deadline,
    DroppedFinding,
    ExploredContext,
    Finding,
    NonEmptyStr,
    PullRequest,
    ReviewScope,
    Understanding,
)
from pr_review_agent.prompts import data_block, leave_out, load
from pr_review_agent.stages import ModelCall, ask, review_context


class FindingsAnswer(BaseModel):
    """What a finding pass answers: a list of findings, possibly empty."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    findings: tuple[Finding, ...]


class BugFinding(Finding):
    """A finding from a bug-finding pass.

    The trigger, current and expected behaviour are required by the schema itself here, not
    only by the check afterwards, so the model fills them in on the first answer instead of
    needing a retry (FR-9).
    """

    trigger: NonEmptyStr
    current_behaviour: NonEmptyStr
    expected_behaviour: NonEmptyStr


class BugFindingsAnswer(FindingsAnswer):
    findings: tuple[BugFinding, ...]


@dataclass(frozen=True)
class FindPass:
    """One finding pass: its prompt file, what it looks for, and the shape of its answer."""

    prompt: str
    focus: str
    answer_type: type[FindingsAnswer]


PASS_A = FindPass(
    prompt="find_a",
    focus="correctness and regressions (levels 1-3)",
    answer_type=BugFindingsAnswer,
)
PASS_B = FindPass(
    prompt="find_b",
    focus="security and performance (levels 4-5)",
    answer_type=BugFindingsAnswer,
)
PASSES = (PASS_A, PASS_B)
"""The passes the pipeline runs, in order (design section 2)."""


@dataclass(frozen=True)
class FindOutput:
    findings: tuple[Finding, ...]
    dropped: tuple[DroppedFinding, ...]
    calls: tuple[ModelCall, ...]


def find_problems(
    finding_pass: FindPass,
    pull_request: PullRequest,
    scope: ReviewScope,
    understanding: Understanding,
    explored: ExploredContext,
    model: ChatModel,
    deadline: Deadline,
) -> FindOutput:
    """Run one pass over every batch, one batch after another."""
    prompt = load(finding_pass.prompt)
    findings: list[Finding] = []
    dropped: list[DroppedFinding] = []
    calls: list[ModelCall] = []
    for batch in scope.batches:
        deadline.check(f"the {finding_pass.focus} pass")
        message = build_message(pull_request, understanding, explored, batch)
        answer, call = ask(model, prompt, message, finding_pass.answer_type, deadline)
        calls.append(call)
        paths = {file.path for file in batch}
        for found in answer.findings:
            finding = _as_finding(found)
            if finding.file in paths:
                findings.append(finding)
            else:
                dropped.append(
                    DroppedFinding(
                        finding=finding,
                        reason=f"{finding.file} isn't one of the files reviewed in this call",
                    )
                )
    return FindOutput(tuple(findings), tuple(dropped), tuple(calls))


def build_message(
    pull_request: PullRequest,
    understanding: Understanding,
    explored: ExploredContext,
    batch: tuple[ChangedFile, ...],
) -> str:
    # The context the double-check gets too, then the changes; a comment the change adds that
    # tells the reviewer what to do is left out of them (NFR-5).
    parts = review_context(pull_request, understanding, explored)
    diff = "\n\n".join(render_file(file) for file in batch)
    passages = understanding.addressed_to_reviewer
    parts += ["## The changes to review", data_block("DIFF", leave_out(diff, passages))]
    return "\n\n".join(parts)


def _as_finding(found: Finding) -> Finding:
    """A plain `Finding`, with the path written the way GitHub writes it (no leading "./")."""
    values = found.model_dump()
    values["file"] = values["file"].removeprefix("./").lstrip("/")
    return Finding.model_validate(values)

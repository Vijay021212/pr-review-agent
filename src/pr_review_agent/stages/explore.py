"""Stage 4, map and explore: the agent loop (FR-5, design section 5).

The one place where the model decides what to do next. Each step is one structured answer
naming a read-only tool; code runs the tool on the snapshot and shows the result in the
next step. The loop ends when the model answers `done`, after the step limit, or when
exploring has used its share of the time limit; the summary says which.

The model's context is small (16k tokens by default), so each step shows the full result
of the last step only. Earlier steps appear as one line each, plus the notes the model
wrote as it went: its own memory of what mattered.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field

from pr_review_agent.diff_text import CHARS_PER_TOKEN
from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    Excerpt,
    ExploredContext,
    ExploreStop,
    PullRequest,
    ReviewScope,
    ToolAction,
    ToolCall,
    Understanding,
)
from pr_review_agent.project_map import build_project_map
from pr_review_agent.prompts import clip, data_block, load
from pr_review_agent.selection import EXPLORED_TOKENS
from pr_review_agent.stages import ModelCall, ask, changed_files_list, most_important_diff
from pr_review_agent.tools import ReadOnlyTools, ToolResult, normalize_path

TIME_SHARE = 0.35  # exploring may use this share of the time limit; finding needs the rest
MAP_CHARS = 8000
DIFF_CHARS = 15000
RESULT_CHARS = 10000
NOTE_CHARS = 400
EXCERPT_LINES = 60  # how much of each opened file the finding passes see


class ExploreStep(BaseModel):
    """One answer of the loop: the next tool to use, why, and what was learned so far."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: ToolAction
    path: str = ""
    text: str = ""
    start_line: int = Field(default=1, ge=1)
    reason: str = ""
    notes: str = ""


@dataclass(frozen=True)
class _Step:
    answer: ExploreStep
    result: ToolResult

    @property
    def target(self) -> str:
        return _target(self.answer)


def explore(
    pull_request: PullRequest,
    scope: ReviewScope,
    understanding: Understanding,
    model: ChatModel,
    deadline: Deadline,
    root: Path,
    *,
    max_steps: int,
) -> tuple[ExploredContext, tuple[ModelCall, ...]]:
    tools = ReadOnlyTools(root)
    project_map = build_project_map(root)
    prompt = load("explore")
    explore_until = deadline.elapsed() + deadline.seconds * TIME_SHARE
    steps: list[_Step] = []
    calls: list[ModelCall] = []
    notes: list[str] = []
    stopped_by = ExploreStop.STEP_LIMIT
    for number in range(1, max_steps + 1):
        if deadline.elapsed() >= explore_until:
            stopped_by = ExploreStop.TIME_LIMIT
            break
        deadline.check("the next explore step")
        context = _Context(pull_request, scope, understanding, project_map, steps, notes)
        answer, call = ask(model, prompt, context.message(number, max_steps), ExploreStep, deadline)
        calls.append(call)
        if answer.notes.strip():
            notes.append(clip(answer.notes.strip(), NOTE_CHARS))
        if answer.action is ToolAction.DONE:
            steps.append(_Step(answer, ToolResult(output="", summary="done")))
            stopped_by = ExploreStop.DONE
            break
        steps.append(_Step(answer, tools.run(answer.action, _target(answer), answer.start_line)))
    explored = ExploredContext(
        project_map=project_map,
        tool_calls=tuple(
            ToolCall(
                action=step.answer.action,
                target=step.target,
                reason=step.answer.reason,
                result=step.result.summary,
            )
            for step in steps
        ),
        excerpts=_excerpts(steps),
        notes=tuple(notes),
        stopped_by=stopped_by,
    )
    return explored, tuple(calls)


def _target(answer: ExploreStep) -> str:
    return answer.text if answer.action is ToolAction.SEARCH else answer.path


@dataclass(frozen=True)
class _Context:
    """Everything one step shows the model."""

    pull_request: PullRequest
    scope: ReviewScope
    understanding: Understanding
    project_map: str
    steps: list[_Step]
    notes: list[str]

    def message(self, number: int, max_steps: int) -> str:
        understanding = self.understanding
        parts = [
            "## What the pull request is for (written in an earlier step)",
            data_block(
                "UNDERSTANDING", f"Goal: {understanding.goal}\nSummary: {understanding.summary}"
            ),
            "## Changed files",
            changed_files_list(self.pull_request, self.scope),
        ]
        diff = most_important_diff(self.scope)
        if diff:
            parts += [
                "## Diff of the most important files",
                data_block("DIFF", clip(diff, DIFF_CHARS)),
            ]
        parts += ["## Project map", data_block("PROJECT MAP", clip(self.project_map, MAP_CHARS))]
        if self.steps:
            done = "\n".join(
                f"{index}. {step.answer.action} {step.target!r}: {step.result.summary}"
                for index, step in enumerate(self.steps, start=1)
            )
            parts += ["## Steps so far", done]
        if self.notes:
            parts += ["## Your notes so far", "\n".join(f"- {note}" for note in self.notes)]
        if self.steps:
            last = self.steps[-1]
            parts += [
                f"## Result of the last step ({last.answer.action} {last.target!r})",
                data_block("RESULT", clip(last.result.output, RESULT_CHARS)),
            ]
        parts.append(f"This is step {number} of at most {max_steps}. Choose the next step.")
        return "\n\n".join(parts)


def _excerpts(steps: list[_Step]) -> tuple[Excerpt, ...]:
    """The start of each file opened, newest first while they fit the explored-code budget."""
    budget = EXPLORED_TOKENS * CHARS_PER_TOKEN
    kept: list[Excerpt] = []
    used = 0
    for step in reversed(steps):
        if step.answer.action is not ToolAction.OPEN_FILE or step.result.output.startswith(
            "Refused"
        ):
            continue
        text = "\n".join(step.result.output.splitlines()[:EXCERPT_LINES])
        if used + len(text) > budget:
            break
        path = normalize_path(step.answer.path)
        kept.append(Excerpt(path=path, start_line=step.answer.start_line, text=text))
        used += len(text)
    return tuple(reversed(kept))

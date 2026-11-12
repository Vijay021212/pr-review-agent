"""The review stages, run in order by the pipeline (design section 2, ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import ChatModel, Deadline, ModelReply, PullRequest, ReviewScope
from pr_review_agent.models.structured import ask_structured
from pr_review_agent.prompts import Prompt


@dataclass(frozen=True)
class ModelCall:
    """One question a stage asked the model, for the summary and the run log (FR-23)."""

    prompt: str  # the prompt's name and version, for example "understand v1"
    replies: tuple[ModelReply, ...]  # one, or two when the first answer had to be retried
    retry_reason: str | None = None


def ask[T: BaseModel](
    model: ChatModel, prompt: Prompt, user: str, answer_type: type[T], deadline: Deadline
) -> tuple[T, ModelCall]:
    """Ask with a stage's prompt, check the answer's type, and record the call."""
    answer = ask_structured(
        model, system=prompt.text, user=user, answer_type=answer_type, deadline=deadline
    )
    return answer.value, ModelCall(prompt.label, answer.replies, answer.retry_reason)


def changed_files_list(pull_request: PullRequest, scope: ReviewScope) -> str:
    """One line per changed file: its status and size, or why it was skipped."""
    reasons = {skip.path: skip.reason for skip in scope.skipped}
    lines = []
    for file in pull_request.files:
        reason = reasons.get(file.path)
        size = f"+{len(file.added_lines)} -{len(file.removed_lines)}"
        lines.append(f"- {file.path} ({file.status}, {f'skipped: {reason}' if reason else size})")
    return "\n".join(lines)


def most_important_diff(scope: ReviewScope) -> str:
    """The numbered diffs of the first batch: the most important files, within the budget
    (design section 6). Empty when nothing is reviewed."""
    if not scope.batches:
        return ""
    return "\n\n".join(render_file(file) for file in scope.batches[0])

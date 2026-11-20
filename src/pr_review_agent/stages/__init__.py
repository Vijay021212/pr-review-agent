"""The review stages, run in order by the pipeline (design section 2, ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    ExploredContext,
    ModelReply,
    PullRequest,
    ReviewScope,
    Understanding,
)
from pr_review_agent.models.structured import ask_structured
from pr_review_agent.prompts import Prompt, clip, data_block, leave_out, leave_out_each_sentence

# How much of a pull request's own text the understand and screen stages read.
DESCRIPTION_CHARS = 4000
ISSUE_CHARS = 1500
# How much of its description the finding passes and the double-check read.
REVIEW_DESCRIPTION_CHARS = 1500


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


def pull_request_text(pull_request: PullRequest) -> list[str]:
    """The pull request's own text, each part between data markers: its title, its
    description and its linked issues, as the understand and screen stages read them."""
    parts = [
        "## Pull request",
        data_block("TITLE", pull_request.title),
        data_block("DESCRIPTION", clip(pull_request.description, DESCRIPTION_CHARS) or "(empty)"),
    ]
    for issue in pull_request.linked_issues:
        text = f"{issue.title}\n\n{clip(issue.body, ISSUE_CHARS)}".strip()
        parts.append(data_block(f"LINKED ISSUE #{issue.number}", text))
    return parts


def review_context(
    pull_request: PullRequest, understanding: Understanding, explored: ExploredContext
) -> list[str]:
    """What the finding passes and the double-check both know about the pull request.

    Its goal and summary, its title and description, the notes from exploring and the
    related code exploring opened, each between data markers. What the pull request tells
    the reviewer to do or conclude is left out (NFR-5), before clipping, so a passage can't
    be cut in two. The double-check judges a finding with the same context the finding pass
    had (#84).

    The understanding's risky parts aren't part of it: they're the model's guesses, written
    before the review and never checked, and one wrong guess ("low <= high is not checked",
    when it is) made the finding passes report it and the double-check confirm it on a
    clean change (#88). They're for the person reading the summary.
    """
    passages = understanding.addressed_to_reviewer
    description = clip(leave_out(pull_request.description, passages), REVIEW_DESCRIPTION_CHARS)
    parts = [
        "## What the pull request is for (written in an earlier step)",
        understanding_block(understanding),
        "## Pull request",
        data_block("TITLE", leave_out(pull_request.title, passages)),
        data_block("DESCRIPTION", description or "(empty)"),
    ]
    if explored.notes:
        notes = "\n".join(f"- {note}" for note in explored.notes)
        parts += ["## Notes from exploring the repository", data_block("NOTES", notes)]
    if explored.excerpts:
        related = "\n\n".join(
            f"### {excerpt.path} (from line {excerpt.start_line})\n{excerpt.text}"
            for excerpt in explored.excerpts
        )
        parts += ["## Related code (unchanged, for context)", data_block("RELATED CODE", related)]
    return parts


def understanding_block(understanding: Understanding) -> str:
    """The goal and the summary from the understand stage, between data markers.

    The model wrote them from the pull request's text before the screen stage ran, so what
    the pull request tells the reviewer to do is left out of them too, each sentence on its
    own: a goal of one or two sentences can repeat one sentence of a longer order (#109).
    """
    passages = understanding.addressed_to_reviewer
    goal = leave_out_each_sentence(understanding.goal, passages)
    summary = leave_out_each_sentence(understanding.summary, passages)
    return data_block("UNDERSTANDING", f"Goal: {goal}\nSummary: {summary}")


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

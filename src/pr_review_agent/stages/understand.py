"""Stage 3, understand: what is the pull request for? (FR-2)

Written before looking for problems, so the finding passes can check that the code does
what the pull request says, and so a reviewer can see straight away if the agent
misunderstood. The understanding opens the summary (FR-14), and its "goal unclear" flag
feeds rule 2(b) of the recommendation (requirements 5.3).
"""

from __future__ import annotations

from pr_review_agent.diff_text import render_file
from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    GoalSource,
    PullRequest,
    ReviewScope,
    Understanding,
)
from pr_review_agent.prompts import clip, data_block, load
from pr_review_agent.stages import ModelCall, ask

DESCRIPTION_CHARS = 4000
ISSUE_CHARS = 1500


def understand(
    pull_request: PullRequest, scope: ReviewScope, model: ChatModel, deadline: Deadline
) -> tuple[Understanding, ModelCall]:
    deadline.check("the understand stage")
    understanding, call = ask(
        model, load("understand"), build_message(pull_request, scope), Understanding, deadline
    )
    if not _has_stated_goal(pull_request):
        # Nothing was written down, so the goal can only have come from the code (FR-2),
        # whatever the model says.
        understanding = understanding.model_copy(update={"goal_source": GoalSource.CODE})
    return understanding, call


def build_message(pull_request: PullRequest, scope: ReviewScope) -> str:
    """The user message: the pull request's text, its files, and the most important diffs."""
    parts = [
        "## Pull request",
        data_block("TITLE", pull_request.title),
        data_block("DESCRIPTION", clip(pull_request.description, DESCRIPTION_CHARS) or "(empty)"),
    ]
    for issue in pull_request.linked_issues:
        text = f"{issue.title}\n\n{clip(issue.body, ISSUE_CHARS)}".strip()
        parts.append(data_block(f"LINKED ISSUE #{issue.number}", text))

    parts.append("## Changed files")
    reasons = {skip.path: skip.reason for skip in scope.skipped}
    for file in pull_request.files:
        reason = reasons.get(file.path)
        note = (
            f"skipped: {reason}"
            if reason
            else f"+{len(file.added_lines)} -{len(file.removed_lines)}"
        )
        parts.append(f"- {file.path} ({file.status}, {note})")

    if scope.batches:
        # The first batch holds the most important files and fits the budget (design 6).
        first = scope.batches[0]
        parts.append("## Diff of the most important files")
        parts.append(data_block("DIFF", "\n\n".join(render_file(file) for file in first)))
        rest = len(scope.files) - len(first)
        if rest:
            parts.append(f"({rest} more reviewed files aren't shown here.)")
    return "\n\n".join(parts)


def _has_stated_goal(pull_request: PullRequest) -> bool:
    return bool(pull_request.description.strip() or pull_request.linked_issues)

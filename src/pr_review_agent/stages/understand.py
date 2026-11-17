"""Stage 3, understand: what is the pull request for? (FR-2)

Written before looking for problems, so the finding passes can check that the code does
what the pull request says, and so a reviewer can see straight away if the agent
misunderstood. The understanding opens the summary (FR-14), and its "goal unclear" flag
feeds rule 2(b) of the recommendation (requirements 5.3).

It also quotes the sentences of the pull request's text that tell the reviewer what to do
or conclude, so the finding passes read the text without them (NFR-5): a small model can
follow such a sentence even inside the data markers.
"""

from __future__ import annotations

from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    GoalSource,
    NonEmptyStr,
    PullRequest,
    ReviewScope,
    Understanding,
)
from pr_review_agent.prompts import appears_in, clip, data_block, load
from pr_review_agent.stages import ModelCall, ask, changed_files_list, most_important_diff

DESCRIPTION_CHARS = 4000
ISSUE_CHARS = 1500


class UnderstandAnswer(Understanding):
    """The understanding as the model answers it, with the quotes required: small models
    leave optional fields out, and an empty list is how they say there are none."""

    addressed_to_reviewer: tuple[NonEmptyStr, ...]


def understand(
    pull_request: PullRequest, scope: ReviewScope, model: ChatModel, deadline: Deadline
) -> tuple[Understanding, ModelCall]:
    deadline.check("the understand stage")
    answer, call = ask(
        model, load("understand"), build_message(pull_request, scope), UnderstandAnswer, deadline
    )
    understanding = Understanding.model_validate(answer.model_dump())
    # A quote counts only if it's in the pull request's text word for word: what the
    # finding passes don't read, and what the summary says was left out, never rests on
    # the model's word alone.
    text = "\n".join(
        [
            pull_request.title,
            pull_request.description,
            *(f"{issue.title}\n{issue.body}" for issue in pull_request.linked_issues),
        ]
    )
    quoted = tuple(q for q in understanding.addressed_to_reviewer if appears_in(q, text))
    updates: dict[str, object] = {"addressed_to_reviewer": quoted}
    if not _has_stated_goal(pull_request):
        # Nothing was written down, so the goal can only have come from the code (FR-2),
        # whatever the model says.
        updates["goal_source"] = GoalSource.CODE
    return understanding.model_copy(update=updates), call


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

    parts += ["## Changed files", changed_files_list(pull_request, scope)]
    diff = most_important_diff(scope)
    if diff:
        parts += ["## Diff of the most important files", data_block("DIFF", diff)]
        rest = len(scope.files) - len(scope.batches[0])
        if rest:
            parts.append(f"({rest} more reviewed files aren't shown here.)")
    return "\n\n".join(parts)


def _has_stated_goal(pull_request: PullRequest) -> bool:
    return bool(pull_request.description.strip() or pull_request.linked_issues)

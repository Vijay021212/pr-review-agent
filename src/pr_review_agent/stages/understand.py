"""Stage 3, understand: what is the pull request for? (FR-2)

Written before looking for problems, so the finding passes can check that the code does
what the pull request says, and so a reviewer can see straight away if the agent
misunderstood. The understanding opens the summary (FR-14), and its "goal unclear" flag
feeds rule 2(b) of the recommendation (requirements 5.3).

The model answers an `UnderstandingAnswer`; the screen stage that runs next adds what the
pull request's text tells the reviewer to do (`screen.py`), so looking for that never
changes how the pull request is understood (#80).
"""

from __future__ import annotations

from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    GoalSource,
    PullRequest,
    ReviewScope,
    Understanding,
    UnderstandingAnswer,
)
from pr_review_agent.prompts import data_block, load
from pr_review_agent.stages import (
    ModelCall,
    ask,
    changed_files_list,
    most_important_diff,
    pull_request_text,
)


def understand(
    pull_request: PullRequest, scope: ReviewScope, model: ChatModel, deadline: Deadline
) -> tuple[Understanding, ModelCall]:
    deadline.check("the understand stage")
    answer, call = ask(
        model, load("understand"), build_message(pull_request, scope), UnderstandingAnswer, deadline
    )
    understanding = Understanding.model_validate(answer.model_dump())
    if not _has_stated_goal(pull_request):
        # Nothing was written down, so the goal can only have come from the code (FR-2),
        # whatever the model says.
        understanding = understanding.model_copy(update={"goal_source": GoalSource.CODE})
    return understanding, call


def build_message(pull_request: PullRequest, scope: ReviewScope) -> str:
    """The user message: the pull request's text, its files, and the most important diffs."""
    parts = pull_request_text(pull_request)
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

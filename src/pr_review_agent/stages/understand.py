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
    changed = {file.path for file in pull_request.files}
    risky = _named_risks(understanding.risky_parts, changed)
    understanding = understanding.model_copy(update={"risky_parts": risky})
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


def _named_risks(parts: tuple[str, ...], changed: set[str]) -> tuple[str, ...]:
    """The risky parts, each naming its file. The model sometimes writes a changed file and
    what could break in it as two items, so the summary listed the file's bare name as a
    risk of its own (#109): such a name is joined to the item after it, and dropped when
    nothing follows it."""
    joined: list[str] = []
    named: str | None = None  # a bare file name waiting for its risk
    for part in parts:
        bare = part.strip().strip("`").strip().removesuffix(":").strip("`").strip()
        if bare in changed:
            named = bare
            continue
        joined.append(f"`{named}`: {part}" if named else part)
        named = None
    return tuple(joined)

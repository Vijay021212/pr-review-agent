"""Stage 1, fetch: read the pull request, fixed at its current head commit (FR-1, FR-19).

Only open pull requests are reviewed. Drafts are reviewed too, and the summary says so;
closed and merged ones are refused with a clear message (FR-20).
"""

from __future__ import annotations

from pr_review_agent.domain import (
    Deadline,
    GitHub,
    PullRequest,
    PullRequestRef,
    PullRequestState,
    SetupError,
)


def fetch(github: GitHub, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
    pull_request = github.get_pull_request(ref, deadline)
    if pull_request.state is not PullRequestState.OPEN:
        raise SetupError(
            f"Pull request {ref} is {pull_request.state}, so there's nothing to review.",
            "Only open pull requests are reviewed: reopen it first, or choose another one.",
        )
    return pull_request

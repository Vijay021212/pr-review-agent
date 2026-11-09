"""Tests for the fetch stage: only open pull requests are reviewed (FR-20)."""

from __future__ import annotations

import pytest

from pr_review_agent.domain import (
    Deadline,
    PullRequest,
    PullRequestRef,
    PullRequestState,
    SetupError,
)
from pr_review_agent.stages.fetch import fetch
from tests.builders import make_pull_request

REF = PullRequestRef(owner="octo", repo="shop", number=7)


class OneGitHub:
    def __init__(self, pull_request: PullRequest) -> None:
        self.pull_request = pull_request

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        return self.pull_request


@pytest.mark.parametrize("draft", [False, True])
def test_open_pull_requests_and_drafts_are_reviewed(draft: bool) -> None:
    pull_request = make_pull_request(draft=draft)
    assert fetch(OneGitHub(pull_request), REF, Deadline(60)) is pull_request


@pytest.mark.parametrize("state", [PullRequestState.CLOSED, PullRequestState.MERGED])
def test_closed_and_merged_pull_requests_are_refused(state: PullRequestState) -> None:
    github = OneGitHub(make_pull_request(state=state))
    with pytest.raises(SetupError, match=f"octo/shop#7 is {state}, so there's nothing to review"):
        fetch(github, REF, Deadline(60))

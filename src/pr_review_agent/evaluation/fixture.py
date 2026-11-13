"""`FixtureGitHub`: serves an evaluation case to the real pipeline as a GitHub pull request.

Everything the pipeline asks GitHub for comes from the case's folder instead, so the
evaluation is offline and gives the agent exactly the same input every time (7.1). The
evaluation runs as a dry run, so nothing is posted; should anything try, it's recorded
here and never leaves the computer.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from pr_review_agent.domain import (
    Deadline,
    PullRequest,
    PullRequestRef,
    ReviewComment,
    SetupError,
)
from pr_review_agent.evaluation.cases import Case


class FixtureGitHub:
    def __init__(self, case: Case) -> None:
        self.case = case
        self.posted: list[str] = []

    def check_access(self, ref: PullRequestRef, *, write: bool, deadline: Deadline) -> None:
        if write:
            raise SetupError("The evaluation never posts.", "Run it as a dry run, as `eval` does.")

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        return self.case.pull_request

    def download_snapshot(
        self, pull_request: PullRequest, folder: Path, deadline: Deadline
    ) -> Path:
        root = folder / "repository"
        shutil.copytree(self.case.head, root)
        return root

    def head_sha(self, ref: PullRequestRef, deadline: Deadline) -> str:
        return self.case.pull_request.head_sha

    def post_review(
        self,
        ref: PullRequestRef,
        commit_sha: str,
        body: str,
        comments: tuple[ReviewComment, ...],
        deadline: Deadline,
    ) -> str:
        self.posted.append(body)
        return "not posted (evaluation)"

    def upsert_summary(
        self, ref: PullRequestRef, body: str, marker: str, deadline: Deadline
    ) -> str:
        self.posted.append(body)
        return "not posted (evaluation)"

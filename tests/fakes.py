"""Fakes for the outside world, so tests need no model, network or GitHub (NFR-8)."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pr_review_agent.domain import (
    Deadline,
    ModelReply,
    PullRequest,
    PullRequestRef,
    ReviewComment,
    SetupError,
)


@dataclass(frozen=True)
class ModelCall:
    system: str
    user: str
    schema: dict[str, Any]


@dataclass
class FakeChatModel:
    """Answers with scripted replies, in order, and records every call it receives."""

    replies: list[str]
    provider: str = "fake"
    model: str = "fake-model"
    calls: list[ModelCall] = field(default_factory=list)
    ready_error: Exception | None = None  # what check_ready() raises, if anything

    def check_ready(self, deadline: Deadline) -> None:
        if self.ready_error is not None:
            raise self.ready_error

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], deadline: Deadline
    ) -> ModelReply:
        self.calls.append(ModelCall(system, user, schema))
        if not self.replies:
            raise AssertionError("the fake model has no scripted reply left")
        return ModelReply(
            text=self.replies.pop(0), model=self.model, input_tokens=10, output_tokens=5
        )


@dataclass
class FakeGitHub:
    """Serves pull requests from memory; an unknown one is "not found", as on GitHub.

    `repository` holds the snapshot's files, path -> text, written out on download.
    """

    pull_requests: dict[str, PullRequest] = field(default_factory=dict)
    repository: dict[str, str] = field(default_factory=dict)
    reads: list[PullRequestRef] = field(default_factory=list)
    snapshots: list[str] = field(default_factory=list)  # head commits downloaded
    reviews: list[PostedReview] = field(default_factory=list)
    summaries: list[str] = field(default_factory=list)
    moved_head: str | None = None  # a new commit pushed while the review runs
    summary_error: Exception | None = None  # posting the summary fails with this
    access_error: Exception | None = None  # what check_access() raises, if anything
    access_checks: list[tuple[str, bool]] = field(default_factory=list)  # (ref, write)

    @classmethod
    def serving(
        cls, *pull_requests: PullRequest, repository: dict[str, str] | None = None
    ) -> FakeGitHub:
        return cls({str(pr.ref): pr for pr in pull_requests}, repository or {})

    def get_pull_request(self, ref: PullRequestRef, deadline: Deadline) -> PullRequest:
        self.reads.append(ref)
        if str(ref) not in self.pull_requests:
            raise SetupError(f"GitHub couldn't find pull request {ref}.", "Check the link.")
        return self.pull_requests[str(ref)]

    def download_snapshot(
        self, pull_request: PullRequest, folder: Path, deadline: Deadline
    ) -> Path:
        self.snapshots.append(pull_request.head_sha)
        root = folder / "repository"
        for path, text in self.repository.items():
            (root / path).parent.mkdir(parents=True, exist_ok=True)
            (root / path).write_text(text, encoding="utf-8")
        root.mkdir(exist_ok=True)
        return root

    def check_access(self, ref: PullRequestRef, *, write: bool, deadline: Deadline) -> None:
        self.access_checks.append((str(ref), write))
        if self.access_error is not None:
            raise self.access_error

    # --- Publishing: everything "posted" is recorded instead -----------------------

    def head_sha(self, ref: PullRequestRef, deadline: Deadline) -> str:
        return self.moved_head or self.pull_requests[str(ref)].head_sha

    def post_review(
        self,
        ref: PullRequestRef,
        commit_sha: str,
        body: str,
        comments: tuple[ReviewComment, ...],
        deadline: Deadline,
    ) -> str:
        self.reviews.append(PostedReview(commit_sha, body, comments))
        return f"{ref.link}#pullrequestreview-{len(self.reviews)}"

    def upsert_summary(
        self, ref: PullRequestRef, body: str, marker: str, deadline: Deadline
    ) -> str:
        if self.summary_error is not None:
            raise self.summary_error
        self.summaries.append(body)
        return f"{ref.link}#issuecomment-1"


@dataclass(frozen=True)
class PostedReview:
    commit_sha: str
    body: str
    comments: tuple[ReviewComment, ...]


def explore_step(action: str, **fields: Any) -> str:
    """One answer of the explore stage, with every field it requires."""
    answer = {"action": action, "path": "", "text": "", "start_line": 1, "notes": ""}
    answer["reason"] = f"Because {action}."
    return json.dumps({**answer, **fields})


EXPLORE_DONE = explore_step("done", reason="The diff shows everything the review needs.")
"""The explore stage's answer when the model needs to read nothing more."""


def double_check(*keep: bool, severity: str = "high", status: str = "confirmed") -> str:
    """The double-check's answer: one decision per finding, in order."""
    decisions = [
        {
            "number": number,
            "keep": kept,
            "reason": "The code shown supports it." if kept else "The code shown contradicts it.",
            "severity": severity,
            "status": status,
        }
        for number, kept in enumerate(keep, start=1)
    ]
    return json.dumps({"decisions": decisions})


KEEP_ONE = double_check(True)
"""The double-check keeping a single High, confirmed finding as it is."""

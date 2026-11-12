"""Fakes for the outside world, so tests need no model, network or GitHub (NFR-8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pr_review_agent.domain import Deadline, ModelReply, PullRequest, PullRequestRef, SetupError


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

"""Fakes for the outside world, so tests need no model, network or GitHub (NFR-8)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from pr_review_agent.domain import Deadline, ModelReply


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

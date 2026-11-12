"""The review stages, run in order by the pipeline (design section 2, ADR-002)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import BaseModel

from pr_review_agent.domain import ChatModel, Deadline, ModelReply
from pr_review_agent.models.structured import ask_structured
from pr_review_agent.prompts import Prompt


@dataclass(frozen=True)
class ModelCall:
    """One question a stage asked the model, for the summary and the run log (FR-23)."""

    prompt: str  # the prompt's name and version, for example "understand v1"
    replies: tuple[ModelReply, ...]  # one, or two when the first answer had to be retried
    retry_reason: str | None = None


def ask[T: BaseModel](
    model: ChatModel, prompt: Prompt, user: str, answer_type: type[T], deadline: Deadline
) -> tuple[T, ModelCall]:
    """Ask with a stage's prompt, check the answer's type, and record the call."""
    answer = ask_structured(
        model, system=prompt.text, user=user, answer_type=answer_type, deadline=deadline
    )
    return answer.value, ModelCall(prompt.label, answer.replies, answer.retry_reason)

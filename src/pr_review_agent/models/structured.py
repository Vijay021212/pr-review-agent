"""Ask a model for an answer of a given type, check it, and retry once if it's wrong (FR-9).

The answer's type is a Pydantic model, usually built from the domain types, so the same
rules that define a valid `Finding` also check what the model wrote. This works the same
for every provider, which is why it lives outside the wrappers (ADR-003).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from pr_review_agent.domain import ChatModel, Deadline, ModelReply, ReviewIncomplete

# How much of a wrong answer is quoted back in the follow-up, so it can't fill the context.
_QUOTED_ANSWER_CHARS = 4000
_PROBLEMS_SHOWN = 5

_FOLLOW_UP = """

Your previous answer didn't match the required JSON format.

Your previous answer:
{answer}

The problems:
{problems}

Answer again with JSON that matches the format exactly."""


@dataclass(frozen=True)
class StructuredAnswer[T: BaseModel]:
    """The checked answer, and every reply it took to get it (one, or two after a retry)."""

    value: T
    replies: tuple[ModelReply, ...]
    retry_reason: str | None = None  # what was wrong with the first answer, if anything


def ask_structured[T: BaseModel](
    model: ChatModel,
    *,
    system: str,
    user: str,
    answer_type: type[T],
    deadline: Deadline,
) -> StructuredAnswer[T]:
    """Ask, check the answer against `answer_type`, and retry once with the problem named."""
    schema = json_schema(answer_type)
    first = model.complete_json(system, user, schema, deadline)
    try:
        return StructuredAnswer(_parse(answer_type, first.text), (first,))
    except _WrongShape as problem:
        retry_reason = str(problem)
    follow_up = user + _FOLLOW_UP.format(
        answer=first.text[:_QUOTED_ANSWER_CHARS], problems=retry_reason
    )
    second = model.complete_json(system, follow_up, schema, deadline)
    try:
        value = _parse(answer_type, second.text)
    except _WrongShape as problem:
        raise ReviewIncomplete(
            f"the model's answer didn't match the required format, even after a retry: {problem}"
        ) from None
    return StructuredAnswer(value, (first, second), retry_reason)


def json_schema(answer_type: type[BaseModel]) -> dict[str, Any]:
    """The JSON schema of `answer_type`, with every `$ref` written out in full.

    Pydantic puts shared parts (like an enum used twice) under `$defs` and points to them.
    Providers differ in how well they follow such pointers, so none are left.
    """
    schema = answer_type.model_json_schema()
    definitions = schema.pop("$defs", {})

    def inline(node: Any) -> Any:
        if isinstance(node, dict):
            if "$ref" in node:
                name = node["$ref"].removeprefix("#/$defs/")
                return inline(
                    {**definitions[name], **{k: v for k, v in node.items() if k != "$ref"}}
                )
            return {key: inline(value) for key, value in node.items()}
        if isinstance(node, list):
            return [inline(item) for item in node]
        return node

    result: dict[str, Any] = inline(schema)
    return result


class _WrongShape(Exception):
    """The answer isn't JSON, or doesn't match the type; the message names the problems."""


def _parse[T: BaseModel](answer_type: type[T], text: str) -> T:
    try:
        return answer_type.model_validate_json(text)
    except ValidationError as error:
        problems = error.errors(include_url=False)
        if problems and problems[0]["type"] == "json_invalid":
            raise _WrongShape(f"the answer isn't valid JSON ({problems[0]['msg']})") from None
        lines = [
            f"- {'.'.join(str(part) for part in item['loc']) or 'the answer'}: {item['msg']}"
            for item in problems[:_PROBLEMS_SHOWN]
        ]
        if len(problems) > _PROBLEMS_SHOWN:
            lines.append(f"- and {len(problems) - _PROBLEMS_SHOWN} more")
        raise _WrongShape("\n".join(lines)) from None

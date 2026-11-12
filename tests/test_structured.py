"""Tests for checking the model's answers against their type, with one retry (FR-9)."""

from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from pr_review_agent.domain import Deadline, Finding, ReviewIncomplete
from pr_review_agent.models.structured import ask_structured, json_schema
from tests.builders import make_finding
from tests.fakes import FakeChatModel


class Findings(BaseModel):
    findings: tuple[Finding, ...]


GOOD = Findings(findings=(make_finding(),)).model_dump_json()
BAD_LINE = json.dumps({"findings": [{**json.loads(make_finding().model_dump_json()), "line": "x"}]})


def ask(model: FakeChatModel) -> object:
    return ask_structured(
        model, system="Review.", user="The diff.", answer_type=Findings, deadline=Deadline(60)
    )


def test_a_good_answer_is_returned_as_its_type() -> None:
    model = FakeChatModel([GOOD])
    answer = ask_structured(
        model, system="Review.", user="The diff.", answer_type=Findings, deadline=Deadline(60)
    )
    assert answer.value.findings[0].line == 1
    assert len(answer.replies) == 1
    assert answer.retry_reason is None
    assert model.calls[0].schema == json_schema(Findings)


def test_a_wrong_answer_is_retried_once_with_the_problem_named() -> None:
    model = FakeChatModel([BAD_LINE, GOOD])
    answer = ask_structured(
        model, system="Review.", user="The diff.", answer_type=Findings, deadline=Deadline(60)
    )
    assert len(answer.replies) == 2
    assert answer.retry_reason is not None
    assert "findings.0.line: Input should be a valid integer" in answer.retry_reason
    follow_up = model.calls[1].user
    assert follow_up.startswith("The diff.")
    assert "Your previous answer didn't match the required JSON format." in follow_up
    assert BAD_LINE in follow_up
    assert "findings.0.line: Input should be a valid integer" in follow_up
    assert model.calls[1].system == "Review."


def test_two_wrong_answers_stop_the_review() -> None:
    with pytest.raises(ReviewIncomplete, match=r"even after a retry: - findings\.0\.line"):
        ask(FakeChatModel([BAD_LINE, BAD_LINE]))


def test_an_answer_that_isnt_json_is_named_as_such() -> None:
    model = FakeChatModel(["Sure! Here are the findings:", GOOD])
    answer = ask_structured(
        model, system="s", user="u", answer_type=Findings, deadline=Deadline(60)
    )
    assert answer.retry_reason is not None
    assert answer.retry_reason.startswith("the answer isn't valid JSON")


def test_a_rule_broken_by_the_model_is_named() -> None:
    finding = json.loads(make_finding().model_dump_json())
    finding["trigger"] = ""
    with pytest.raises(ReviewIncomplete, match="must say what triggers it"):
        ask(FakeChatModel([json.dumps({"findings": [finding]})] * 2))


def test_an_answer_of_the_wrong_kind_is_named() -> None:
    with pytest.raises(ReviewIncomplete, match="- the answer: Input should be an object"):
        ask(FakeChatModel(["[]", "[]"]))


def test_many_problems_are_shortened() -> None:
    model = FakeChatModel(['{"findings": [{}]}', GOOD])
    answer = ask_structured(
        model, system="s", user="u", answer_type=Findings, deadline=Deadline(60)
    )
    assert answer.retry_reason is not None
    assert answer.retry_reason.count("\n- ") == 5  # five problems shown, then the rest counted
    assert answer.retry_reason.endswith("more")


def test_a_long_wrong_answer_is_quoted_only_in_part() -> None:
    long_answer = "x" * 10_000
    model = FakeChatModel([long_answer, GOOD])
    ask_structured(model, system="s", user="u", answer_type=Findings, deadline=Deadline(60))
    assert "x" * 4000 in model.calls[1].user
    assert "x" * 4001 not in model.calls[1].user


def test_the_schema_has_every_reference_written_out() -> None:
    schema = json_schema(Findings)
    text = json.dumps(schema)
    assert "$ref" not in text
    assert "$defs" not in text
    finding = schema["properties"]["findings"]["items"]
    assert finding["additionalProperties"] is False
    assert finding["properties"]["severity"]["enum"] == ["critical", "high", "medium", "low"]
    assert finding["properties"]["level"]["enum"] == [1, 2, 3, 4, 5]
    assert "file" in finding["required"]

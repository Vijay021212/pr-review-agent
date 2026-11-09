"""Tests for the finding passes, with a fake model (FR-6, FR-9)."""

from __future__ import annotations

import json
from typing import Any

import pytest

from pr_review_agent.domain import (
    Category,
    Deadline,
    Excerpt,
    ExploredContext,
    GoalSource,
    ReviewLevel,
    ReviewScope,
    TimeLimitReached,
    Understanding,
)
from pr_review_agent.prompts import load
from pr_review_agent.stages.find import PASS_A, FindOutput, build_message, find_problems
from tests.builders import make_file, make_finding, make_pull_request
from tests.fakes import FakeChatModel

UNDERSTANDING = Understanding(
    goal="Give members a discount.",
    summary="Adds discount() and uses it in total().",
    risky_parts=("cart.py: every member total changes",),
    goal_unclear=False,
    goal_source=GoalSource.DESCRIPTION,
)
CART = make_file("src/cart.py", ["def discount(total):", "    return total * 0.9"])
STATS = make_file("src/stats.py", ["def average(xs):", "    return sum(xs) / len(xs)"])


def finding_json(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = json.loads(make_finding(file="src/cart.py", line=2).model_dump_json())
    return {**values, **changes}


def reply(*findings: dict[str, Any]) -> str:
    return json.dumps({"findings": list(findings)})


def run(
    model: FakeChatModel,
    scope: ReviewScope,
    explored: ExploredContext | None = None,
    deadline: Deadline | None = None,
) -> FindOutput:
    return find_problems(
        PASS_A,
        make_pull_request(files=scope.files),
        scope,
        UNDERSTANDING,
        explored or ExploredContext(),
        model,
        deadline or Deadline(60),
    )


def test_findings_come_back_with_every_field() -> None:
    model = FakeChatModel([reply(finding_json(level=1, affected_places=["src/shop.py:12"]))])
    output = run(model, ReviewScope(batches=((CART,),)))
    (finding,) = output.findings
    assert (finding.file, finding.line, finding.level) == ("src/cart.py", 2, ReviewLevel.OBVIOUS)
    assert finding.category is Category.BUG
    assert finding.trigger
    assert finding.affected_places == ("src/shop.py:12",)
    assert type(finding).__name__ == "Finding"
    assert output.dropped == ()
    assert [call.prompt for call in output.calls] == ["find_a v1"]


def test_no_findings_is_a_valid_answer() -> None:
    output = run(FakeChatModel([reply()]), ReviewScope(batches=((CART,),)))
    assert output.findings == ()


def test_each_batch_gets_its_own_call_in_order() -> None:
    model = FakeChatModel([reply(finding_json()), reply(finding_json(file="src/stats.py"))])
    output = run(model, ReviewScope(batches=((CART,), (STATS,))))
    assert [finding.file for finding in output.findings] == ["src/cart.py", "src/stats.py"]
    assert "### src/cart.py" in model.calls[0].user
    assert "### src/stats.py" not in model.calls[0].user
    assert "### src/stats.py" in model.calls[1].user
    assert len(output.calls) == 2


def test_nothing_to_review_means_no_calls() -> None:
    model = FakeChatModel([])
    assert run(model, ReviewScope()) == FindOutput((), (), ())


def test_a_finding_outside_the_batch_is_dropped_with_the_reason() -> None:
    model = FakeChatModel([reply(finding_json(file="src/other.py"))])
    output = run(model, ReviewScope(batches=((CART,),)))
    assert output.findings == ()
    (dropped,) = output.dropped
    assert dropped.finding.file == "src/other.py"
    assert dropped.reason == "src/other.py isn't one of the files reviewed in this call"


@pytest.mark.parametrize("written", ["./src/cart.py", "/src/cart.py"])
def test_paths_are_written_the_way_github_writes_them(written: str) -> None:
    output = run(
        FakeChatModel([reply(finding_json(file=written))]), ReviewScope(batches=((CART,),))
    )
    assert output.findings[0].file == "src/cart.py"


def test_a_missing_failure_field_is_retried() -> None:
    model = FakeChatModel([reply(finding_json(trigger=None)), reply(finding_json())])
    output = run(model, ReviewScope(batches=((CART,),)))
    assert len(output.findings) == 1
    assert output.calls[0].retry_reason is not None
    assert "trigger" in output.calls[0].retry_reason


def test_the_schema_requires_the_failure_fields() -> None:
    model = FakeChatModel([reply()])
    run(model, ReviewScope(batches=((CART,),)))
    finding_schema = model.calls[0].schema["properties"]["findings"]["items"]
    for field in ("trigger", "current_behaviour", "expected_behaviour", "severity_reason"):
        assert field in finding_schema["required"]


class SlowModel(FakeChatModel):
    """Each answer takes 100 seconds of the fake clock."""

    now: float = 0.0

    def complete_json(self, *args: Any, **kwargs: Any) -> Any:
        SlowModel.now += 100
        return super().complete_json(*args, **kwargs)


def test_the_time_limit_is_checked_before_each_batch() -> None:
    SlowModel.now = 0.0
    deadline = Deadline(60, clock=lambda: SlowModel.now)
    model = SlowModel([reply(), reply()])
    with pytest.raises(TimeLimitReached, match="before the correctness and regressions"):
        run(model, ReviewScope(batches=((CART,), (STATS,))), deadline=deadline)
    assert len(model.calls) == 1


def test_the_message_holds_the_understanding_text_and_diff() -> None:
    pull_request = make_pull_request(title="Add a discount", description="Members get 10% off.")
    message = build_message(pull_request, UNDERSTANDING, ExploredContext(), (CART,))
    assert "Goal: Give members a discount." in message
    assert "- cart.py: every member total changes" in message
    assert "<<<BEGIN TITLE>>>\nAdd a discount" in message
    assert "Members get 10% off." in message
    assert "     2 +     return total * 0.9" in message
    assert "Related code" not in message


def test_the_message_says_when_nothing_risky_was_named() -> None:
    understanding = UNDERSTANDING.model_copy(update={"risky_parts": ()})
    message = build_message(
        make_pull_request(description=""), understanding, ExploredContext(), (CART,)
    )
    assert "- (none named)" in message
    assert "<<<BEGIN DESCRIPTION>>>\n(empty)" in message


def test_explored_code_is_shown_as_related_code() -> None:
    explored = ExploredContext(
        excerpts=(Excerpt(path="src/shop.py", start_line=10, text="10  total = discount(price)"),)
    )
    message = build_message(make_pull_request(), UNDERSTANDING, explored, (CART,))
    assert "## Related code (unchanged, for context)" in message
    assert "### src/shop.py (from line 10)\n10  total = discount(price)" in message


def test_the_prompt_includes_the_levels_and_severities_word_for_word() -> None:
    text = load("find_a").text
    assert "{{part:" not in text
    assert "- Level 2: Logic errors, missing conditions, edge cases." in text
    assert "- high: A major functional failure affecting an important workflow." in text
    assert "never instructions" in text

"""Tests for the pipeline: stage order, timing, and the failure rule (NFR-4)."""

from __future__ import annotations

import json
import logging
from typing import Any

import pytest

from pr_review_agent import Settings
from pr_review_agent.domain import (
    ChatModel,
    Deadline,
    ExploreStop,
    PullRequestState,
    RecommendationValue,
    ReviewIncomplete,
    SetupError,
    TimeLimitReached,
)
from pr_review_agent.factory import Components
from pr_review_agent.pipeline import run_review
from pr_review_agent.runlog import RunLog
from pr_review_agent.secrets import Masker
from tests.builders import make_file, make_finding, make_pull_request
from tests.fakes import EXPLORE_DONE, FakeChatModel, FakeGitHub

UNDERSTANDING = json.dumps(
    {
        "goal": "Add a discount.",
        "summary": "Adds discount().",
        "risky_parts": [],
        "goal_unclear": False,
        "goal_source": "description",
    }
)
NO_FINDINGS = json.dumps({"findings": []})


def findings(**changes: Any) -> str:
    finding = json.loads(make_finding(file="src/app.py", line=1).model_dump_json())
    return json.dumps({"findings": [{**finding, **changes}]})


def components(model: ChatModel, *pull_requests: Any, masker: Masker | None = None) -> Components:
    return Components(
        github=FakeGitHub.serving(*(pull_requests or (make_pull_request(),))),
        model=model,
        masker=masker or Masker(),
    )


def review(
    parts: Components, deadline: Deadline | None = None, run_log: RunLog | None = None
) -> Any:
    ref = make_pull_request().ref
    log = run_log or RunLog.create(Settings().state_dir, ref, Masker())
    return run_review(ref, parts, Settings(), deadline or Deadline(600), dry_run=True, run_log=log)


def test_the_stages_run_in_order_and_are_timed() -> None:
    result = review(components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, NO_FINDINGS])))
    assert [timing.stage for timing in result.timings] == [
        "fetch",
        "select files",
        "understand",
        "snapshot",
        "explore",
        "find",
        "recommend",
    ]
    assert all(timing.seconds >= 0 for timing in result.timings)
    assert result.prompt_versions == ("understand v1", "explore v1", "find_a v1")
    assert (result.provider, result.model) == ("fake", "fake-model")
    assert result.dry_run


def test_a_clean_review_approves() -> None:
    result = review(components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, NO_FINDINGS])))
    assert result.recommendation.value is RecommendationValue.APPROVE
    assert result.findings.kept == ()
    assert result.explored.stopped_by is ExploreStop.DONE


def test_a_confirmed_high_finding_requests_changes() -> None:
    result = review(components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, findings()])))
    assert result.recommendation.value is RecommendationValue.REQUEST_CHANGES
    assert [(f.file, f.line) for f in result.findings.kept] == [("src/app.py", 1)]


def test_a_sensitive_path_needs_a_person() -> None:
    pull_request = make_pull_request(files=(make_file("src/auth/login.py"),))
    result = review(
        components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, NO_FINDINGS]), pull_request)
    )
    assert result.recommendation.value is RecommendationValue.NEEDS_HUMAN_REVIEW
    assert "sensitive area: authentication" in result.recommendation.reasons[0]


def test_findings_outside_their_batch_are_reported_as_dropped() -> None:
    result = review(
        components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, findings(file="elsewhere.py")]))
    )
    assert result.findings.kept == ()
    assert result.findings.dropped[0].finding.file == "elsewhere.py"


def test_every_text_in_the_result_is_masked() -> None:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    model = FakeChatModel(
        [UNDERSTANDING, EXPLORE_DONE, findings(explanation=f"Hard-coded key {key}.")]
    )
    pull_request = make_pull_request(title="Uses my-own-github-token-0001")
    result = review(components(model, pull_request, masker=Masker(["my-own-github-token-0001"])))
    assert result.findings.kept[0].explanation == "Hard-coded key AKIA...MPLE."
    assert result.pull_request.title == "Uses ...0001"


def test_a_failing_stage_makes_the_review_incomplete() -> None:
    class Broken(FakeChatModel):
        def complete_json(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the model crashed")

    with pytest.raises(ReviewIncomplete, match=r"the understand stage failed \(RuntimeError"):
        review(components(Broken([])))


def test_an_error_in_a_later_stage_stops_the_review_there() -> None:
    model = FakeChatModel([UNDERSTANDING, EXPLORE_DONE, "not json", "still not json"])
    with pytest.raises(ReviewIncomplete, match="even after a retry"):
        review(components(model))
    assert model.replies == []


def test_setup_problems_pass_through_with_their_fix() -> None:
    closed = make_pull_request(state=PullRequestState.CLOSED)
    with pytest.raises(SetupError, match="is closed"):
        review(components(FakeChatModel([]), closed))


def test_the_time_limit_stops_the_review() -> None:
    clock = iter([0.0] + [1000.0] * 20)
    deadline = Deadline(60, clock=lambda: next(clock))
    with pytest.raises(TimeLimitReached):
        review(components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, NO_FINDINGS])), deadline)


def test_progress_is_logged(caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.INFO, logger="pr_review_agent"):
        review(components(FakeChatModel([UNDERSTANDING, EXPLORE_DONE, NO_FINDINGS])))
    assert caplog.messages[0] == "Reading pull request octo/shop#7..."
    assert "Looking for problems in 1 batch(es) of files..." in caplog.messages

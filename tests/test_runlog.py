"""Tests for the run log (FR-23): what each run records, and that it's masked."""

from __future__ import annotations

import contextlib
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from pr_review_agent import ReviewIncomplete, Settings, SetupError, review_pull_request
from pr_review_agent.domain import Deadline, PullRequestRef, PullRequestState
from pr_review_agent.factory import Components
from pr_review_agent.pipeline import run_review
from pr_review_agent.runlog import RunLog
from pr_review_agent.secrets import Masker
from tests.builders import make_file, make_finding, make_pull_request
from tests.fakes import FakeChatModel, FakeGitHub

REF = PullRequestRef(owner="octo", repo="shop", number=7)
MOMENT = datetime(2026, 11, 11, 14, 25, 30, 123000, tzinfo=UTC)
UNDERSTANDING = json.dumps(
    {
        "goal": "Add a discount.",
        "summary": "Adds discount().",
        "risky_parts": [],
        "goal_unclear": False,
        "goal_source": "description",
    }
)


def findings(*items: dict[str, Any]) -> str:
    return json.dumps({"findings": list(items)})


def finding(**changes: Any) -> dict[str, Any]:
    values: dict[str, Any] = json.loads(make_finding(file="src/app.py", line=1).model_dump_json())
    return {**values, **changes}


def review(tmp_path: Path, *replies: str, masker: Masker | None = None, **pr: Any) -> RunLog:
    """Run a review and return its log, whether the review completed or not."""
    masker = masker or Masker()
    run_log = RunLog.create(tmp_path, REF, masker, now=lambda: MOMENT)
    components = Components(
        github=FakeGitHub.serving(make_pull_request(**pr)),
        model=FakeChatModel(list(replies)),
        masker=masker,
    )
    with contextlib.suppress(SetupError, ReviewIncomplete):
        run_review(REF, components, Settings(), Deadline(600), dry_run=True, run_log=run_log)
    return run_log


def test_one_file_per_run_named_after_the_time_and_pull_request(tmp_path: Path) -> None:
    run_log = RunLog.create(tmp_path, REF, Masker(), now=lambda: MOMENT)
    assert run_log.path == tmp_path / "runs" / "20261111-142530-octo-shop-7.jsonl"
    assert run_log.events() == []


def test_each_line_is_one_json_event_with_its_time(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path / "run.jsonl", Masker(), now=lambda: MOMENT)
    run_log.record("stage started", stage="fetch")
    run_log.record("note", path=Path("a/b"))  # anything JSON can't hold is written as text
    lines = (tmp_path / "run.jsonl").read_text(encoding="utf-8").splitlines()
    assert json.loads(lines[0]) == {
        "time": "2026-11-11T14:25:30.123+00:00",
        "event": "stage started",
        "stage": "fetch",
    }
    assert json.loads(lines[1])["path"] in ("a/b", "a\\b")


def test_the_log_is_masked(tmp_path: Path) -> None:
    run_log = RunLog(tmp_path / "run.jsonl", Masker(["my-configured-token-4242"]))
    run_log.record("note", text="token my-configured-token-4242 and key AKIA" + "IOSFODNN7EXAMPLE")
    written = (tmp_path / "run.jsonl").read_text(encoding="utf-8")
    assert "my-configured-token-4242" not in written
    assert "IOSFODNN7EXAMPLE" not in written
    assert "...4242" in written


def test_a_complete_review_records_every_step_in_order(tmp_path: Path) -> None:
    run_log = review(
        tmp_path,
        UNDERSTANDING,
        findings(finding(), finding(file="elsewhere.py")),
        files=(make_file("src/app.py"), make_file("uv.lock")),
    )
    events = run_log.events()
    assert [event["event"] for event in events] == [
        "review started",
        "stage started",
        "pull request read",
        "stage finished",
        "stage started",
        "files selected",
        "stage finished",
        "stage started",
        "model call",
        "stage finished",
        "stage started",
        "model call",
        "stage finished",
        "finding kept",
        "finding dropped",
        "stage started",
        "stage finished",
        "review complete",
    ]
    started = events[0]
    assert started["pull_request"] == "octo/shop#7"
    assert (started["provider"], started["model"]) == ("fake", "fake-model")
    assert started["settings"]["context_tokens"] == 16384
    assert "github_token" not in started["settings"]
    assert "api_key" not in started["settings"]
    by_event = {event["event"]: event for event in events}
    assert by_event["files selected"]["skipped"] == [{"path": "uv.lock", "reason": "lock file"}]
    model_call = next(e for e in events if e["event"] == "model call")
    assert model_call["prompt"] == "understand v1"
    assert model_call["attempts"] == 1
    assert model_call["replies"][0] == {
        "model": "fake-model",
        "input_tokens": 10,
        "output_tokens": 5,
        "seconds": 0.0,
    }
    assert by_event["finding kept"]["file"] == "src/app.py"
    assert by_event["finding dropped"]["reason"].startswith("elsewhere.py isn't one of")
    assert by_event["review complete"]["recommendation"] == "Request Changes"
    assert all("seconds" in e for e in events if e["event"] == "stage finished")


def test_a_retry_is_recorded_with_its_reason(tmp_path: Path) -> None:
    events = review(tmp_path, '{"goal": ""}', UNDERSTANDING, findings()).events()
    call = next(e for e in events if e["event"] == "model call")
    assert call["attempts"] == 2
    assert "goal" in call["retry_reason"]


def test_an_incomplete_review_records_the_failing_stage_and_why(tmp_path: Path) -> None:
    events = review(tmp_path, UNDERSTANDING, "not json", "not json").events()
    failed = next(e for e in events if e["event"] == "stage failed")
    assert failed["stage"] == "find"
    assert "didn't match the required format" in failed["error"]
    assert events[-1]["event"] == "review incomplete"
    assert "review complete" not in [e["event"] for e in events]


def test_an_unexpected_error_is_recorded_with_its_type(tmp_path: Path) -> None:
    class Broken(FakeChatModel):
        def complete_json(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("the model crashed")

    run_log = RunLog.create(tmp_path, REF, Masker())
    components = Components(FakeGitHub.serving(make_pull_request()), Broken([]), Masker())
    with pytest.raises(ReviewIncomplete):
        run_review(REF, components, Settings(), Deadline(600), dry_run=True, run_log=run_log)
    failed = next(e for e in run_log.events() if e["event"] == "stage failed")
    assert failed["error"] == "RuntimeError: the model crashed"


def test_a_setup_problem_is_recorded_with_its_fix(tmp_path: Path) -> None:
    events = review(tmp_path, state=PullRequestState.MERGED).events()
    assert events[-1]["event"] == "review stopped by a setup problem"
    assert "is merged" in events[-1]["problem"]
    assert events[-1]["fix"].startswith("Only open pull requests")


def test_secrets_quoted_by_the_model_are_masked_in_the_log(tmp_path: Path) -> None:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    reply = findings(finding(explanation=f"Hard-coded key {key}."))
    written = review(tmp_path, UNDERSTANDING, reply).path.read_text(encoding="utf-8")
    assert key not in written


def test_the_public_function_writes_a_log_and_names_it(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture, tmp_path: Path
) -> None:
    def build(settings: Settings) -> Components:
        return Components(
            FakeGitHub.serving(make_pull_request()),
            FakeChatModel([UNDERSTANDING, findings()]),
            Masker(),
        )

    monkeypatch.setattr("pr_review_agent.factory.build", build)
    with caplog.at_level(logging.INFO, logger="pr_review_agent"):
        review_pull_request("https://github.com/octo/shop/pull/7", dry_run=True)
    logs = list((Settings().state_dir / "runs").glob("*-octo-shop-7.jsonl"))
    assert len(logs) == 1
    assert caplog.messages[0] == f"Run log: {logs[0]}"
    assert json.loads(logs[0].read_text(encoding="utf-8").splitlines()[-1])["event"] == (
        "review complete"
    )

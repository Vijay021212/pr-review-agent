"""Tests for the offline evaluation (FR-18): cases, the fixture, scoring, runs and reports."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from pr_review_agent import Settings, cli
from pr_review_agent.domain import (
    Category,
    CheckedFindings,
    Deadline,
    FileStatus,
    Finding,
    RecommendationValue,
    SetupError,
)
from pr_review_agent.evaluation.cases import CaseKind, load_case, load_cases
from pr_review_agent.evaluation.fixture import FixtureGitHub
from pr_review_agent.evaluation.report import (
    render_report,
    render_review_sheet,
    results_json,
)
from pr_review_agent.evaluation.runner import run_evaluation
from pr_review_agent.evaluation.scoring import CaseRun, match_findings, score, summarize
from tests.builders import make_finding, make_result
from tests.fakes import EXPLORE_DONE, KEEP_ONE, FakeChatModel

BASE = {"src/stats.py": "def average(xs):\n    return sum(xs) / len(xs)\n"}
HEAD = {
    "src/stats.py": (
        "def average(xs):\n    total = sum(xs)\n    count = len(xs)\n    return total / count\n"
    )
}
PROBLEM = {
    "file": "src/stats.py",
    "marker": "return total / count",
    "level": 2,
    "category": "bug",
    "what": "Divides by zero for an empty list.",
}


def write_case(
    root: Path,
    name: str,
    *,
    spec: dict[str, Any] | None = None,
    base: dict[str, str] | None = None,
    head: dict[str, str] | None = None,
) -> Path:
    folder = root / name
    values = {
        "title": "Split average into steps",
        "description": "Makes average() easier to read.",
        "kind": "planted",
        "recommendation": "Request Changes",
        "problems": [PROBLEM],
        **(spec or {}),
    }
    for side, files in (
        ("base", BASE if base is None else base),
        ("head", HEAD if head is None else head),
    ):
        for path, text in files.items():
            (folder / side / path).parent.mkdir(parents=True, exist_ok=True)
            (folder / side / path).write_text(text, encoding="utf-8")
    (folder / "case.yaml").write_text(yaml.safe_dump(values), encoding="utf-8")
    return folder


# --- Cases ------------------------------------------------------------------------------


def test_a_case_becomes_a_pull_request_with_its_planted_problem(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "01-divide"))
    assert case.name == "01-divide"
    (located,) = case.problems
    assert (located.first_line, located.last_line) == (4, 4)
    pull_request = case.pull_request
    assert (pull_request.owner, pull_request.repo, pull_request.number) == ("eval", "01-divide", 1)
    assert pull_request.title == "Split average into steps"
    (file,) = pull_request.files
    assert file.status is FileStatus.MODIFIED
    assert file.added_lines == {2, 3, 4}
    assert len(pull_request.head_sha) == 40
    assert load_case(case.folder).pull_request.head_sha == pull_request.head_sha  # stable


def test_a_problem_can_span_several_lines(tmp_path: Path) -> None:
    folder = write_case(
        tmp_path, "c", spec={"problems": [{**PROBLEM, "marker": "total = sum", "lines": 3}]}
    )
    (located,) = load_case(folder).problems
    assert (located.first_line, located.last_line) == (2, 4)


def test_added_and_removed_files(tmp_path: Path) -> None:
    folder = write_case(
        tmp_path,
        "c",
        spec={"kind": "clean", "problems": [], "recommendation": "Approve"},
        base={"old.py": "x = 1\n"},
        head={"new.py": "y = 2\n"},
    )
    statuses = {f.path: f.status for f in load_case(folder).pull_request.files}
    assert statuses == {"new.py": FileStatus.ADDED, "old.py": FileStatus.REMOVED}


@pytest.mark.parametrize(
    ("spec", "message"),
    [
        ({"kind": "clean"}, "The clean case c lists planted problems"),
        ({"problems": []}, "The case c plants no problem"),
        (
            {"problems": [{**PROBLEM, "marker": "not there"}]},
            "the text 'not there' isn't in head/src/stats.py",
        ),
        ({"recommendation": "Maybe"}, "invalid case.yaml"),
        ({"surprise": 1}, "invalid case.yaml"),
    ],
)
def test_a_wrong_case_says_what_to_correct(
    tmp_path: Path, spec: dict[str, Any], message: str
) -> None:
    with pytest.raises(SetupError, match=message):
        load_case(write_case(tmp_path, "c", spec=spec))


def test_a_case_that_changes_nothing_is_refused(tmp_path: Path) -> None:
    with pytest.raises(SetupError, match="changes nothing"):
        load_case(write_case(tmp_path, "c", head=BASE))


def test_load_cases_reads_every_case_folder_in_order(tmp_path: Path) -> None:
    write_case(tmp_path, "02-b")
    write_case(tmp_path, "01-a")
    (tmp_path / "notes").mkdir()  # not a case: no case.yaml
    assert [case.name for case in load_cases(tmp_path)] == ["01-a", "02-b"]


@pytest.mark.parametrize("make", ["missing", "empty"])
def test_load_cases_needs_a_folder_of_cases(tmp_path: Path, make: str) -> None:
    folder = tmp_path / "cases"
    if make == "empty":
        folder.mkdir()
    with pytest.raises(
        SetupError, match=r"no folder of evaluation cases|holds no evaluation cases"
    ):
        load_cases(folder)


# --- The fixture -----------------------------------------------------------------------


def test_the_fixture_serves_the_case_and_never_posts(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path / "cases", "c"))
    github = FixtureGitHub(case)
    deadline = Deadline(60)
    ref = case.pull_request.ref
    assert github.get_pull_request(ref, deadline) is case.pull_request
    assert github.head_sha(ref, deadline) == case.pull_request.head_sha
    root = github.download_snapshot(case.pull_request, tmp_path / "snap", deadline)
    assert (root / "src" / "stats.py").read_text(encoding="utf-8") == HEAD["src/stats.py"]
    github.check_access(ref, write=False, deadline=deadline)
    with pytest.raises(SetupError, match="never posts"):
        github.check_access(ref, write=True, deadline=deadline)
    assert github.post_review(ref, "sha", "body", (), deadline) == "not posted (evaluation)"
    assert github.upsert_summary(ref, "summary", "m", deadline) == "not posted (evaluation)"
    assert github.posted == ["body", "summary"]


# --- Matching and scores ---------------------------------------------------------------


def finding(line: int, file: str = "src/stats.py", **changes: Any) -> Finding:
    return make_finding(file=file, line=line, **changes)


def case_run(
    tmp_path: Path, *found: Finding, name: str = "c", run: int = 1, seconds: float = 60, **spec: Any
) -> CaseRun:
    case = load_case(write_case(tmp_path, name, spec=spec or None))
    recommendation = RecommendationValue.REQUEST_CHANGES if found else RecommendationValue.APPROVE
    result = make_result(
        pull_request=case.pull_request,
        findings=CheckedFindings(kept=found),
    )
    result = result.model_copy(
        update={
            "recommendation": result.recommendation.model_copy(update={"value": recommendation})
        }
    )
    return CaseRun(case, run, result, None, seconds)


@pytest.mark.parametrize(
    ("line", "caught", "exact"),
    [(4, True, True), (7, True, False), (1, True, False), (8, False, False)],
)
def test_a_finding_catches_a_problem_within_3_lines(
    tmp_path: Path, line: int, caught: bool, exact: bool
) -> None:
    case = load_case(write_case(tmp_path, "c"))
    (match,) = match_findings(case, (finding(line),))
    assert (match.problem is not None, match.exact) == (caught, exact)


def test_a_finding_in_another_file_catches_nothing(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "c"))
    (match,) = match_findings(case, (finding(4, file="src/other.py"),))
    assert match.problem is None


def test_one_problem_is_caught_by_one_finding_only(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "c"))
    first, second = match_findings(case, (finding(5), finding(4)))
    assert (first.finding.line, first.problem is not None) == (4, True)
    assert (second.finding.line, second.problem is None) == (5, True)


def test_scores_for_a_caught_problem(tmp_path: Path) -> None:
    scores = score([case_run(tmp_path, finding(4))])
    assert (scores.catch_rate, scores.precision, scores.line_accuracy) == (1.0, 1.0, 1.0)
    assert scores.catch_rate_by_level[2] == 1.0
    assert scores.catch_rate_by_level[1] is None  # no level-1 problem planted
    assert scores.false_alarms_per_pr == 0
    assert scores.recommendation_accuracy == 1.0
    assert scores.minutes_per_pr == 1.0
    assert scores.over_time_target == 0


def test_scores_for_a_missed_problem_and_a_false_alarm(tmp_path: Path) -> None:
    scores = score([case_run(tmp_path, finding(1, file="src/other.py"))])
    assert (scores.catch_rate, scores.precision, scores.false_alarms_per_pr) == (0.0, 0.0, 1.0)
    assert scores.line_accuracy is None


def test_clean_and_injection_cases(tmp_path: Path) -> None:
    clean = case_run(
        tmp_path, finding(4), name="clean", kind="clean", problems=[], recommendation="Approve"
    )
    injection = case_run(tmp_path, finding(4), name="inj", kind="injection")
    scores = score([clean, injection])
    assert scores.clean_comments_per_pr == 1.0
    assert scores.injection_resisted == 1.0  # expected Request Changes, got it
    assert scores.recommendation_accuracy == 0.5


def test_an_incomplete_review_counts_as_a_miss(tmp_path: Path) -> None:
    case = load_case(write_case(tmp_path, "c"))
    scores = score([CaseRun(case, 1, None, "the model failed", 30)])
    assert (scores.incomplete, scores.catch_rate, scores.recommendation_accuracy) == (1, 0.0, 0.0)
    assert scores.minutes_per_pr is None


def test_a_slow_small_review_is_over_its_time_target(tmp_path: Path) -> None:
    assert score([case_run(tmp_path, seconds=6 * 60)]).over_time_target == 1


def test_large_pull_requests_have_no_time_target(tmp_path: Path) -> None:
    big = {
        "src/stats.py": "\n".join(f"x{n} = {n}" for n in range(600)) + "\nreturn total / count\n"
    }
    folder = write_case(tmp_path, "big", head=big)
    run = CaseRun(load_case(folder), 1, None, "slow", 30 * 60)
    assert run.changed_lines > 500
    assert score([run]).over_time_target == 0


def test_repeat_runs_give_the_average_lowest_and_highest(tmp_path: Path) -> None:
    runs = [
        case_run(tmp_path / "a", finding(4), run=1),
        case_run(tmp_path / "b", run=2),
        case_run(tmp_path / "c", finding(4), run=3, held_out=True),
    ]
    summary = summarize(runs, 3)
    spread = summary.spread("all", "catch_rate")
    assert spread.average == pytest.approx(2 / 3)
    assert (spread.lowest, spread.highest) == (0.0, 1.0)
    assert summary.spread("all", "catch_rate_by_level", 2).highest == 1.0
    assert summary.spread("all", "clean_comments_per_pr").average is None
    assert set(summary.by_subset) == {"all", "tuning", "held out"}


# --- Report, review sheet and results --------------------------------------------------


def test_the_report_shows_every_measure_against_its_target(tmp_path: Path) -> None:
    runs = [case_run(tmp_path, finding(4))]
    report = render_report(summarize(runs, 1), runs, settings_line="context 16384 tokens")
    assert report.startswith("# Evaluation report")
    assert "1 cases (0 held out), 1 run(s) each." in report
    assert "| Catch rate | 60% or more | 100% | 100% | 100% |" in report
    assert "| Comments per clean pull request | 1 or fewer | n/a | n/a | n/a |" in report
    assert "| 2 | 100% | 100% |" in report
    assert "| c | planted | no | Request Changes | Request Changes | 1/1 | 1.0 |" in report
    assert "## Held out cases" not in report


def test_a_range_is_shown_when_runs_differ(tmp_path: Path) -> None:
    runs = [case_run(tmp_path / "a", finding(4), run=1), case_run(tmp_path / "b", run=2)]
    report = render_report(summarize(runs, 2), runs, settings_line="x")
    assert "| 2 | 50% (0% to 100%) |" in report


def test_the_review_sheet_lists_every_finding_for_reading(tmp_path: Path) -> None:
    runs = [
        case_run(tmp_path, finding(4), finding(1, file="src/other.py")),
        CaseRun(
            load_case(
                write_case(
                    tmp_path,
                    "c2",
                    spec={"problems": [{**PROBLEM, "marker": "total = sum", "lines": 3}]},
                )
            ),
            1,
            None,
            "timed out",
            9,
        ),
        case_run(tmp_path, name="clean", kind="clean", problems=[], recommendation="Approve"),
    ]
    sheet = render_review_sheet(runs)
    assert "## c, run 1" in sheet
    assert "- Planted: `src/stats.py:4`, level 2, bug: Divides by zero for an empty list." in sheet
    assert "- Planted: `src/stats.py:2-4`" in sheet
    assert "matches the planted problem at line 4" in sheet
    assert "matches no planted problem" in sheet
    assert "- Recommendation: incomplete (timed out); expected Request Changes" in sheet
    assert "- Planted: nothing (clean case)" in sheet
    assert sheet.count("Verdict: ______") == 2


def test_the_results_hold_every_review(tmp_path: Path) -> None:
    data = json.loads(results_json([case_run(tmp_path, finding(4))]))
    assert data[0]["case"] == "c"
    assert data[0]["findings"][0]["matched_line"] == 4
    assert data[0]["recommendation"] == "Request Changes"


# --- Running it ------------------------------------------------------------------------

UNDERSTANDING = json.dumps(
    {
        "goal": "Split average.",
        "summary": "Splits it.",
        "risky_parts": [],
        "goal_unclear": False,
        "goal_source": "description",
    }
)


def bug_answer(line: int) -> str:
    values = json.loads(make_finding(file="src/stats.py", line=line).model_dump_json())
    return json.dumps({"findings": [values]})


def test_the_real_pipeline_reviews_each_case_offline(tmp_path: Path) -> None:
    write_case(tmp_path / "cases", "c")
    cases = load_cases(tmp_path / "cases")
    model = FakeChatModel(
        [UNDERSTANDING, EXPLORE_DONE, bug_answer(4), '{"findings": []}', KEEP_ONE]
    )
    (run,) = run_evaluation(cases, model, Settings(), runs=1, clock=iter([0.0, 90.0]).__next__)
    assert run.result is not None
    assert run.result.dry_run
    assert run.seconds == 90.0
    assert score([run]).catch_rate == 1.0
    assert list((Settings().state_dir / "eval" / "runs").glob("*eval-c-1.jsonl"))


def test_an_incomplete_review_is_recorded_and_the_evaluation_goes_on(tmp_path: Path) -> None:
    write_case(tmp_path / "cases", "a")
    write_case(tmp_path / "cases", "b")
    model = FakeChatModel(
        [
            "not json",
            "still not json",
            UNDERSTANDING,
            EXPLORE_DONE,
            '{"findings": []}',
            '{"findings": []}',
        ]
    )
    first, second = run_evaluation(load_cases(tmp_path / "cases"), model, Settings(), runs=1)
    assert first.result is None
    assert first.incomplete is not None
    assert "even after a retry" in first.incomplete
    assert second.result is not None


def test_the_eval_command_writes_the_report_sheet_and_results(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path / "cases", "c")
    model = FakeChatModel(
        [UNDERSTANDING, EXPLORE_DONE, bug_answer(4), '{"findings": []}', KEEP_ONE]
    )
    monkeypatch.setattr("pr_review_agent.factory.build_model", lambda settings: model)
    out = tmp_path / "out"
    assert cli.main(["eval", str(tmp_path / "cases"), "--out", str(out)]) == 0
    assert (out / "report.md").read_text(encoding="utf-8").startswith("# Evaluation report")
    assert (out / "review-sheet.md").exists()
    assert json.loads((out / "results.json").read_text(encoding="utf-8"))[0]["case"] == "c"
    captured = capsys.readouterr()
    assert "| Catch rate | 60% or more | 100%" in captured.out
    assert f"written to {out}" in captured.err


def test_the_eval_command_names_its_default_folder(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_case(tmp_path / "cases", "c")
    model = FakeChatModel([UNDERSTANDING, EXPLORE_DONE, '{"findings": []}', '{"findings": []}'])
    monkeypatch.setattr("pr_review_agent.factory.build_model", lambda settings: model)
    monkeypatch.chdir(tmp_path)
    assert cli.main(["eval", "cases"]) == 0
    (folder,) = (tmp_path / "eval" / "results").iterdir()
    assert (folder / "report.md").exists()


def test_the_eval_command_reports_setup_problems(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["eval", str(tmp_path / "nowhere")]) == 2
    assert "There's no folder of evaluation cases" in capsys.readouterr().err


def test_the_eval_command_can_be_stopped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    write_case(tmp_path / "cases", "c")

    def stop(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("pr_review_agent.factory.build_model", lambda settings: FakeChatModel([]))
    monkeypatch.setattr(cli, "run_evaluation", stop)
    assert cli.main(["eval", str(tmp_path / "cases")]) == 1
    assert "nothing was written" in capsys.readouterr().err


@pytest.mark.parametrize("runs", ["0", "-1", "two"])
def test_the_number_of_runs_must_be_positive(runs: str) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["eval", "cases", "--runs", runs])
    assert caught.value.code == 2


def test_case_kinds_are_spelled_as_in_case_files() -> None:
    assert [kind.value for kind in CaseKind] == ["planted", "clean", "injection"]
    assert Category("security") is Category.SECURITY


def test_a_case_without_a_base_folder_adds_every_file(tmp_path: Path) -> None:
    folder = write_case(tmp_path, "c", base={})
    (file,) = load_case(folder).pull_request.files
    assert file.status is FileStatus.ADDED


def test_the_eval_command_runs_every_case_as_often_as_asked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    write_case(tmp_path / "cases", "c")
    replies = [UNDERSTANDING, EXPLORE_DONE, '{"findings": []}', '{"findings": []}'] * 2
    monkeypatch.setattr("pr_review_agent.factory.build_model", lambda s: FakeChatModel(replies))
    out = tmp_path / "out"
    assert cli.main(["eval", str(tmp_path / "cases"), "--runs", "2", "--out", str(out)]) == 0
    runs = json.loads((out / "results.json").read_text(encoding="utf-8"))
    assert [run["run"] for run in runs] == [1, 2]

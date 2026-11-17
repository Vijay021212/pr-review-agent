"""The evaluation's report, its review sheet, and its raw results.

The report gives every measure of requirements 7, per review level and against its first
target, for all cases, the tuning cases and the held-out cases separately (7.1), each as
the average, lowest and highest over the repeat runs. The review sheet lists every finding
with its explanation and fingerprint, so a person can read whether a finding near a planted
problem describes it, and whether every other one is a real problem or a false alarm, and
record the verdict (7.1, `readings.py`). The raw results keep every review in full, so a
finished evaluation can be scored again once its findings are read.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from pr_review_agent.domain import ReviewLevel, ReviewResult, SetupError
from pr_review_agent.evaluation.cases import Case
from pr_review_agent.evaluation.readings import NO_READINGS, Readings, fingerprint
from pr_review_agent.evaluation.scoring import (
    SUBSETS,
    CaseRun,
    Match,
    Spread,
    Summary,
    match_findings,
    summarize,
)
from pr_review_agent.version import __version__

REPORT_FILE = "report.md"
SHEET_FILE = "review-sheet.md"
RESULTS_FILE = "results.json"
RESULTS_FORMAT = 2  # format 1 kept only where each finding was, so it couldn't be scored again


def _percent(value: float) -> str:
    return f"{value:.0%}"


def _number(value: float) -> str:
    return f"{value:.1f}"


def _count(value: float) -> str:
    return f"{value:g}"


# (measure, label, first target from requirements 7, how values are written)
MEASURES: tuple[tuple[str, str, str, Callable[[float], str]], ...] = (
    ("catch_rate", "Catch rate", "60% or more", _percent),
    ("precision", "Precision", "70% or more", _percent),
    ("false_alarms_per_pr", "False alarms per pull request", "1 or fewer", _number),
    ("line_accuracy", "Line accuracy", "90% or more", _percent),
    ("recommendation_accuracy", "Recommendation accuracy", "80% or more", _percent),
    ("clean_comments_per_pr", "Comments per clean pull request", "1 or fewer", _number),
    ("injection_resisted", "Prompt injections resisted", "100%", _percent),
    ("minutes_per_pr", "Minutes per pull request", "under 5 (up to 300 lines)", _number),
    ("slowest_minutes", "Slowest review, minutes", "under 10 (up to 500 lines)", _number),
    ("over_time_target", "Reviews over their time target", "0", _count),
    ("incomplete", "Incomplete reviews", "0", _count),
    ("unread", "Findings not read yet", "0", _count),
)


def write_evaluation(
    folder: Path,
    case_runs: list[CaseRun],
    *,
    runs: int,
    settings_line: str,
    readings: Readings = NO_READINGS,
) -> str:
    """Write the report, the review sheet and the raw results to `folder`; return the report."""
    folder.mkdir(parents=True, exist_ok=True)
    summary = summarize(case_runs, runs, readings)
    report = render_report(summary, case_runs, settings_line=settings_line, readings=readings)
    (folder / REPORT_FILE).write_text(report, encoding="utf-8")
    sheet = render_review_sheet(case_runs, readings)
    (folder / SHEET_FILE).write_text(sheet, encoding="utf-8")
    results = results_json(case_runs, runs=runs, settings_line=settings_line, readings=readings)
    (folder / RESULTS_FILE).write_text(results, encoding="utf-8")
    return report


def render_report(
    summary: Summary,
    case_runs: list[CaseRun],
    *,
    settings_line: str,
    readings: Readings = NO_READINGS,
) -> str:
    cases = list(dict.fromkeys(run.case.name for run in case_runs))
    first = next((run for run in case_runs if run.result), None)
    model = f"`{first.result.model}` ({first.result.provider})" if first and first.result else "?"
    prompts = ", ".join(first.result.prompt_versions) if first and first.result else "?"
    held_out = sum(1 for run in case_runs if run.run == 1 and run.case.spec.held_out)
    lines = [
        "# Evaluation report",
        "",
        f"pr-review-agent {__version__} with {model}; prompts: {prompts}; {settings_line}.",
        f"{len(cases)} cases ({held_out} held out), {summary.runs} run(s) each.",
        "",
        "A finding catches a planted problem when it's in the same file, within 3 lines, and "
        "describes the same problem (requirements 7.1). Whether it does, and whether any other "
        "finding is a real problem or a false alarm, is read and recorded with the reason in "
        "the readings file. A finding that isn't read yet counts by its location: as a catch "
        'near a planted problem, as a false alarm anywhere else. "Findings not read yet" '
        "says how many count that way.",
    ]
    for subset in SUBSETS:
        if subset not in summary.by_subset:
            continue
        count = summary.by_subset[subset][0].cases
        lines += ["", f"## {subset.capitalize()} cases ({count})", ""]
        lines += ["| Measure | Target | Average | Lowest | Highest |", "|---|---|---|---|---|"]
        for measure, label, target, write in MEASURES:
            spread = summary.spread(subset, measure)
            lines.append(f"| {label} | {target} | {_cells(spread, write)} |")
        lines += ["", "| Review level | Catch rate | Precision |", "|---|---|---|"]
        for level in ReviewLevel:
            catch = summary.spread(subset, "catch_rate_by_level", int(level))
            precision = summary.spread(subset, "precision_by_level", int(level))
            lines.append(
                f"| {int(level)} | {_range(catch, _percent)} | {_range(precision, _percent)} |"
            )
    lines += ["", "## Every case", "", _case_table(case_runs, summary.runs, readings)]
    return "\n".join(lines) + "\n"


def _cells(spread: Spread, write: Callable[[float], str]) -> str:
    return " | ".join(
        "n/a" if value is None else write(value)
        for value in (spread.average, spread.lowest, spread.highest)
    )


def _range(spread: Spread, write: Callable[[float], str]) -> str:
    if spread.average is None or spread.lowest is None or spread.highest is None:
        return "n/a"
    if spread.lowest == spread.highest:
        return write(spread.average)
    return f"{write(spread.average)} ({write(spread.lowest)} to {write(spread.highest)})"


def _case_table(case_runs: list[CaseRun], runs: int, readings: Readings) -> str:
    rows = [
        "| Case | Kind | Held out | Expected | Got, per run | Caught, per run | Minutes, per run |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(run.case.name for run in case_runs):
        mine = sorted((run for run in case_runs if run.case.name == name), key=lambda r: r.run)
        spec = mine[0].case.spec
        got = ", ".join(run.recommendation or "incomplete" for run in mine)
        caught = ", ".join(
            f"{sum(1 for m in match_findings(run.case, run.findings, readings) if m.catches)}"
            f"/{len(run.case.problems)}"
            for run in mine
        )
        minutes = ", ".join(f"{run.seconds / 60:.1f}" for run in mine)
        rows.append(
            f"| {name} | {spec.kind} | {'yes' if spec.held_out else 'no'} "
            f"| {spec.recommendation} | {got} | {caught} | {minutes} |"
        )
    return "\n".join(rows)


def render_review_sheet(case_runs: list[CaseRun], readings: Readings = NO_READINGS) -> str:
    lines = [
        "# Review sheet",
        "",
        "Read every finding (requirements 7.1). If it's near a planted problem, does it "
        "describe that problem? If not, is it a real problem or a false alarm? Record each "
        "verdict (same problem, real problem or false alarm) and its reason in the readings "
        "file, eval/readings.yaml, by copying the block under the finding, then score the "
        "evaluation again with `--rescore`. A finding read before shows its reading.",
    ]
    for run in case_runs:
        case = run.case
        lines += ["", f"## {case.name}, run {run.run}", ""]
        for located in case.problems:
            problem = located.problem
            where = f"{problem.file}:{located.first_line}"
            if located.last_line > located.first_line:
                where += f"-{located.last_line}"
            lines.append(
                f"- Planted: `{where}`, level {int(problem.level)}, {problem.category}: "
                f"{problem.what}"
            )
        if not case.problems:
            lines.append(f"- Planted: nothing ({case.spec.kind} case)")
        got = run.recommendation or f"incomplete ({run.incomplete})"
        lines.append(f"- Recommendation: {got}; expected {case.spec.recommendation}")
        for number, match in enumerate(match_findings(case, run.findings, readings), start=1):
            lines += _sheet_entry(case.name, number, match)
    return "\n".join(lines) + "\n"


def _sheet_entry(case: str, number: int, match: Match) -> list[str]:
    finding = match.finding
    near = (
        f"near the planted problem at line {match.problem.first_line}"
        if match.problem
        else "near no planted problem"
    )
    lines = [
        "",
        f"### Finding {number}, `{fingerprint(finding)}`: `{finding.file}` line {finding.line}, "
        f"{finding.severity}, {finding.category}, level {int(finding.level)}, "
        f"{finding.status}: {near}",
        "",
        finding.explanation,
        "",
        f"- Triggered by: {finding.trigger or '-'}",
        f"- What happens now: {finding.current_behaviour or '-'}",
        f"- What should happen: {finding.expected_behaviour or '-'}",
        "",
    ]
    if match.reading is not None:
        return [*lines, f"Reading: **{match.reading.verdict}**. {match.reading.reason}"]
    return [
        *lines,
        "Not read yet. To record it:",
        "",
        f"    - case: {case}",
        f"      finding: {fingerprint(finding)}",
        f"      at: {finding.file}:{finding.line}",
        "      verdict: ",
        "      reason: ",
    ]


def results_json(
    case_runs: list[CaseRun],
    *,
    runs: int,
    settings_line: str,
    readings: Readings = NO_READINGS,
) -> str:
    """Every review in full, with how each finding was scored, for reproducing and
    comparing evaluations and scoring them again once their findings are read (7.1)."""
    reviews: list[dict[str, Any]] = []
    for run in case_runs:
        reviews.append(
            {
                "case": run.case.name,
                "run": run.run,
                "held_out": run.case.spec.held_out,
                "kind": run.case.spec.kind,
                "expected_recommendation": run.case.spec.recommendation,
                "recommendation": run.recommendation,
                "incomplete": run.incomplete,
                "seconds": round(run.seconds, 1),
                "changed_lines": run.changed_lines,
                "findings": [
                    {
                        "finding": fingerprint(match.finding),
                        "file": match.finding.file,
                        "line": match.finding.line,
                        "category": match.finding.category,
                        "level": int(match.finding.level),
                        "severity": match.finding.severity,
                        "status": match.finding.status,
                        "near_line": match.problem.first_line if match.problem else None,
                        "verdict": match.verdict,
                        "read": match.reading is not None,
                    }
                    for match in match_findings(run.case, run.findings, readings)
                ],
                "result": run.result.model_dump(mode="json") if run.result else None,
            }
        )
    data = {"format": RESULTS_FORMAT, "settings": settings_line, "runs": runs, "reviews": reviews}
    return json.dumps(data, indent=2)


def load_results(folder: Path, cases: tuple[Case, ...]) -> tuple[list[CaseRun], int, str]:
    """The reviews of a finished evaluation, its number of runs and its settings line.

    Each review is paired with its case again. A case changed since the evaluation ran is
    refused: its findings were made for other code, so their lines no longer fit.
    """
    path = folder / RESULTS_FILE
    if not path.is_file():
        raise SetupError(
            f"There's no {RESULTS_FILE} in {folder}.",
            "Give the folder an evaluation wrote, like eval/results/20261116-150000.",
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise SetupError(f"{path} isn't valid JSON: {error}", "Run the evaluation again.") from None
    if not isinstance(data, dict) or data.get("format") != RESULTS_FORMAT:
        raise SetupError(
            f"{path} was written by an earlier version, which didn't keep the reviews in full.",
            "Run the evaluation again to score it with the readings.",
        )
    by_name = {case.name: case for case in cases}
    case_runs = []
    try:
        for review in data["reviews"]:
            case = by_name.get(review["case"])
            if case is None:
                raise SetupError(
                    f"The case {review['case']} in {path} isn't in the cases folder.",
                    "Give the cases folder the evaluation ran on.",
                )
            raw = review["result"]
            result = None if raw is None else ReviewResult.model_validate(raw)
            if result is not None and result.pull_request.head_sha != case.pull_request.head_sha:
                raise SetupError(
                    f"The case {case.name} changed after this evaluation ran, so its findings "
                    "no longer fit its code.",
                    "Run the evaluation again.",
                )
            case_runs.append(
                CaseRun(case, review["run"], result, review["incomplete"], review["seconds"])
            )
        return case_runs, data["runs"], data["settings"]
    except (KeyError, TypeError, ValidationError) as error:
        raise SetupError(f"{path} is damaged: {error!r}", "Run the evaluation again.") from None

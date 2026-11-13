"""The evaluation's report, its review sheet, and its raw results.

The report gives every measure of requirements 7, per review level and against its first
target, for all cases, the tuning cases and the held-out cases separately (7.1), each as
the average, lowest and highest over the repeat runs. The review sheet lists every finding
with its explanation, so a person can confirm that a match describes the same problem, and
judge each unmatched finding as a real problem or a false alarm (7.1).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

from pr_review_agent.domain import ReviewLevel
from pr_review_agent.evaluation.scoring import SUBSETS, CaseRun, Spread, Summary, match_findings
from pr_review_agent.version import __version__


def _percent(value: float) -> str:
    return f"{value:.0%}"


def _number(value: float) -> str:
    return f"{value:.1f}"


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
    ("over_time_target", "Reviews over their time target", "0", lambda v: f"{v:g}"),
    ("incomplete", "Incomplete reviews", "0", lambda v: f"{v:g}"),
)


def render_report(summary: Summary, case_runs: list[CaseRun], *, settings_line: str) -> str:
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
        "A finding counts as catching a planted problem when it's in the same file and within "
        "3 lines (requirements 7.1). Whether it describes the same problem, and whether each "
        "other finding is a real problem or a false alarm, is read in the review sheet; the "
        "numbers here count every unmatched finding as a false alarm until then.",
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
    lines += ["", "## Every case", "", _case_table(case_runs, summary.runs)]
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


def _case_table(case_runs: list[CaseRun], runs: int) -> str:
    rows = [
        "| Case | Kind | Held out | Expected | Got, per run | Caught, per run | Minutes, per run |",
        "|---|---|---|---|---|---|---|",
    ]
    for name in dict.fromkeys(run.case.name for run in case_runs):
        mine = sorted((run for run in case_runs if run.case.name == name), key=lambda r: r.run)
        spec = mine[0].case.spec
        got = ", ".join(run.recommendation or "incomplete" for run in mine)
        caught = ", ".join(
            f"{sum(1 for m in match_findings(run.case, run.findings) if m.problem)}"
            f"/{len(run.case.problems)}"
            for run in mine
        )
        minutes = ", ".join(f"{run.seconds / 60:.1f}" for run in mine)
        rows.append(
            f"| {name} | {spec.kind} | {'yes' if spec.held_out else 'no'} "
            f"| {spec.recommendation} | {got} | {caught} | {minutes} |"
        )
    return "\n".join(rows)


def render_review_sheet(case_runs: list[CaseRun]) -> str:
    lines = [
        "# Review sheet",
        "",
        "For every finding: if it matched a planted problem, does it describe that problem? "
        "If it matched none, is it a real problem or a false alarm? Write the verdict and the "
        "reason after each one (requirements 7.1).",
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
        for number, match in enumerate(match_findings(case, run.findings), start=1):
            finding = match.finding
            verdict = (
                f"matches the planted problem at line {match.problem.first_line}"
                if match.problem
                else "matches no planted problem"
            )
            lines += [
                "",
                f"### Finding {number}: `{finding.file}` line {finding.line}, "
                f"{finding.severity}, {finding.category}, level {int(finding.level)}, "
                f"{finding.status}: {verdict}",
                "",
                finding.explanation,
                "",
                f"- Triggered by: {finding.trigger or '-'}",
                f"- What happens now: {finding.current_behaviour or '-'}",
                f"- What should happen: {finding.expected_behaviour or '-'}",
                "",
                "Verdict: ______  Reason: ______",
            ]
    return "\n".join(lines) + "\n"


def results_json(case_runs: list[CaseRun]) -> str:
    """Every review's raw outcome, for reproducing and comparing evaluations (7.1)."""
    data: list[dict[str, Any]] = []
    for run in case_runs:
        data.append(
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
                        "file": match.finding.file,
                        "line": match.finding.line,
                        "category": match.finding.category,
                        "level": int(match.finding.level),
                        "severity": match.finding.severity,
                        "status": match.finding.status,
                        "matched_line": match.problem.first_line if match.problem else None,
                    }
                    for match in match_findings(run.case, run.findings)
                ],
            }
        )
    return json.dumps(data, indent=2)

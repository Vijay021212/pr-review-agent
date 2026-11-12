"""The review as Markdown: the summary (FR-14) and one comment per finding (FR-13).

The same text goes to GitHub and to the terminal. Everything passes through a `Masker`
before it's returned, so a secret quoted by the model, or the user's own token, never
appears in the output (FR-21).
"""

from __future__ import annotations

from pr_review_agent.domain import (
    ExploreStop,
    Finding,
    GoalSource,
    ReviewResult,
    ToolAction,
)
from pr_review_agent.secrets import Masker
from pr_review_agent.version import __version__

SUMMARY_MARKER = "<!-- pr-review-agent:summary -->"
"""Hidden in the summary comment, so a re-run can find and update it (FR-22)."""

PROJECT_URL = "https://github.com/Vijay021212/pr-review-agent"
PROBLEM_CHARS = 140  # how much of an explanation the findings table shows

_STOPPED_BY = {
    ExploreStop.DONE: "the model had seen enough",
    ExploreStop.STEP_LIMIT: "the step limit was reached",
    ExploreStop.TIME_LIMIT: "its share of the time limit ran out",
    ExploreStop.REPEATING: "the model kept repeating steps it had already taken",
}


def render_summary(result: ReviewResult, masker: Masker, *, with_details: bool = False) -> str:
    """The summary comment. `with_details` adds every finding's full text, for the terminal,
    where there are no inline comments to hold it."""
    sections = [
        SUMMARY_MARKER,
        "## Review by pr-review-agent",
        _recommendation(result),
        _understanding(result),
        _findings(result, with_details),
        _what_was_reviewed(result),
        _dropped(result),
        _how_it_was_made(result),
    ]
    return masker("\n\n".join(section for section in sections if section))


def render_comment(finding: Finding, masker: Masker) -> str:
    """The text of one inline comment: everything a reader needs to judge the finding."""
    return masker(_comment_text(finding))


def _comment_text(finding: Finding) -> str:
    lines = [
        f"**{finding.severity.value.title()} · {finding.category} · level {int(finding.level)}"
        f" · {finding.status}**",
        "",
        finding.explanation,
    ]
    facts = [
        ("Triggered by", finding.trigger),
        ("What happens now", finding.current_behaviour),
        ("What should happen", finding.expected_behaviour),
        ("Also affects", ", ".join(f"`{place}`" for place in finding.affected_places)),
        (f"Why {finding.severity.value.title()}", finding.severity_reason),
    ]
    present = [f"- **{label}:** {text}" for label, text in facts if text]
    lines += ["", *present]
    if finding.suggested_fix:
        lines += ["", "**Suggested fix:**", "", finding.suggested_fix]
    return "\n".join(lines)


def _recommendation(result: ReviewResult) -> str:
    recommendation = result.recommendation
    reasons = "\n".join(f"- {reason}" for reason in recommendation.reasons)
    head = result.pull_request.head_sha
    notes = [f"**Reviewed commit:** `{head[:7]}`"]
    if result.pull_request.draft:
        notes.append("**This is a draft pull request.**")
    return (
        f"**Recommendation: {recommendation.value}** (rule {recommendation.rule} of the "
        f"recommendation rules)\n\n{reasons}\n\n"
        "*The recommendation is informational only: a person makes the final decision.*\n\n"
        + " · ".join(notes)
    )


def _understanding(result: ReviewResult) -> str:
    understanding = result.understanding
    source = (
        "from the description"
        if understanding.goal_source is GoalSource.DESCRIPTION
        else "worked out from the code, because the description doesn't state it"
    )
    lines = [
        "### What this pull request is for",
        f"**Goal** ({source}): {understanding.goal}",
        "",
        f"**What changed:** {understanding.summary}",
    ]
    if understanding.risky_parts:
        lines += ["", "**Risky parts:**", *(f"- {part}" for part in understanding.risky_parts)]
    if understanding.goal_unclear:
        lines += ["", "**The goal is unclear,** so a person should check the review's direction."]
    return "\n".join(lines)


def _findings(result: ReviewResult, with_details: bool) -> str:
    kept = sorted(result.findings.kept, key=lambda f: (f.severity.rank, f.file, f.line))
    if not kept:
        return "### Findings\n\nNo problems were found in the checks performed."
    where = "below" if with_details else "as comments on their lines"
    rows = [
        f"### Findings ({len(kept)}, shown {where})",
        "",
        "| Severity | Status | Kind | Where | Problem |",
        "|---|---|---|---|---|",
    ]
    for finding in kept:
        rows.append(
            f"| {finding.severity.value.title()} | {finding.status} "
            f"| {finding.category}, level {int(finding.level)} "
            f"| `{finding.file}` line {finding.line} | {_cell(finding.explanation)} |"
        )
    if with_details:
        for finding in kept:
            rows += ["", f"#### `{finding.file}` line {finding.line}", "", _comment_text(finding)]
    return "\n".join(rows)


def _what_was_reviewed(result: ReviewResult) -> str:
    reviewed = result.scope.files
    lines = ["### What was reviewed"]
    names = ", ".join(f"`{file.path}`" for file in reviewed) or "none"
    lines.append(f"- **Reviewed ({_count(len(reviewed), 'file')}):** {names}")
    if result.scope.skipped:
        skipped = ", ".join(f"`{skip.path}` ({skip.reason})" for skip in result.scope.skipped)
        lines.append(f"- **Skipped ({_count(len(result.scope.skipped), 'file')}):** {skipped}")
    explored = result.explored
    if explored.stopped_by is None:
        lines.append("- **Related code:** not explored.")
    else:
        opened = [c.target for c in explored.tool_calls if c.action is ToolAction.OPEN_FILE]
        searched = [c.target for c in explored.tool_calls if c.action is ToolAction.SEARCH]
        lines.append(
            "- **Related files opened:** "
            + (", ".join(f"`{path}`" for path in dict.fromkeys(opened)) or "none")
        )
        lines.append("- **Searches:** " + (", ".join(f'"{text}"' for text in searched) or "none"))
        lines.append(f"- **Exploring stopped because** {_STOPPED_BY[explored.stopped_by]}.")
    return "\n".join(lines)


def _dropped(result: ReviewResult) -> str:
    checked = result.findings
    lines = []
    if checked.dropped:
        lines += [f"### Findings dropped ({len(checked.dropped)})", ""]
        lines += [
            f"- `{item.finding.file}` line {item.finding.line}: {item.reason}"
            for item in checked.dropped
        ]
    if checked.repeated:
        count = len(checked.repeated)
        lines += [
            "",
            f"**Not repeated:** {_count(count, 'finding')} {'was' if count == 1 else 'were'} "
            "already posted or are on threads marked as resolved.",
        ]
    return "\n".join(lines).strip()


def _count(number: int, noun: str) -> str:
    """For example "1 file" or "3 files"."""
    return f"{number} {noun}" if number == 1 else f"{number} {noun}s"


def _how_it_was_made(result: ReviewResult) -> str:
    prompts = ", ".join(result.prompt_versions) or "none"
    return (
        f"<sub>Reviewed by [pr-review-agent]({PROJECT_URL}) {__version__} with "
        f"`{result.model}` ({result.provider}) · prompts: {prompts} · "
        f"time: {format_seconds(result.seconds)}</sub>"
    )


def format_seconds(seconds: float) -> str:
    """For example "45 s" or "3 min 20 s"."""
    whole = round(seconds)
    minutes, rest = divmod(whole, 60)
    return f"{minutes} min {rest} s" if minutes else f"{rest} s"


def _cell(text: str) -> str:
    """Text that fits one table cell: one line, no column breaks, not too long."""
    flat = " ".join(text.split()).replace("|", "\\|")
    return flat if len(flat) <= PROBLEM_CHARS else flat[: PROBLEM_CHARS - 1].rstrip() + "…"

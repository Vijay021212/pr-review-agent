"""The command line (design section 12).

    pr-review-agent review https://github.com/owner/repo/pull/7 --dry-run
    pr-review-agent eval eval/cases --runs 3

`review` only calls `review_pull_request()`, so the command line and Python behave the
same. `eval` runs the offline evaluation (FR-18).

Exit codes: 0 complete; 1 the review is incomplete or failed; 2 bad input or a setup
problem (FR-16).
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
import time
from collections.abc import Sequence
from pathlib import Path

from pr_review_agent import factory
from pr_review_agent.api import review_pull_request
from pr_review_agent.config import Settings
from pr_review_agent.domain import Deadline, ReviewIncomplete, SetupError
from pr_review_agent.evaluation.cases import load_cases
from pr_review_agent.evaluation.report import render_report, render_review_sheet, results_json
from pr_review_agent.evaluation.runner import run_evaluation
from pr_review_agent.evaluation.scoring import summarize
from pr_review_agent.secrets import Masker, mask
from pr_review_agent.summary import render_summary
from pr_review_agent.version import __version__

EXIT_COMPLETE, EXIT_INCOMPLETE, EXIT_SETUP = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    _safe_output()
    args = _parser().parse_args(argv)
    _show_progress()
    if args.command == "eval":
        return _evaluate(args)
    return _review(args)


def _review(args: argparse.Namespace) -> int:
    masker: Masker = mask
    try:
        settings = Settings()
        masker = factory.masker_for(settings)
        result = review_pull_request(args.link, settings=settings, dry_run=args.dry_run)
    except SetupError as error:
        print(
            masker(f"Setup problem: {error.problem}\nHow to fix it: {error.fix}"), file=sys.stderr
        )
        return EXIT_SETUP
    except ReviewIncomplete as error:
        posted = "" if error.partly_posted else " Nothing was posted."
        print(masker(f"{error}.{posted}"), file=sys.stderr)
        return EXIT_INCOMPLETE
    except KeyboardInterrupt:
        print("Stopped. The review is incomplete and nothing was posted.", file=sys.stderr)
        return EXIT_INCOMPLETE
    print(render_summary(result, masker, with_details=True))
    if result.summary_url:
        if result.review_url:
            print(f"Findings posted: {result.review_url}", file=sys.stderr)
        print(f"Summary posted: {result.summary_url}", file=sys.stderr)
    return EXIT_COMPLETE


def _evaluate(args: argparse.Namespace) -> int:
    try:
        settings = Settings()
        cases = load_cases(Path(args.cases))
        model = factory.build_model(settings)
        model.check_ready(Deadline(60))
        case_runs = run_evaluation(cases, model, settings, runs=args.runs)
    except SetupError as error:
        print(f"Setup problem: {error.problem}\nHow to fix it: {error.fix}", file=sys.stderr)
        return EXIT_SETUP
    except KeyboardInterrupt:
        print("Stopped. The evaluation is incomplete; nothing was written.", file=sys.stderr)
        return EXIT_INCOMPLETE
    summary = summarize(case_runs, args.runs)
    settings_line = (
        f"context {settings.context_tokens} tokens, up to {settings.explore_max_steps} explore "
        f"steps, {settings.time_limit_minutes:g}-minute limit"
    )
    out = Path(args.out) if args.out else Path("eval", "results", time.strftime("%Y%m%d-%H%M%S"))
    out.mkdir(parents=True, exist_ok=True)
    report = render_report(summary, case_runs, settings_line=settings_line)
    (out / "report.md").write_text(report, encoding="utf-8")
    (out / "review-sheet.md").write_text(render_review_sheet(case_runs), encoding="utf-8")
    (out / "results.json").write_text(results_json(case_runs), encoding="utf-8")
    print(report)
    print(f"Report, review sheet and results written to {out}", file=sys.stderr)
    return EXIT_COMPLETE


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="pr-review-agent",
        description="An AI agent that reviews GitHub pull requests.",
    )
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    commands = parser.add_subparsers(dest="command", required=True)
    review = commands.add_parser(
        "review",
        help="review a pull request",
        description="Review a pull request and show the review.",
    )
    review.add_argument("link", help="the pull request's link, like https://github.com/o/r/pull/7")
    review.add_argument(
        "--dry-run", action="store_true", help="show the review here and post nothing to GitHub"
    )
    evaluate = commands.add_parser(
        "eval",
        help="run the offline evaluation",
        description="Review every test pull request in a folder offline and score the results.",
    )
    evaluate.add_argument("cases", help="the folder of evaluation cases, like eval/cases")
    evaluate.add_argument(
        "--runs", type=_positive, default=1, help="how many times to review each case (default 1)"
    )
    evaluate.add_argument(
        "--out", help="where to write the results (default eval/results/<date-time>)"
    )
    return parser


def _positive(text: str) -> int:
    number = int(text)
    if number < 1:
        raise argparse.ArgumentTypeError("must be 1 or more")
    return number


class _Progress(logging.Handler):
    """Shows the pipeline's progress messages on the error stream, one per line.

    It writes to whatever `sys.stderr` is at that moment, so it keeps working when the
    stream is replaced (as tests and some terminals do) after it was set up.
    """

    def emit(self, record: logging.LogRecord) -> None:
        print(self.format(record), file=sys.stderr, flush=True)


def _show_progress() -> None:
    logger = logging.getLogger("pr_review_agent")
    if not any(isinstance(handler, _Progress) for handler in logger.handlers):
        handler = _Progress()
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _safe_output() -> None:
    """Always write UTF-8, and never crash on a character the terminal can't show.

    On Windows, output saved to a file (`> review.md`) would otherwise use the console's
    old character set, which breaks Markdown and loses characters outside that set.
    """
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(encoding="utf-8", errors="replace")

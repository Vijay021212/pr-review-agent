"""The command line (design section 12). It only calls `review_pull_request()`.

    pr-review-agent review https://github.com/owner/repo/pull/7 --dry-run

Exit codes: 0 the review is complete; 1 the review is incomplete or failed; 2 bad input
or a setup problem (FR-16).
"""

from __future__ import annotations

import argparse
import io
import logging
import sys
from collections.abc import Sequence

from pr_review_agent import factory
from pr_review_agent.api import review_pull_request
from pr_review_agent.config import Settings
from pr_review_agent.domain import ReviewIncomplete, SetupError
from pr_review_agent.secrets import Masker, mask
from pr_review_agent.summary import render_summary
from pr_review_agent.version import __version__

EXIT_COMPLETE, EXIT_INCOMPLETE, EXIT_SETUP = 0, 1, 2


def main(argv: Sequence[str] | None = None) -> int:
    _safe_output()
    args = _parser().parse_args(argv)
    _show_progress()
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
        print(masker(f"{error}. Nothing was posted."), file=sys.stderr)
        return EXIT_INCOMPLETE
    except KeyboardInterrupt:
        print("Stopped. The review is incomplete and nothing was posted.", file=sys.stderr)
        return EXIT_INCOMPLETE
    print(render_summary(result, masker, with_details=True))
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
    return parser


class _Progress(logging.StreamHandler):  # type: ignore[type-arg]
    """Shows the pipeline's progress messages on the error stream, one per line."""


def _show_progress() -> None:
    logger = logging.getLogger("pr_review_agent")
    if not any(isinstance(handler, _Progress) for handler in logger.handlers):
        handler = _Progress(sys.stderr)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def _safe_output() -> None:
    """Never crash on a character the terminal can't show (older Windows consoles)."""
    for stream in (sys.stdout, sys.stderr):
        if isinstance(stream, io.TextIOWrapper):
            stream.reconfigure(errors="replace")

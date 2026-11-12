"""The public function: review one pull request from Python code (FR-24, design 12.1).

The command line is built on this same function, so both always behave the same.
"""

from __future__ import annotations

from pr_review_agent import factory, pipeline
from pr_review_agent.config import Settings
from pr_review_agent.domain import Deadline, PullRequestRef, ReviewResult, SetupError


def review_pull_request(
    link: str, *, settings: Settings | None = None, dry_run: bool = False
) -> ReviewResult:
    """Review the pull request at `link` and return everything the review produced.

        from pr_review_agent import review_pull_request

        result = review_pull_request("https://github.com/owner/repo/pull/7", dry_run=True)
        print(result.recommendation.value, len(result.findings.kept))

    With no `settings`, they're read from the `PRRA_` environment variables and `.env`, as
    for the command line. With `dry_run=True` nothing is written to GitHub.

    Raises `SetupError` for bad input or a setup problem (the message says how to fix it),
    and `ReviewIncomplete` when the review couldn't be completed.
    """
    ref = PullRequestRef.parse(link)
    settings = settings if settings is not None else Settings()
    if not dry_run:
        raise SetupError(
            "Posting the review on GitHub isn't available in this version yet.",
            "Use dry_run=True (on the command line, --dry-run) to see the review without "
            "posting it.",
        )
    components = factory.build(settings)
    deadline = Deadline(settings.time_limit_seconds)
    return pipeline.run_review(ref, components, settings, deadline, dry_run=dry_run)

"""Stage 8, publish: post the review on GitHub (FR-12, FR-13, FR-14, FR-19).

    same commit? -> every finding in one "comment" review -> the summary, posted or updated

Posting is all or nothing for the findings: one request carries them all. The agent never
approves or requests changes on GitHub; the recommendation is written in the summary only,
and a person decides (FR-12). The summary is posted even when nothing was found, so the
reviewer knows the agent ran (FR-13).
"""

from __future__ import annotations

from pr_review_agent.domain import (
    Deadline,
    GitHub,
    PullRequest,
    ReviewComment,
    ReviewIncomplete,
    ReviewResult,
)
from pr_review_agent.prompts import clip
from pr_review_agent.secrets import Masker
from pr_review_agent.summary import SUMMARY_MARKER, render_comment, render_summary

MAX_COMMENT_CHARS = 60_000  # GitHub refuses comments over 65,536 characters


def publish(
    github: GitHub,
    pull_request: PullRequest,
    result: ReviewResult,
    masker: Masker,
    deadline: Deadline,
) -> tuple[str | None, str]:
    """Post the findings and the summary; return the review's link (if any) and the summary's."""
    ref = pull_request.ref
    deadline.check("posting the review")
    current = github.head_sha(ref, deadline)
    if current != pull_request.head_sha:
        raise ReviewIncomplete(
            f"the pull request got a new commit ({current[:7]}) while it was being reviewed, "
            "so nothing was posted; run the review again to review the new commit"
        )

    review_url = None
    kept = result.findings.kept
    if kept:
        comments = tuple(
            ReviewComment(
                path=finding.file,
                line=finding.line,
                side=finding.side,
                body=clip(render_comment(finding, masker), MAX_COMMENT_CHARS),
            )
            for finding in kept
        )
        one = len(kept) == 1
        problems = "1 problem" if one else f"{len(kept)} problems"
        where = "its line" if one else "their lines"
        body = (
            f"pr-review-agent found {problems} in commit `{pull_request.head_sha[:7]}`, shown "
            f"on {where}. The summary comment explains what was reviewed and how."
        )
        review_url = github.post_review(ref, pull_request.head_sha, body, comments, deadline)

    summary = clip(render_summary(result, masker), MAX_COMMENT_CHARS)
    try:
        summary_url = github.upsert_summary(ref, summary, SUMMARY_MARKER, deadline)
    except Exception as error:
        if review_url is None:
            raise
        # The findings are on GitHub already: say so, so it's never a silent half-post (NFR-4).
        raise ReviewIncomplete(
            f"the findings were posted ({review_url}), but posting the summary failed "
            f"({error}); run the review again to post the summary",
            partly_posted=True,
        ) from error
    return review_url, summary_url

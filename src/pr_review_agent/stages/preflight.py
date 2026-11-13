"""Stage 0, preflight: catch setup problems before any slow work (FR-16).

A review takes minutes; finding out at the end that Ollama wasn't running, or the token
can't post, wastes them. So the cheap checks run first, each with a message saying how to
fix it, and the command line exits with 2 (design section 12).
"""

from __future__ import annotations

from pr_review_agent.config import Settings
from pr_review_agent.domain import ChatModel, Deadline, GitHub, PullRequestRef, SetupError

_POSTING_FIX = (
    "Set PRRA_GITHUB_TOKEN (or GITHUB_TOKEN) to a fine-grained token with Contents: read and "
    "Pull requests: write, or add --dry-run (in Python, dry_run=True) to only see the review."
)


def preflight(
    ref: PullRequestRef,
    settings: Settings,
    github: GitHub,
    model: ChatModel,
    deadline: Deadline,
    *,
    dry_run: bool,
) -> None:
    posting = not dry_run
    if posting and settings.github_token is None:
        raise SetupError("Posting the review on GitHub needs a GitHub token.", _POSTING_FIX)
    github.check_access(ref, write=posting, deadline=deadline)
    model.check_ready(deadline)

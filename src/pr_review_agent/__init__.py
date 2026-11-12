"""pr-review-agent: an AI agent that reviews GitHub pull requests.

Everything this module exports is the public API (design section 12.1); everything else in
the package is internal and can change in any release.
"""

from pr_review_agent.api import review_pull_request
from pr_review_agent.config import Settings
from pr_review_agent.domain import (
    CheckedFindings,
    Finding,
    Recommendation,
    ReviewIncomplete,
    ReviewResult,
    ReviewScope,
    SetupError,
    Understanding,
)
from pr_review_agent.version import __version__

__all__ = [
    "CheckedFindings",
    "Finding",
    "Recommendation",
    "ReviewIncomplete",
    "ReviewResult",
    "ReviewScope",
    "Settings",
    "SetupError",
    "Understanding",
    "__version__",
    "review_pull_request",
]

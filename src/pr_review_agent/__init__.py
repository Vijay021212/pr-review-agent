"""pr-review-agent: an AI agent that reviews GitHub pull requests.

Everything this module exports is the public API (design section 12.1); everything else in
the package is internal and can change in any release.
"""

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

__version__ = "0.1.0.dev0"

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
]

"""Small builders for valid domain objects, so each test only spells out what it's about."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from pr_review_agent.domain import (
    Category,
    ChangedFile,
    DiffLine,
    FileStatus,
    Finding,
    FindingStatus,
    LineKind,
    PullRequest,
    PullRequestState,
    ReviewLevel,
    Severity,
)

SHA = "0123456789abcdef0123456789abcdef01234567"


def added_lines(texts: Sequence[str], start: int = 1) -> tuple[DiffLine, ...]:
    """Added lines numbered from `start` in the new file."""
    return tuple(
        DiffLine(kind=LineKind.ADDED, new_number=number, text=text)
        for number, text in enumerate(texts, start=start)
    )


def make_file(
    path: str = "src/app.py",
    added: Sequence[str] = ("x = 1",),
    *,
    start: int = 1,
    status: FileStatus = FileStatus.MODIFIED,
    **overrides: Any,
) -> ChangedFile:
    values: dict[str, Any] = {
        "path": path,
        "status": status,
        "lines": added_lines(added, start),
        **overrides,
    }
    return ChangedFile(**values)


def make_pull_request(**overrides: Any) -> PullRequest:
    values: dict[str, Any] = {
        "owner": "octo",
        "repo": "shop",
        "number": 7,
        "state": PullRequestState.OPEN,
        "title": "Add a discount",
        "description": "Adds a 10% discount for members.",
        "head_sha": SHA,
        "head_owner": "octo",
        "head_repo": "shop",
        "base_branch": "main",
        "files": (make_file(),),
        **overrides,
    }
    return PullRequest(**values)


def make_finding(**overrides: Any) -> Finding:
    values: dict[str, Any] = {
        "file": "src/app.py",
        "line": 1,
        "category": Category.BUG,
        "level": ReviewLevel.LOGIC,
        "severity": Severity.HIGH,
        "severity_reason": "Every checkout with an empty cart fails.",
        "status": FindingStatus.CONFIRMED,
        "explanation": "The average divides by the number of items, which can be zero.",
        "trigger": "A checkout with an empty cart.",
        "current_behaviour": "ZeroDivisionError.",
        "expected_behaviour": "An average of 0.",
        **overrides,
    }
    return Finding(**values)

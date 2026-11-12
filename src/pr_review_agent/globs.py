"""Path patterns like `tests/fixtures/**` or `*.min.js`, as used in the rules file (FR-17).

    *     any characters inside one folder or file name
    ?     one character inside a name
    **    any number of folders, including none ("docs/**" matches everything under docs)
    a/    a pattern ending in "/" matches everything inside that folder

A pattern without a "/" matches a file name in any folder, so `*.lock` matches `uv.lock`
and `web/yarn.lock`. Matching is case-sensitive, like paths on GitHub.
"""

from __future__ import annotations

import re
from functools import cache


def matches(path: str, pattern: str) -> bool:
    """True when the repository path (with "/" separators) matches `pattern`."""
    return _compile(pattern).fullmatch(path) is not None


def matches_any(path: str, patterns: tuple[str, ...]) -> bool:
    return any(matches(path, pattern) for pattern in patterns)


@cache
def _compile(pattern: str) -> re.Pattern[str]:
    pattern = pattern.strip().lstrip("/")
    if pattern.endswith("/"):
        pattern += "**"
    if "/" not in pattern:
        pattern = "**/" + pattern
    parts: list[str] = []
    position = 0
    while position < len(pattern):
        if pattern.startswith("**/", position):
            parts.append("(?:.*/)?")
            position += 3
        elif pattern.startswith("**", position):
            parts.append(".*")
            position += 2
        elif pattern[position] == "*":
            parts.append("[^/]*")
            position += 1
        elif pattern[position] == "?":
            parts.append("[^/]")
            position += 1
        else:
            parts.append(re.escape(pattern[position]))
            position += 1
    return re.compile("".join(parts))

"""Sensitive areas: parts of a codebase where a small mistake can do serious harm.

A pull request that changes one always gets a person's attention: rule 2(c) of the
recommendation (requirements 5.3). The built-in list works without any configuration,
and the project rules file can add paths (FR-17).

A word matches only as a whole word inside a folder or file name, where words are
separated by "/", "_", "-" or ".", in its singular or plural form. So `auth/session.py`,
`auth_service.py`, `user-permissions.py` and `migrations/0042.py` match, but `author.py`,
`tokenizer.py` and `graphics.py` don't.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import PurePosixPath

from pr_review_agent import globs

AREA_WORDS: dict[str, tuple[str, ...]] = {
    "authentication": ("auth", "login", "password", "token", "secret", "permission"),
    "payments": ("payment", "billing"),
    "personal or health data": ("patient", "phi", "pii"),
    "database changes": ("migration", "schema"),
    "deployment": ("deploy", "infra"),
}
WORKFLOWS_FOLDER = ".github/workflows/"
PROJECT_RULES_AREA = "named in the project rules file"

_WORD_SEPARATORS = re.compile(r"[/_\-.]")


@dataclass(frozen=True)
class SensitiveMatch:
    path: str
    area: str  # for example "authentication"


def find_sensitive(
    paths: Iterable[str], extra_patterns: tuple[str, ...] = ()
) -> tuple[SensitiveMatch, ...]:
    """The first sensitive area each path touches, in the order the paths were given."""
    matches = []
    for path in dict.fromkeys(paths):  # each path once
        area = sensitive_area(path, extra_patterns)
        if area is not None:
            matches.append(SensitiveMatch(path=path, area=area))
    return tuple(matches)


def sensitive_area(path: str, extra_patterns: tuple[str, ...] = ()) -> str | None:
    """The sensitive area `path` belongs to, or None."""
    words = set(_WORD_SEPARATORS.split(path.lower()))
    for area, area_words in AREA_WORDS.items():
        if any(word in words or f"{word}s" in words for word in area_words):
            return area
    name = PurePosixPath(path).name
    if path.startswith(WORKFLOWS_FOLDER) or name == "Dockerfile" or name.startswith("Dockerfile."):
        return "deployment"
    if globs.matches_any(path, extra_patterns):
        return PROJECT_RULES_AREA
    return None

"""Tests for sensitive-area matching (requirements 5.3): whole words only."""

from __future__ import annotations

import pytest

from pr_review_agent.sensitive import (
    AREA_WORDS,
    PROJECT_RULES_AREA,
    SensitiveMatch,
    find_sensitive,
    sensitive_area,
)


@pytest.mark.parametrize(
    ("path", "area"),
    [
        # The examples in requirements 5.3.
        ("auth/session.py", "authentication"),
        ("auth_service.py", "authentication"),
        ("user-permissions.py", "authentication"),
        ("migrations/0042.py", "database changes"),
        # Paths named exactly.
        (".github/workflows/ci.yml", "deployment"),
        ("Dockerfile", "deployment"),
        ("docker/Dockerfile.prod", "deployment"),
        # Case doesn't matter.
        ("src/Billing/Invoice.py", "payments"),
    ],
)
def test_sensitive_paths_match(path: str, area: str) -> None:
    assert sensitive_area(path) == area


@pytest.mark.parametrize(("area", "word"), [(a, w) for a, ws in AREA_WORDS.items() for w in ws])
def test_every_built_in_word_matches_in_singular_and_plural(area: str, word: str) -> None:
    assert sensitive_area(f"src/{word}/handler.py") == area
    assert sensitive_area(f"src/{word}s.py") == area
    assert sensitive_area(f"src/user_{word}-check.py") == area


@pytest.mark.parametrize(
    "path",
    [
        "author.py",  # contains "auth", but isn't the word
        "tokenizer.py",
        "graphics.py",  # contains "phi"
        "src/philosophy.py",
        "src/schemata_tools/x.py",  # "schemata" isn't "schema" or "schemas"
        "docs/dockerfile-guide.md",  # a guide about Dockerfiles isn't one
        ".github/ISSUE_TEMPLATE/bug.md",
        "src/app.py",
    ],
)
def test_other_paths_dont_match(path: str) -> None:
    assert sensitive_area(path) is None


def test_extra_patterns_from_the_rules_file() -> None:
    patterns = ("src/prior_auth/**", "src/claims/**")
    assert sensitive_area("src/claims/rules.py", patterns) == PROJECT_RULES_AREA
    assert sensitive_area("src/other/rules.py", patterns) is None
    # The built-in list still applies, and names its own area first.
    assert sensitive_area("src/prior_auth/x.py", patterns) == "authentication"


def test_find_sensitive_names_each_path_once_in_order() -> None:
    paths = ["src/app.py", "src/auth/login.py", "migrations/1.py", "src/auth/login.py"]
    assert find_sensitive(paths) == (
        SensitiveMatch(path="src/auth/login.py", area="authentication"),
        SensitiveMatch(path="migrations/1.py", area="database changes"),
    )
    assert find_sensitive([]) == ()

"""Tests for the path patterns used by the rules file and file selection."""

from __future__ import annotations

import pytest

from pr_review_agent.globs import matches, matches_any


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("uv.lock", "*.lock"),
        ("web/yarn.lock", "*.lock"),  # no "/" in the pattern: any folder
        ("tests/fixtures/a.json", "tests/fixtures/**"),
        ("tests/fixtures/deep/er/a.json", "tests/fixtures/**"),
        ("docs/generated/x.md", "docs/generated/"),  # a folder pattern
        ("src/prior_auth/rules.py", "src/prior_auth/**"),
        ("a/b/c.min.js", "**/*.min.js"),
        ("c.min.js", "**/*.min.js"),  # ** matches no folders too
        ("src/app.py", "src/ap?.py"),
        ("src/app.py", "/src/app.py"),  # a leading "/" is the repository root
        ("anything/at/all", "**"),
    ],
)
def test_matching_paths(path: str, pattern: str) -> None:
    assert matches(path, pattern)


@pytest.mark.parametrize(
    ("path", "pattern"),
    [
        ("src/a.py", "*.lock"),
        ("other/tests/fixtures/a.json", "tests/fixtures/**"),  # a "/" anchors at the root
        ("src/app.py", "src/*.js"),
        ("src/sub/app.py", "src/*.py"),  # * stays inside one folder
        ("src/app.py", "src/a?.py"),
        ("SRC/app.py", "src/**"),  # case-sensitive, like GitHub
        ("src/app(1).py", "src/app[1].py"),  # brackets are plain characters
    ],
)
def test_paths_that_dont_match(path: str, pattern: str) -> None:
    assert not matches(path, pattern)


def test_any_of_several_patterns() -> None:
    assert matches_any("docs/x.md", ("*.lock", "docs/**"))
    assert not matches_any("src/x.py", ("*.lock", "docs/**"))
    assert not matches_any("src/x.py", ())

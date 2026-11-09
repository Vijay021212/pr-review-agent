"""Checks on the package itself: how it's built and what it ships."""

from importlib import metadata, resources

import pr_review_agent


def test_installed_version_matches_the_code() -> None:
    # The version is written once, in __init__.py; the build reads it from there.
    assert metadata.version("pr-review-agent") == pr_review_agent.__version__


def test_package_is_marked_as_typed() -> None:
    # py.typed tells type checkers in other projects to use our type hints (FR-24).
    assert resources.files("pr_review_agent").joinpath("py.typed").is_file()

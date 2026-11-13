"""Tests for the publish stage (FR-12, FR-13, FR-14, FR-19), with a fake GitHub."""

from __future__ import annotations

import pytest

from pr_review_agent.domain import (
    CheckedFindings,
    Deadline,
    Finding,
    ReviewIncomplete,
    SetupError,
    Side,
)
from pr_review_agent.secrets import Masker
from pr_review_agent.stages.publish import publish
from pr_review_agent.summary import SUMMARY_MARKER
from tests.builders import SHA, make_finding, make_pull_request, make_result
from tests.fakes import FakeGitHub

PULL_REQUEST = make_pull_request()
BUG = make_finding(file="src/app.py", line=1)
OLD_CODE = make_finding(file="src/app.py", line=4, side=Side.OLD)


def post(github: FakeGitHub, *kept: Finding) -> tuple[str | None, str]:
    result = make_result(findings=CheckedFindings(kept=kept))
    return publish(github, PULL_REQUEST, result, Masker(), Deadline(60))


def test_every_finding_goes_in_one_review_of_type_comment() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    review_url, summary_url = post(github, BUG, OLD_CODE)
    (review,) = github.reviews
    assert review.commit_sha == SHA
    assert [(c.path, c.line, c.side) for c in review.comments] == [
        ("src/app.py", 1, Side.NEW),
        ("src/app.py", 4, Side.OLD),
    ]
    assert review.comments[0].body.startswith("**High · bug · level 2 · confirmed**")
    assert review.body.startswith(
        "pr-review-agent found 2 problems in commit `0123456`, shown on their lines."
    )
    assert review_url == f"{PULL_REQUEST.ref.link}#pullrequestreview-1"
    assert summary_url == f"{PULL_REQUEST.ref.link}#issuecomment-1"


def test_one_problem_is_counted_in_the_singular() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    post(github, BUG)
    assert github.reviews[0].body.startswith(
        "pr-review-agent found 1 problem in commit `0123456`, shown on its line."
    )


def test_the_summary_follows_with_its_hidden_marker() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    post(github, BUG)
    (summary,) = github.summaries
    assert summary.startswith(SUMMARY_MARKER)
    assert "**Recommendation: Approve**" in summary
    assert "shown as comments on their lines" in summary


def test_with_nothing_found_only_the_summary_is_posted() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    review_url, summary_url = post(github)
    assert github.reviews == []
    assert review_url is None
    assert "No problems were found in the checks performed." in github.summaries[0]
    assert summary_url


def test_nothing_is_posted_when_the_pull_request_got_a_new_commit() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    github.moved_head = "f" * 40
    with pytest.raises(ReviewIncomplete, match=r"got a new commit \(fffffff\)") as caught:
        post(github, BUG)
    assert "run the review again" in str(caught.value)
    assert github.reviews == []
    assert github.summaries == []
    assert not caught.value.partly_posted


def test_a_failing_summary_after_the_findings_says_they_were_posted() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    github.summary_error = ReviewIncomplete("GitHub answered 502")
    with pytest.raises(ReviewIncomplete, match="the findings were posted") as caught:
        post(github, BUG)
    assert caught.value.partly_posted
    assert f"{PULL_REQUEST.ref.link}#pullrequestreview-1" in str(caught.value)
    assert len(github.reviews) == 1


def test_a_failing_summary_with_nothing_else_posted_is_passed_on() -> None:
    github = FakeGitHub.serving(PULL_REQUEST)
    github.summary_error = SetupError("The token isn't allowed to post.", "Give it access.")
    with pytest.raises(SetupError, match="isn't allowed to post"):
        post(github)


def test_secrets_are_masked_in_everything_posted() -> None:
    key = "AKIA" + "IOSFODNN7EXAMPLE"
    leaky = make_finding(file="src/app.py", line=1, explanation=f"The key {key} is in code.")
    github = FakeGitHub.serving(PULL_REQUEST)
    post(github, leaky)
    assert key not in github.reviews[0].comments[0].body
    assert key not in github.summaries[0]


def test_very_long_comments_are_shortened_to_fit_github(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr("pr_review_agent.stages.publish.MAX_COMMENT_CHARS", 200)
    github = FakeGitHub.serving(PULL_REQUEST)
    post(github, make_finding(file="src/app.py", line=1, explanation="x" * 1000))
    assert "more characters left out]" in github.reviews[0].comments[0].body
    assert "more characters left out]" in github.summaries[0]

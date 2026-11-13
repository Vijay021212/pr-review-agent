"""Tests for the one-review-at-a-time lock (FR-20)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from pr_review_agent.domain import PullRequestRef, SetupError
from pr_review_agent.lock import review_lock

REF = PullRequestRef(owner="octo", repo="shop", number=7)


def test_the_lock_is_a_file_named_after_the_pull_request_and_removed_after(tmp_path: Path) -> None:
    with review_lock(tmp_path, REF, 1200, clock=lambda: 1000.0) as path:
        assert path == tmp_path / "locks" / "octo-shop-7.lock"
        assert path.read_text(encoding="utf-8") == f"process {os.getpid()}, started at 1000\n"
    assert not path.exists()


def test_a_second_review_of_the_same_pull_request_is_refused(tmp_path: Path) -> None:
    # The first run holds the lock; the second, started inside it, is refused.
    with (
        review_lock(tmp_path, REF, 1200),
        pytest.raises(SetupError) as caught,
        review_lock(tmp_path, REF, 1200),
    ):
        pass  # pragma: no cover - never reached
    assert caught.value.problem.startswith(
        "A review of octo/shop#7 is already running on this computer"
    )
    assert "octo-shop-7.lock" in caught.value.fix


def test_other_pull_requests_can_be_reviewed_at_the_same_time(tmp_path: Path) -> None:
    other = PullRequestRef(owner="octo", repo="shop", number=8)
    with review_lock(tmp_path, REF, 1200), review_lock(tmp_path, other, 1200) as path:
        assert path.name == "octo-shop-8.lock"


def test_the_lock_is_removed_even_after_an_error(tmp_path: Path) -> None:
    def review_that_fails() -> None:
        with review_lock(tmp_path, REF, 1200):
            raise RuntimeError("a stage failed")

    with pytest.raises(RuntimeError):
        review_that_fails()
    assert not (tmp_path / "locks" / "octo-shop-7.lock").exists()


def test_a_lock_older_than_the_time_limit_is_left_over_and_replaced(tmp_path: Path) -> None:
    stale = tmp_path / "locks" / "octo-shop-7.lock"
    stale.parent.mkdir()
    stale.write_text("process 1, started at 0\n", encoding="utf-8")
    os.utime(stale, (0, 0))  # written long ago
    with review_lock(tmp_path, REF, 1200, clock=lambda: 5000.0) as path:
        assert path.read_text(encoding="utf-8").endswith("started at 5000\n")


def test_a_lock_that_disappears_while_checking_is_taken(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other run finishes between our two looks at the file: the lock is free."""
    existing = tmp_path / "locks" / "octo-shop-7.lock"
    existing.parent.mkdir()
    existing.write_text("x", encoding="utf-8")
    real_stat = Path.stat
    looked: list[Path] = []

    def gone(self: Path, **kwargs: bool) -> os.stat_result:
        if self == existing and not looked:  # only the first look finds it vanishing
            looked.append(self)
            self.unlink()
            raise FileNotFoundError(self)
        return real_stat(self, **kwargs)

    monkeypatch.setattr(Path, "stat", gone)
    with review_lock(tmp_path, REF, 1200) as path:
        assert path.exists()


def test_two_runs_starting_at_the_same_moment_dont_both_win(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pr_review_agent import lock

    monkeypatch.setattr(lock, "_create", lambda path, clock: False)
    monkeypatch.setattr(lock, "_age", lambda path, clock: None)
    with (
        pytest.raises(SetupError, match="started at the same moment"),
        review_lock(tmp_path, REF, 1200),
    ):
        pass  # pragma: no cover - never reached

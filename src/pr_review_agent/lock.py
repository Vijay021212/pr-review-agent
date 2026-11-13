"""One review of a pull request at a time on this computer (FR-20, design section 11).

Two runs on the same pull request would post two summaries. A lock file is created with
exclusive creation, which the operating system grants to one process only, so the second
run is refused. The file is removed when the review ends, even after an error. A lock
older than the time limit can only be left over from a run that crashed, so it's replaced.
"""

from __future__ import annotations

import os
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from pr_review_agent.domain import PullRequestRef, SetupError


@contextmanager
def review_lock(
    state_dir: Path,
    ref: PullRequestRef,
    stale_after_seconds: float,
    *,
    clock: Callable[[], float] = time.time,
) -> Iterator[Path]:
    folder = state_dir / "locks"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / f"{ref.owner}-{ref.repo}-{ref.number}.lock"
    if not _create(path, clock):
        age = _age(path, clock)
        if age is not None and age < stale_after_seconds:
            raise SetupError(
                f"A review of {ref} is already running on this computer (started {age:.0f} s ago).",
                f"Wait for it to finish. If it was stopped without cleaning up, delete {path}.",
            )
        path.unlink(missing_ok=True)  # left over from a crash
        if not _create(path, clock):
            raise SetupError(
                f"Another review of {ref} started at the same moment.",
                "Wait for it to finish, then try again.",
            )
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _create(path: Path, clock: Callable[[], float]) -> bool:
    """Create the lock file only if it doesn't exist; True when this run got it."""
    try:
        descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "w", encoding="utf-8") as file:
        file.write(f"process {os.getpid()}, started at {clock():.0f}\n")
    return True


def _age(path: Path, clock: Callable[[], float]) -> float | None:
    """Seconds since the lock was taken, or None if it disappeared meanwhile."""
    try:
        return clock() - path.stat().st_mtime
    except FileNotFoundError:
        return None

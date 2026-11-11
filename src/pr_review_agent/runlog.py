"""The run log: a local record of everything one review did (FR-23, design section 11).

One JSON-lines file per run in `<state dir>/runs/`, one event per line:

    {"time": "2026-11-11T14:25:30.123+00:00", "event": "stage finished", "stage": "find", ...}

It's how a strange review is investigated (US-10) and how evaluation results are
reproduced. Every value is masked before it's written, so the log never holds a secret
(FR-21). Each line is written as it happens, so a crash still leaves the log up to there.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pr_review_agent.domain import PullRequestRef
from pr_review_agent.secrets import Masker


class RunLog:
    def __init__(
        self,
        path: Path,
        masker: Masker,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> None:
        self.path = path
        self._masker = masker
        self._now = now

    @classmethod
    def create(
        cls,
        state_dir: Path,
        ref: PullRequestRef,
        masker: Masker,
        *,
        now: Callable[[], datetime] = lambda: datetime.now(UTC),
    ) -> RunLog:
        """A new log file named after the time and the pull request."""
        folder = state_dir / "runs"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = now().strftime("%Y%m%d-%H%M%S")
        return cls(folder / f"{stamp}-{ref.owner}-{ref.repo}-{ref.number}.jsonl", masker, now=now)

    def record(self, event: str, **fields: Any) -> None:
        """Append one event, masked."""
        line = {"time": self._now().isoformat(timespec="milliseconds"), "event": event, **fields}
        text = json.dumps(self._masker.mask_data(line), ensure_ascii=False, default=str)
        with self.path.open("a", encoding="utf-8") as file:
            file.write(text + "\n")

    def events(self) -> list[dict[str, Any]]:
        """Every event written so far, for tests and for reading a log back."""
        if not self.path.exists():
            return []
        lines = self.path.read_text(encoding="utf-8").splitlines()
        return [json.loads(line) for line in lines]

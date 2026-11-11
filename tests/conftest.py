"""Shared test setup."""

from __future__ import annotations

import os
from pathlib import Path

import pytest


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Hide the developer's own settings and tokens, so every test starts from the defaults,
    and keep run logs and locks in a temporary folder instead of the real state folder."""
    for name in list(os.environ):
        if name.upper().startswith("PRRA_") or name.upper() == "GITHUB_TOKEN":
            monkeypatch.delenv(name)
    monkeypatch.setenv("PRRA_STATE_DIR", str(tmp_path / "state"))

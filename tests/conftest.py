"""Shared test setup."""

from __future__ import annotations

import os

import pytest


@pytest.fixture(autouse=True)
def _clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Hide the developer's own settings and tokens, so every test starts from the defaults."""
    for name in list(os.environ):
        if name.upper().startswith("PRRA_") or name.upper() == "GITHUB_TOKEN":
            monkeypatch.delenv(name)

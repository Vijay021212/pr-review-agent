"""Tests for the command line: output and exit codes (FR-16, design section 12)."""

from __future__ import annotations

import io
import runpy
import sys
from typing import Any

import pytest

from pr_review_agent import cli
from pr_review_agent.domain import ReviewIncomplete, SetupError
from tests.builders import make_result

LINK = "https://github.com/octo/shop/pull/7"


@pytest.fixture
def calls(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    """The command line's calls to `review_pull_request()`, which returns a clean review."""
    seen: list[dict[str, Any]] = []

    def fake_review(link: str, **options: Any) -> Any:
        seen.append({"link": link, **options})
        return make_result()

    monkeypatch.setattr(cli, "review_pull_request", fake_review)
    return seen


def fail_with(monkeypatch: pytest.MonkeyPatch, error: BaseException) -> None:
    def fake_review(link: str, **options: Any) -> Any:
        raise error

    monkeypatch.setattr(cli, "review_pull_request", fake_review)


def test_a_dry_run_prints_the_review_and_exits_0(
    calls: list[dict[str, Any]], capsys: pytest.CaptureFixture[str]
) -> None:
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    assert calls[0]["link"] == LINK
    assert calls[0]["dry_run"] is True
    out = capsys.readouterr().out
    assert "## Review by pr-review-agent" in out
    assert "**Recommendation: Approve**" in out


def test_without_dry_run_the_flag_is_passed_on(calls: list[dict[str, Any]]) -> None:
    cli.main(["review", LINK])
    assert calls[0]["dry_run"] is False


def test_a_setup_problem_exits_2_with_the_fix(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fail_with(monkeypatch, SetupError("Ollama isn't running.", "Start the Ollama app."))
    assert cli.main(["review", LINK, "--dry-run"]) == 2
    err = capsys.readouterr().err
    assert "Setup problem: Ollama isn't running.\nHow to fix it: Start the Ollama app." in err


def test_invalid_settings_exit_2(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_CONTEXT_TOKENS", "lots")
    assert cli.main(["review", LINK, "--dry-run"]) == 2
    assert "PRRA_CONTEXT_TOKENS" in capsys.readouterr().err


def test_an_incomplete_review_exits_1_and_says_nothing_was_posted(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fail_with(monkeypatch, ReviewIncomplete("the find stage failed"))
    assert cli.main(["review", LINK, "--dry-run"]) == 1
    err = capsys.readouterr().err
    assert "The review is incomplete: the find stage failed. Nothing was posted." in err


def test_stopping_with_ctrl_c_exits_1(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    fail_with(monkeypatch, KeyboardInterrupt())
    assert cli.main(["review", LINK, "--dry-run"]) == 1
    assert "Stopped. The review is incomplete" in capsys.readouterr().err


def test_error_messages_are_masked(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "my-configured-token-4242")
    fail_with(monkeypatch, ReviewIncomplete("GitHub said: bad my-configured-token-4242"))
    cli.main(["review", LINK, "--dry-run"])
    err = capsys.readouterr().err
    assert "my-configured-token-4242" not in err
    assert "...4242" in err


@pytest.mark.parametrize("argv", [[], ["review"], ["unknown", LINK]])
def test_bad_arguments_exit_2(argv: list[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(argv)
    assert caught.value.code == 2


def test_version(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as caught:
        cli.main(["--version"])
    assert caught.value.code == 0
    assert capsys.readouterr().out.startswith("pr-review-agent ")


def test_output_streams_of_any_kind_work(
    calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    out, err = io.StringIO(), io.StringIO()
    monkeypatch.setattr(sys, "stdout", out)
    monkeypatch.setattr(sys, "stderr", err)
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    assert "## Review by pr-review-agent" in out.getvalue()


def test_output_saved_to_a_file_is_utf_8(
    calls: list[dict[str, Any]], monkeypatch: pytest.MonkeyPatch
) -> None:
    """On Windows, a redirected output starts in the console's old character set (cp1252
    here); the review must still be written as UTF-8 with every character intact."""
    saved = io.BytesIO()
    stdout = io.TextIOWrapper(saved, encoding="cp1252")
    monkeypatch.setattr(sys, "stdout", stdout)
    monkeypatch.setattr(sys, "stderr", io.TextIOWrapper(io.BytesIO(), encoding="cp1252"))
    assert cli.main(["review", LINK, "--dry-run"]) == 0
    stdout.flush()
    text = saved.getvalue().decode("utf-8")
    assert "qwen2.5-coder:7b` (ollama) · prompts" in text


def test_python_dash_m_runs_the_same_command(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(sys, "argv", ["pr_review_agent", "--version"])
    with pytest.raises(SystemExit) as caught:
        runpy.run_module("pr_review_agent", run_name="__main__")
    assert caught.value.code == 0
    assert "pr-review-agent" in capsys.readouterr().out

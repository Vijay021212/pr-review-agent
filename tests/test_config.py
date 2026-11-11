"""Tests for the settings: defaults, overrides, the token fallback and clear errors."""

from __future__ import annotations

from pathlib import Path

import pytest

from pr_review_agent import Settings
from pr_review_agent.config import Provider
from pr_review_agent.domain import SetupError


@pytest.fixture(autouse=True)
def _empty_folder(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """Run in an empty folder, so a developer's own .env file is never read."""
    monkeypatch.chdir(tmp_path)


def write_env_file(text: str) -> None:
    Path(".env").write_text(text, encoding="utf-8")


def test_defaults_use_the_local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("PRRA_STATE_DIR")
    settings = Settings()
    assert settings.provider is Provider.OLLAMA
    assert settings.model == "qwen2.5-coder:7b"
    assert settings.endpoint == "http://localhost:11434"
    assert settings.api_key is None
    assert settings.github_token is None
    assert settings.context_tokens == 16384
    assert settings.explore_max_steps == 10
    assert settings.time_limit_minutes == 20
    assert settings.time_limit_seconds == 1200
    assert settings.state_dir == Path.home() / ".pr-review-agent"


def test_every_setting_can_be_changed_from_the_environment(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    values = {
        "PRRA_PROVIDER": "openai_compatible",
        "PRRA_MODEL": "gpt-x",
        "PRRA_ENDPOINT": "https://api.example.com/v1",
        "PRRA_API_KEY": "sk-test",
        "PRRA_GITHUB_TOKEN": "ghp_test",
        "PRRA_CONTEXT_TOKENS": "32768",
        "PRRA_EXPLORE_MAX_STEPS": "5",
        "PRRA_TIME_LIMIT_MINUTES": "7.5",
        "PRRA_STATE_DIR": str(tmp_path / "state"),
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)
    settings = Settings()
    assert settings.provider is Provider.OPENAI_COMPATIBLE
    assert settings.model == "gpt-x"
    assert settings.endpoint == "https://api.example.com/v1"
    assert settings.api_key is not None
    assert settings.api_key.get_secret_value() == "sk-test"
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_test"
    assert settings.context_tokens == 32768
    assert settings.explore_max_steps == 5
    assert settings.time_limit_seconds == 450
    assert settings.state_dir == tmp_path / "state"


def test_settings_are_read_from_a_env_file() -> None:
    write_env_file("PRRA_MODEL=qwen3-coder:30b\nPRRA_GITHUB_TOKEN=ghp_from_file\nOTHER=1\n")
    settings = Settings()
    assert settings.model == "qwen3-coder:30b"
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_from_file"


def test_the_environment_wins_over_the_env_file(monkeypatch: pytest.MonkeyPatch) -> None:
    write_env_file("PRRA_MODEL=from-file\n")
    monkeypatch.setenv("PRRA_MODEL", "from-environment")
    assert Settings().model == "from-environment"


def test_an_empty_value_means_the_default() -> None:
    write_env_file("PRRA_MODEL=\n")
    assert Settings().model == "qwen2.5-coder:7b"


def test_github_token_falls_back_to_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_general")
    token = Settings().github_token
    assert token is not None
    assert token.get_secret_value() == "ghp_general"


def test_prra_github_token_wins_over_github_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_general")
    monkeypatch.setenv("PRRA_GITHUB_TOKEN", "ghp_specific")
    token = Settings().github_token
    assert token is not None
    assert token.get_secret_value() == "ghp_specific"


def test_values_in_code_win_over_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRRA_MODEL", "from-environment")
    monkeypatch.setenv("GITHUB_TOKEN", "ghp_environment")
    settings = Settings(model="from-code", github_token="ghp_code")
    assert settings.model == "from-code"
    assert settings.github_token is not None
    assert settings.github_token.get_secret_value() == "ghp_code"


def test_anthropic_gets_its_endpoint_by_default() -> None:
    settings = Settings(provider="anthropic", model="claude-x", api_key="sk-ant-test")
    assert settings.endpoint == "https://api.anthropic.com"


def test_secrets_never_show_when_settings_are_printed() -> None:
    settings = Settings(github_token="ghp_secret", api_key="sk-secret")
    assert "ghp_secret" not in repr(settings)
    assert "sk-secret" not in str(settings)


def test_settings_are_immutable() -> None:
    settings = Settings()
    with pytest.raises(ValueError, match="frozen"):
        settings.model = "other"  # type: ignore[misc]


@pytest.mark.parametrize(
    ("values", "message"),
    [
        ({"provider": "gemini"}, "PRRA_PROVIDER: Input should be 'ollama'"),
        ({"context_tokens": "lots"}, "PRRA_CONTEXT_TOKENS: Input should be a valid integer"),
        ({"context_tokens": 1000}, "PRRA_CONTEXT_TOKENS: Input should be greater than"),
        ({"explore_max_steps": 0}, "PRRA_EXPLORE_MAX_STEPS"),
        ({"time_limit_minutes": 0}, "PRRA_TIME_LIMIT_MINUTES"),
        ({"endpoint": "localhost:11434"}, "PRRA_ENDPOINT: String should match pattern"),
        (
            {"provider": "anthropic", "model": "claude-x"},
            "PRRA_API_KEY must be set for the anthropic provider",
        ),
        (
            {"provider": "openai_compatible", "api_key": "sk-x"},
            "PRRA_MODEL: Field required; PRRA_ENDPOINT: Field required",
        ),
    ],
)
def test_invalid_settings_name_the_variable_to_fix(values: dict[str, object], message: str) -> None:
    with pytest.raises(SetupError, match="How to fix it: Correct these variables") as caught:
        Settings(**values)
    assert message in caught.value.problem


def test_invalid_environment_values_are_reported_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PRRA_EXPLORE_MAX_STEPS", "many")
    with pytest.raises(SetupError, match="PRRA_EXPLORE_MAX_STEPS"):
        Settings()

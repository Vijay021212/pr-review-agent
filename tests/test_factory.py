"""Tests for the factory: the real parts are built from the settings."""

from __future__ import annotations

import pytest

from pr_review_agent import Settings
from pr_review_agent.domain import SetupError
from pr_review_agent.factory import build, build_model, masker_for
from pr_review_agent.github.rest import GitHubRest
from pr_review_agent.models.ollama import OllamaChatModel


def test_the_default_settings_build_the_local_model_and_github() -> None:
    components = build(Settings(github_token="ghp_" + "x" * 36))
    assert isinstance(components.github, GitHubRest)
    assert isinstance(components.model, OllamaChatModel)
    assert (components.model.provider, components.model.model) == ("ollama", "qwen2.5-coder:7b")


def test_a_review_needs_no_token_for_public_repositories() -> None:
    assert isinstance(build(Settings()).github, GitHubRest)


def test_the_model_comes_from_the_settings() -> None:
    model = build_model(Settings(model="qwen3-coder:30b", endpoint="http://gpu-box:11434"))
    assert model.model == "qwen3-coder:30b"


@pytest.mark.parametrize("provider", ["anthropic", "openai_compatible"])
def test_providers_not_built_yet_are_a_setup_error(provider: str) -> None:
    settings = Settings(
        provider=provider, model="m", endpoint="https://api.example.com", api_key="sk-x"
    )
    with pytest.raises(SetupError, match=f"The {provider} provider isn't available") as caught:
        build_model(settings)
    assert "PRRA_PROVIDER=ollama" in caught.value.fix


def test_the_masker_hides_the_configured_token_and_key() -> None:
    masker = masker_for(Settings(github_token="my-github-token-123456", api_key="my-api-key-7890"))
    assert masker("a my-github-token-123456 b my-api-key-7890") == "a ...3456 b ****"
    assert masker_for(Settings())("plain text") == "plain text"

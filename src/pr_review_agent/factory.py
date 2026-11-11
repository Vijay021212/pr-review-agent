"""The one place that builds the real parts from the settings (design section 1, NFR-8).

Everything else receives its parts ready-made, which is what lets tests plug in fakes.
"""

from __future__ import annotations

from dataclasses import dataclass

from pr_review_agent.config import Provider, Settings
from pr_review_agent.domain import ChatModel, GitHub, SetupError
from pr_review_agent.github.rest import GitHubRest
from pr_review_agent.models.ollama import OllamaChatModel
from pr_review_agent.secrets import Masker


@dataclass(frozen=True)
class Components:
    github: GitHub
    model: ChatModel
    masker: Masker


def build(settings: Settings) -> Components:
    token = settings.github_token.get_secret_value() if settings.github_token else None
    return Components(
        github=GitHubRest(token), model=build_model(settings), masker=masker_for(settings)
    )


def build_model(settings: Settings) -> ChatModel:
    if settings.provider is Provider.OLLAMA:
        return OllamaChatModel(settings.endpoint, settings.model, settings.context_tokens)
    raise SetupError(
        f"The {settings.provider} provider isn't available in this version yet.",
        "Set PRRA_PROVIDER=ollama to use the local model.",
    )


def masker_for(settings: Settings) -> Masker:
    """A `Masker` that also hides the configured GitHub token and API key (NFR-5)."""
    secrets = (settings.github_token, settings.api_key)
    return Masker([secret.get_secret_value() for secret in secrets if secret is not None])

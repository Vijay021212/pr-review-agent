"""Settings, read from `PRRA_` environment variables or a `.env` file (design section 10).

Nothing about models or providers is written into the code: changing either is a settings
change only (NFR-7). Tokens and keys are kept as `SecretStr`, which prints as `**********`,
so they can't leak through a log line or an error message (NFR-5).
"""

from __future__ import annotations

from enum import StrEnum
from pathlib import Path
from typing import Annotated, Any

from pydantic import AliasChoices, Field, SecretStr, ValidationError, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from pr_review_agent.domain import NonEmptyStr, SetupError


class Provider(StrEnum):
    OLLAMA = "ollama"
    OPENAI_COMPATIBLE = "openai_compatible"
    ANTHROPIC = "anthropic"


# Values a provider gets when they aren't set. A provider without an entry for a value
# (for example the model of an OpenAI-compatible endpoint) must be given it.
_PROVIDER_DEFAULTS: dict[str, dict[str, str]] = {
    Provider.OLLAMA: {"model": "qwen2.5-coder:7b", "endpoint": "http://localhost:11434"},
    Provider.ANTHROPIC: {"endpoint": "https://api.anthropic.com"},
}

HttpUrl = Annotated[NonEmptyStr, Field(pattern=r"^https?://\S+$")]


class Settings(BaseSettings):
    """Every setting of the agent. Create it with no arguments to read the environment, or
    pass values in code, which win over the environment:

        Settings(provider="anthropic", model="claude-...", api_key="...")

    Invalid settings raise `SetupError`, naming each variable and what's wrong with it.
    """

    model_config = SettingsConfigDict(
        env_prefix="PRRA_",
        env_file=".env",
        env_file_encoding="utf-8",
        env_ignore_empty=True,  # PRRA_MODEL= in a .env file means "use the default"
        extra="ignore",  # a .env file may hold other programs' variables too
        frozen=True,
        validate_by_name=True,
        validate_by_alias=True,
    )

    provider: Provider = Provider.OLLAMA
    model: NonEmptyStr
    endpoint: HttpUrl
    api_key: SecretStr | None = None
    github_token: SecretStr | None = Field(
        default=None,
        # PRRA_GITHUB_TOKEN wins; GITHUB_TOKEN is the name most tools already use.
        validation_alias=AliasChoices("PRRA_GITHUB_TOKEN", "GITHUB_TOKEN", "github_token"),
    )
    context_tokens: int = Field(default=16384, ge=4096)
    explore_max_steps: int = Field(default=10, ge=1, le=50)
    time_limit_minutes: float = Field(default=20, gt=0)
    state_dir: Path = Field(default_factory=lambda: Path.home() / ".pr-review-agent")

    def __init__(self, **values: Any) -> None:
        try:
            super().__init__(**values)
        except ValidationError as error:
            raise _setup_error(error) from None

    @model_validator(mode="before")
    @classmethod
    def _fill_provider_defaults(cls, data: dict[str, Any]) -> dict[str, Any]:
        """Give the chosen provider its default model and endpoint, unless they're set.

        Settings are always built from a dict of the collected values, so `data` is one.
        """
        given = {key: value for key, value in data.items() if value not in (None, "")}
        provider = str(given.get("provider", Provider.OLLAMA))
        return {**_PROVIDER_DEFAULTS.get(provider, {}), **given}

    @model_validator(mode="after")
    def _paid_providers_need_a_key(self) -> Settings:
        if self.provider is not Provider.OLLAMA and self.api_key is None:
            raise ValueError(f"PRRA_API_KEY must be set for the {self.provider} provider")
        return self

    @property
    def time_limit_seconds(self) -> float:
        return self.time_limit_minutes * 60


def _setup_error(error: ValidationError) -> SetupError:
    """Turn Pydantic's error into one message that names each variable to fix."""
    problems = []
    for item in error.errors():
        message = item["msg"].removeprefix("Value error, ")
        field = str(item["loc"][0]) if item["loc"] else ""
        if field:
            message = f"{_variable_name(field)}: {message}"
        problems.append(message)
    return SetupError(
        "The settings are invalid: " + "; ".join(problems) + ".",
        "Correct these variables in your environment or .env file. "
        "Every setting is listed in the README under Settings.",
    )


def _variable_name(field: str) -> str:
    """The environment variable for a field, for example `model` -> `PRRA_MODEL`."""
    upper = field.upper()
    return upper if upper.startswith("PRRA_") or upper == "GITHUB_TOKEN" else f"PRRA_{upper}"

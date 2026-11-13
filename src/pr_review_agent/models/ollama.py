"""`ChatModel` for Ollama, the free local default (NFR-1, ADR-001)."""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Any

import httpx

from pr_review_agent.domain import Deadline, ModelReply, ReviewIncomplete, SetupError
from pr_review_agent.models import ANSWER_TOKENS
from pr_review_agent.retry import send_with_retries

CONNECT_SECONDS = 10.0


class OllamaChatModel:
    """Talks to Ollama's `/api/chat`, with the answer held to a JSON schema by `format`."""

    provider = "ollama"

    def __init__(
        self,
        endpoint: str,
        model: str,
        context_tokens: int,
        *,
        client: httpx.Client | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = endpoint.rstrip("/") + "/api/chat"
        self._endpoint = endpoint
        self._model = model
        self._context_tokens = context_tokens
        self._client = client or httpx.Client()
        self._sleep = sleep

    @property
    def model(self) -> str:
        return self._model

    def complete_json(
        self, system: str, user: str, schema: dict[str, Any], deadline: Deadline
    ) -> ModelReply:
        body = {
            "model": self._model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "format": schema,
            "stream": False,
            # Keep the model in memory between the stages of one review.
            "keep_alive": "10m",
            # Temperature 0 gives the most predictable answer, so reviews are repeatable.
            "options": {
                "temperature": 0,
                "num_ctx": self._context_tokens,
                "num_predict": ANSWER_TOKENS,
            },
        }

        def send() -> httpx.Response:
            # A CPU-only model can take minutes, so the only limit is the review's own.
            timeout = httpx.Timeout(max(deadline.remaining(), 1.0), connect=CONNECT_SECONDS)
            return self._client.post(self._url, json=body, timeout=timeout)

        try:
            response = send_with_retries(
                send, service="Ollama", deadline=deadline, sleep=self._sleep
            )
        except httpx.ConnectError:
            raise self._not_running() from None
        return self._read(response)

    def check_ready(self, deadline: Deadline) -> None:
        """Ollama answers and has the model, checked with its model list before any review
        work (FR-16): a quick call, unlike loading the model."""
        url = self._url.removesuffix("/api/chat") + "/api/tags"

        def send() -> httpx.Response:
            return self._client.get(url, timeout=httpx.Timeout(30.0, connect=CONNECT_SECONDS))

        try:
            response = send_with_retries(
                send, service="Ollama", deadline=deadline, sleep=self._sleep
            )
        except httpx.ConnectError:
            raise self._not_running() from None
        if response.is_error:
            raise ReviewIncomplete(
                f"Ollama couldn't list its models (status {response.status_code}: "
                f"{_error_text(response)})"
            )
        names = {model.get("name") for model in response.json().get("models", [])}
        if self._model not in names and f"{self._model}:latest" not in names:
            raise self._not_downloaded()

    def _not_running(self) -> SetupError:
        return SetupError(
            f"Ollama isn't running at {self._endpoint}.",
            "Start the Ollama app (or run `ollama serve`), then try again.",
        )

    def _not_downloaded(self) -> SetupError:
        return SetupError(
            f"The model {self._model} isn't downloaded in Ollama.",
            f"Run `ollama pull {self._model}`, or set PRRA_MODEL to a model you have "
            "(`ollama list` shows them).",
        )

    def _read(self, response: httpx.Response) -> ModelReply:
        if response.status_code == 404 and "not found" in _error_text(response):
            raise self._not_downloaded()
        if response.is_error:
            raise ReviewIncomplete(
                f"Ollama refused the request (status {response.status_code}: "
                f"{_error_text(response)})"
            )
        data = response.json()
        return ModelReply(
            text=data["message"]["content"],
            model=data.get("model") or self._model,
            input_tokens=data.get("prompt_eval_count", 0),
            output_tokens=data.get("eval_count", 0),
            seconds=data.get("total_duration", 0) / 1e9,  # Ollama reports nanoseconds
        )


def _error_text(response: httpx.Response) -> str:
    """Ollama's error message ({"error": "..."}), or the raw body if it isn't JSON."""
    try:
        data = response.json()
    except ValueError:
        return response.text
    return str(data["error"]) if isinstance(data, dict) and "error" in data else response.text

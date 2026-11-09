"""Tests for the Ollama wrapper, with replies recorded from a real Ollama."""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx
import pytest

from pr_review_agent.domain import Deadline, ReviewIncomplete, SetupError
from pr_review_agent.models.ollama import OllamaChatModel

FIXTURES = Path(__file__).parent / "fixtures" / "ollama"
SCHEMA = {"type": "object", "properties": {"goal": {"type": "string"}}}


def recorded(name: str) -> Any:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def make_model(
    handler: Callable[[httpx.Request], httpx.Response],
    *,
    endpoint: str = "http://localhost:11434",
    waits: list[float] | None = None,
) -> OllamaChatModel:
    return OllamaChatModel(
        endpoint,
        "qwen2.5-coder:7b",
        16384,
        client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=(waits if waits is not None else []).append,
    )


def ask(model: OllamaChatModel) -> Any:
    return model.complete_json("Answer in JSON.", "What is the goal?", SCHEMA, Deadline(600))


def test_a_recorded_answer_comes_back_with_its_counts() -> None:
    reply = ask(make_model(lambda request: httpx.Response(200, json=recorded("chat_reply.json"))))
    assert json.loads(reply.text)["risky"] is True
    assert reply.model == "qwen2.5-coder:7b"
    assert (reply.input_tokens, reply.output_tokens) == (41, 52)
    assert reply.seconds == pytest.approx(12.07, abs=0.01)


def test_the_request_holds_the_schema_and_the_settings() -> None:
    sent: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        sent.append(request)
        return httpx.Response(200, json=recorded("chat_reply.json"))

    ask(make_model(handler, endpoint="http://ollama.local:11434/"))
    request = sent[0]
    assert str(request.url) == "http://ollama.local:11434/api/chat"
    body = json.loads(request.content)
    assert body["model"] == "qwen2.5-coder:7b"
    assert body["format"] == SCHEMA
    assert body["stream"] is False
    assert body["options"] == {"temperature": 0, "num_ctx": 16384, "num_predict": 1024}
    assert body["messages"] == [
        {"role": "system", "content": "Answer in JSON."},
        {"role": "user", "content": "What is the goal?"},
    ]


def test_a_model_that_isnt_downloaded_says_how_to_get_it() -> None:
    not_found = recorded("model_not_found.json")
    model = make_model(lambda request: httpx.Response(not_found["status"], json=not_found["body"]))
    with pytest.raises(SetupError, match="isn't downloaded") as caught:
        ask(model)
    assert "ollama pull qwen2.5-coder:7b" in caught.value.fix


def test_ollama_not_running_says_how_to_start_it() -> None:
    def refuse(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(SetupError, match="isn't running at http://localhost:11434") as caught:
        ask(make_model(refuse))
    assert "Start the Ollama app" in caught.value.fix


def test_server_errors_are_retried_then_the_review_is_incomplete() -> None:
    waits: list[float] = []
    model = make_model(lambda request: httpx.Response(500, json={"error": "busy"}), waits=waits)
    with pytest.raises(ReviewIncomplete, match="Ollama kept failing after 5 attempts"):
        ask(model)
    assert waits == [1.0, 2.0, 4.0, 8.0]


def test_a_server_error_that_clears_up_is_fine() -> None:
    replies = [httpx.Response(503), httpx.Response(200, json=recorded("chat_reply.json"))]
    assert ask(make_model(lambda request: replies.pop(0))).output_tokens == 52


@pytest.mark.parametrize(
    ("response", "message"),
    [
        (httpx.Response(400, json={"error": "invalid format"}), "status 400: invalid format"),
        (httpx.Response(400, text="plain text"), "status 400: plain text"),
        (httpx.Response(404, json=["unexpected"]), 'status 404: \\["unexpected"\\]'),
    ],
)
def test_other_errors_stop_the_review_with_ollamas_message(
    response: httpx.Response, message: str
) -> None:
    with pytest.raises(ReviewIncomplete, match=message):
        ask(make_model(lambda request: response))


def test_it_reports_its_provider_and_model() -> None:
    model = make_model(lambda request: httpx.Response(200))
    assert (model.provider, model.model) == ("ollama", "qwen2.5-coder:7b")

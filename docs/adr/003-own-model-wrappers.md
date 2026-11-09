# ADR-003: Our own model wrappers instead of LiteLLM

- **Status:** Accepted, 2026-11-05
- **Decided by:** Vijay Bolle
- **Requirements:** NFR-1 (local by default), NFR-7 (swappable model), NFR-8 (testable with fakes), FR-9 (structured findings)

## Context

The agent must work with a free local model by default and let users switch to a paid one through settings (NFR-1, NFR-7). The requirements name three kinds of provider:

| Provider | Covers |
|---|---|
| Ollama | Every local model (the default, ADR-001) |
| OpenAI-compatible APIs | OpenAI, and the many services that copy its format (Groq, Together, vLLM and others) |
| Anthropic | Claude models |

Each provider has its own request format, and its own way of forcing an answer into a JSON shape. The code needs one way to talk to all of them.

## Options

| Option | How it works | Good | Bad |
|---|---|---|---|
| A. LiteLLM | A library that translates one call into 100+ providers' formats | No provider code to write | A large layer we don't control sits between us and every provider. PR-Agent's most-discussed issues include LiteLLM breaking on provider differences ([#1015](https://github.com/The-PR-Agent/pr-agent/issues/1015), [#356](https://github.com/The-PR-Agent/pr-agent/issues/356), [#2400](https://github.com/The-PR-Agent/pr-agent/issues/2400)) |
| **B. Our own three wrappers** | One small class per provider behind a `ChatModel` interface | Small, readable, fully tested; each provider's structured output used the right way | Three wrappers to keep up to date; a new kind of provider needs a new wrapper |
| C. Each provider's official SDK | The `openai` and `anthropic` Python packages, plus plain HTTP for Ollama | Maintained by the providers | Two extra dependencies for what are a few HTTP calls; still needs our own interface on top |

## Decision

**Option B: our own three small wrappers**, `OllamaChatModel`, `OpenAICompatibleChatModel` and `AnthropicChatModel`, using `httpx`, behind one interface:

```python
class ChatModel(Protocol):
    def complete_json(
        self, system: str, user: str, schema: dict, deadline: Deadline
    ) -> ModelReply: ...
```

Each wrapper uses its provider's own way of getting JSON in a fixed shape (Ollama's `format`, OpenAI's `response_format`, Anthropic's forced tool call). The check against the Pydantic type and the one retry (FR-9) happen once, outside the wrappers, so every provider behaves the same way.

## Why

- The agent needs only **one** kind of call: instructions and a message in, JSON out. That's about 100 lines per provider.
- Fewer hidden parts means fewer surprises: when something breaks, the code that broke is ours and small enough to read.
- Tests swap in a `FakeChatModel` behind the same interface (NFR-8), with no library to mock.
- One "OpenAI-compatible" wrapper already covers most paid and self-hosted services.

## Consequences

- We maintain the wrappers. If a provider changes its API, we fix our wrapper.
- A provider that isn't Ollama, OpenAI-compatible or Anthropic (for example Google's own API) needs a new wrapper. The interface keeps that a single new file plus a line in `factory.py`.
- Paid providers are tested with recorded replies in fast tests; a real paid call is a manual check, because development and evaluations use the free local model.

## Revisit when

- Users ask for several providers we don't support, so writing wrappers costs more than a library would.
- The agent needs features beyond one JSON call (for example streaming or images) that would make the wrappers large.

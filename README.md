# pr-review-agent

An AI agent that reviews GitHub pull requests the way a careful senior engineer would: it understands the goal of the change, explores the related code on its own, finds real problems at five review levels, double-checks them, and posts one clear review with a recommendation. It runs on a **free local model** by default (Ollama), and can be switched to a paid model through settings.

> **Status:** in development, not yet usable. Progress is tracked in the [issues](https://github.com/Vijay021212/pr-review-agent/issues) and [milestones](https://github.com/Vijay021212/pr-review-agent/milestones).

## Documentation

| Document | Contents |
|---|---|
| [Requirements](docs/requirements.md) | What the agent must do, and how success is measured |
| [Design](docs/design.md) | How it's built: stages, interfaces, GitHub and model integration |
| [Plan](docs/plan.md) | Releases, issues and the quality rule every change follows |
| Decision records | [ADR-001 default model](docs/adr/001-model-choice.md), [ADR-002 one agent in stages](docs/adr/002-single-agent-with-stages.md), [ADR-003 own model wrappers](docs/adr/003-own-model-wrappers.md) |
| [Model test](experiments/adr-001-model-test/) | The test behind ADR-001, with every raw answer |

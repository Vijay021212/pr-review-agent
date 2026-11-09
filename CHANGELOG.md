# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project setup: Python package, ruff, strict mypy, pytest with a 90% branch-coverage gate, CI on Windows and Linux, Dependabot, pull request template ([#1](https://github.com/Vijay021212/pr-review-agent/issues/1)).
- Domain types passed between the review stages, the `GitHub` and `ChatModel` interfaces, the hard time limit, and the `SetupError` and `ReviewIncomplete` exceptions ([#2](https://github.com/Vijay021212/pr-review-agent/issues/2)).
- `Settings`, read from `PRRA_` environment variables or a `.env` file, with `GITHUB_TOKEN` as the fallback for the token and a clear message for every invalid value ([#3](https://github.com/Vijay021212/pr-review-agent/issues/3)).
- Secret masking: private keys, GitHub, OpenAI-style, Anthropic and AWS keys, and passwords in code are masked as `sk-...a1b2` in everything the agent outputs ([#4](https://github.com/Vijay021212/pr-review-agent/issues/4)).
- Ollama model wrapper, a check of every model answer against its expected type with one retry naming the problem, and retries with a growing wait for temporary errors ([#5](https://github.com/Vijay021212/pr-review-agent/issues/5)).
- Reading a pull request from GitHub: details, linked issues, languages, every changed file with its diff and line numbers; closed and merged pull requests are refused, drafts are marked, forks read the fork's code; rate limits and server errors are retried ([#6](https://github.com/Vijay021212/pr-review-agent/issues/6)).
- File selection: lock, binary and generated files are skipped with a reason; the rest are ranked (source, then tests, then docs and configuration; the repository's main language first; smaller diffs first) and packed into batches that fit the model's context; a file too large for any batch is listed as a gap ([#7](https://github.com/Vijay021212/pr-review-agent/issues/7)).

# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- Project setup: Python package, ruff, strict mypy, pytest with a 90% branch-coverage gate, CI on Windows and Linux, Dependabot, pull request template ([#1](https://github.com/Vijay021212/pr-review-agent/issues/1)).
- Domain types passed between the review stages, the `GitHub` and `ChatModel` interfaces, the hard time limit, and the `SetupError` and `ReviewIncomplete` exceptions ([#2](https://github.com/Vijay021212/pr-review-agent/issues/2)).
- `Settings`, read from `PRRA_` environment variables or a `.env` file, with `GITHUB_TOKEN` as the fallback for the token and a clear message for every invalid value ([#3](https://github.com/Vijay021212/pr-review-agent/issues/3)).

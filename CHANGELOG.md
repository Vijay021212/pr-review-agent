# Changelog

All notable changes are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and versions follow [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added

- The repository snapshot: the reviewed commit's files, downloaded from the fork for fork pull requests and unpacked safely into a temporary read-only folder that's removed after the review ([#14](https://github.com/Vijay021212/pr-review-agent/issues/14)).
- The project map and the read-only explore tools: a code-built overview of the repository, and `list_dir`, `open_file` and `search` over the snapshot, which refuse paths outside it and never follow links ([#15](https://github.com/Vijay021212/pr-review-agent/issues/15)).
- The explore stage: before looking for problems, the agent reads related code on its own, choosing one read-only tool at a time (search, open a file, list a folder) until it has seen enough, reaches the step limit or uses its share of the time; its notes and the code it opened go to the finding pass, and the summary and run log record every step ([#16](https://github.com/Vijay021212/pr-review-agent/issues/16)).
- Find pass B: security and performance problems at review levels 4 and 5, reported only with concrete evidence from the code, run on every batch after pass A ([#17](https://github.com/Vijay021212/pr-review-agent/issues/17)).
- The double-check stage: every finding is moved onto a changed line or dropped, re-checked by the model against the code at the reviewed commit, and kept once when reported twice; weak findings and Low-severity style suggestions are dropped with their reason ([#18](https://github.com/Vijay021212/pr-review-agent/issues/18)).
- Posting the review on GitHub: without `--dry-run`, every finding is posted in one plain comment review on its line, never an approval or a block, and the summary is posted or updated; nothing is posted if the pull request got a new commit, and a summary that fails after the findings is reported as such ([#19](https://github.com/Vijay021212/pr-review-agent/issues/19)).
- Preflight and the one-review lock: before any slow work, the agent checks that Ollama runs and has the model and that the GitHub token can read (and, when posting, write to) the repository, each problem with its fix and exit code 2; a second review of the same pull request on the same computer is refused, and a lock left by a crash is replaced ([#20](https://github.com/Vijay021212/pr-review-agent/issues/20)).

### Fixed

- The posted review says "shown on its line" for one finding ([#55](https://github.com/Vijay021212/pr-review-agent/issues/55)).

## [0.1.0] - 2026-11-12

The first review in the terminal: `pr-review-agent review <link> --dry-run` prints a complete review with a recommendation. Posting on GitHub comes in 0.2.0.

### Added

- Project setup: Python package, ruff, strict mypy, pytest with a 90% branch-coverage gate, CI on Windows and Linux, Dependabot, pull request template ([#1](https://github.com/Vijay021212/pr-review-agent/issues/1)).
- Domain types passed between the review stages, the `GitHub` and `ChatModel` interfaces, the hard time limit, and the `SetupError` and `ReviewIncomplete` exceptions ([#2](https://github.com/Vijay021212/pr-review-agent/issues/2)).
- `Settings`, read from `PRRA_` environment variables or a `.env` file, with `GITHUB_TOKEN` as the fallback for the token and a clear message for every invalid value ([#3](https://github.com/Vijay021212/pr-review-agent/issues/3)).
- Secret masking: private keys, GitHub, OpenAI-style, Anthropic and AWS keys, and passwords in code are masked as `sk-...a1b2` in everything the agent outputs ([#4](https://github.com/Vijay021212/pr-review-agent/issues/4)).
- Ollama model wrapper, a check of every model answer against its expected type with one retry naming the problem, and retries with a growing wait for temporary errors ([#5](https://github.com/Vijay021212/pr-review-agent/issues/5)).
- Reading a pull request from GitHub: details, linked issues, languages, every changed file with its diff and line numbers; closed and merged pull requests are refused, drafts are marked, forks read the fork's code; rate limits and server errors are retried ([#6](https://github.com/Vijay021212/pr-review-agent/issues/6)).
- File selection: lock, binary and generated files are skipped with a reason; the rest are ranked (source, then tests, then docs and configuration; the repository's main language first; smaller diffs first) and packed into batches that fit the model's context; a file too large for any batch is listed as a gap ([#7](https://github.com/Vijay021212/pr-review-agent/issues/7)).
- The understand stage: before reviewing, the agent writes down the pull request's goal, a summary, the risky parts and whether the goal is unclear; prompts are kept in versioned text files, with pull request text marked as data ([#8](https://github.com/Vijay021212/pr-review-agent/issues/8)).
- Find pass A: correctness and regressions at review levels 1–3, one model call per batch, each finding with its file, line, level, severity and reason, status, and what triggers it, what happens now and what should happen instead ([#9](https://github.com/Vijay021212/pr-review-agent/issues/9)).
- Recommendation rules and sensitive areas: Approve, Request Changes or Needs Human Review, decided by fixed rules in code with the rule and reasons that decided it ([#10](https://github.com/Vijay021212/pr-review-agent/issues/10)).
- The review summary and finding comments in Markdown: the understanding, the recommendation and its reasons, the reviewed commit, files reviewed and skipped, findings and dropped findings, the model, prompts and time; every text is masked ([#11](https://github.com/Vijay021212/pr-review-agent/issues/11)).
- The first complete review: `pr-review-agent review <link> --dry-run` and the `review_pull_request()` function run every stage in order, stop with "incomplete" if any stage fails or the time limit is reached, and exit with 0, 1 or 2 ([#12](https://github.com/Vijay021212/pr-review-agent/issues/12)).
- A run log for every review: one JSON-lines file in `~/.pr-review-agent/runs/` with each stage, model call (prompt version, tokens, seconds, retries), finding and how the review ended, masked ([#13](https://github.com/Vijay021212/pr-review-agent/issues/13)).

### Fixed

- Reviews saved to a file on Windows are written as UTF-8; time limits under a minute are written in seconds; the "posting isn't available yet" message names `--dry-run` ([#44](https://github.com/Vijay021212/pr-review-agent/issues/44)).

[Unreleased]: https://github.com/Vijay021212/pr-review-agent/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Vijay021212/pr-review-agent/releases/tag/v0.1.0

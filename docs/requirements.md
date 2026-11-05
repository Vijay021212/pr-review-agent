# Requirements

> **Status:** **v1.2.** Baseline v1.0 approved 2026-11-04 by Vijay Bolle; later changes go through change control (see [section 13](#13-change-history)).
> **Project:** `pr-review-agent` · **Owner and approver:** Vijay Bolle

This document says **what** the agent must do and how we'll know it works. "v1" means the first full release of the software; the document's own versions (draft 0.x, baseline 1.0) are counted separately. It doesn't say **how** it's built; that goes in the design document. Words with a special meaning are explained in the [glossary](#12-glossary).

**Guiding principle for v1:** keep it focused, measurable, secure and achievable on a Windows laptop with 24 GB RAM and no GPU. A capability is added only when it's needed, not because it sounds useful.

---

## 1. The problem

Code review is slow, and reviewers miss things: a missing `None` check, an off-by-one loop, a secret committed by accident. Existing AI reviewers help, but they have three gaps:

1. **They're built for large paid models.** Your code is sent to a third party and every review costs money. Some, like PR-Agent, can be pointed at a local model, but they're designed and tuned for large paid models, and none of the tools we studied show how well they work with a free model on an ordinary laptop.
2. **They're noisy and they fail quietly.** Their users report duplicate comments, findings that disappear, and reviews that break while still showing a green check (see [section 8](#8-lessons-from-existing-tools)).
3. **They don't publish how well they work.** None of the tools we studied publish how many real bugs they catch or how many false alarms they raise.

## 2. Goal

A PR review agent that:

- **understands a pull request** before reviewing it, and checks that the code does what the pull request says,
- reviews it and posts detailed comments on the exact lines that have problems,
- runs **on a laptop with a free local model by default**, so the code never goes to an AI service, and can be **configured to use a paid model** instead for anyone who prefers one,
- **reads related code**, not just the changed lines, before deciding something is a problem,
- **checks its own findings** before posting them, to keep noise down,
- gives an **informational recommendation**, while a person always makes the final decision,
- **never fails silently**, and
- comes with **measured scores**, per review level, on pull requests with planted problems.

## 3. Users and stories

| ID | As a... | I want to... | So that... |
|---|---|---|---|
| US-1 | developer | give the agent my pull request's link and get comments on the exact lines | problems are found before a teammate reviews it |
| US-2 | reviewer | read a detailed summary on the pull request of what the agent did | I know exactly what it checked, what it found and what it dropped |
| US-3 | developer | re-run the review after fixing things, without getting the same comments again | the pull request stays readable |
| US-4 | developer | see what was reviewed and what was skipped | I know what the review did NOT cover |
| US-5 | maintainer | tell the agent which files to ignore and what my project's rules are | the review fits my project |
| US-6 | anyone judging the tool | run the evaluation and see its scores | I can trust (or distrust) it based on evidence |
| US-7 | reviewer | see what the agent understood the pull request is for | I can tell straight away if it misunderstood, and judge its comments in that light |
| US-8 | maintainer | know whether a change fits the way the codebase is already built, and meets its quality standards | the codebase stays consistent and easy to maintain, not just free of bugs |
| US-9 | reviewer | get a clear recommendation with its reason, while keeping the final decision | I can act quickly without handing my judgement to a machine |
| US-10 | developer | find out what went wrong when a review behaves strangely | I can fix the problem or report it with evidence |
| US-11 | developer building tools | call the agent from my own Python code and get the review back as data | I can build it into my own tools and workflows |

## 4. Scope

**In scope for v1**

- GitHub pull requests, reviewed when you run one command on your own computer and give it the pull request's link, or from Python code through a small public API (FR-24).
- A free local model through Ollama **by default**. A paid model provider can be configured instead (NFR-1).
- Any text-based code can be reviewed, but quality is **measured on Python only**.

**Not in v1** (possible later)

| Not now | Why |
|---|---|
| GitLab, Bitbucket, Azure DevOps | Most setup problems in PR-Agent come from supporting many hosts. One host keeps v1 small. |
| GitHub Enterprise Server (a company's own GitHub installation) | v1 supports github.com only. |
| Running automatically on every push (GitHub Action or webhook server) | A free GitHub runner can't run a large local model. |
| Changing code or committing fixes | The agent only reads and comments. Safer, and simpler. |
| Writing PR descriptions, answering questions about a PR | Other jobs. v1 does one job well: review. |
| Checking business rules against outside documents (for example a policy document) | Keeps v1 simple. The agent checks the code against what the pull request itself says (FR-2). |
| Running static analysis or security scanners (for example Ruff, Bandit) | Keeps the security boundary simple (NFR-5). Allowlisted scanners can be reconsidered later. |
| Reading Git history | More context, longer reviews, and a risk of mistaking old code for current behaviour. To be reconsidered if the evaluation shows missing history causes false alarms or missed bugs. |

## 5. Functional requirements

Priority uses **MoSCoW**: **Must** have, **Should** have, **Could** have. Requirements are listed in the order the agent works; IDs are fixed and don't follow that order.

**Verified by** says how we prove each requirement is met:

- **Test:** automated tests with fakes (no model, network or GitHub).
- **Eval:** the evaluation on test pull requests (FR-18).
- **Manual:** a person checks it on a real pull request, following a written checklist (written later, in the test plan).

| ID | Requirement | Priority | Verified by | Comes from |
|---|---|---|---|---|
| FR-1 | **Read a pull request** from its link: title, description, changed files and the diff. | Must | Test | All tools |
| FR-20 | **Handle the pull request's state:** an open pull request is reviewed; a **draft** is reviewed and the summary says it's a draft; a **closed or merged** one is refused with a clear message. Pull requests from a **fork** are reviewed the same way, reading the fork's code. If a review of the same pull request is already running on this computer, a second run refuses with a clear message, so two summaries are never posted. | Must | Test | Design review of v0.3 |
| FR-19 | **Review one fixed commit:** the review is tied to the pull request's latest commit at the moment it starts, all code is read at that commit, and the summary names it. If new commits arrive before the review is posted, nothing is posted; the agent says the pull request changed and asks for a re-run. | Must | Test | Design review of v0.3 |
| FR-2 | **Understand the pull request before looking for problems:** write down its goal (from the title, the description and any issue it says it fixes), what changed at a high level, and which parts look risky. If the description is empty, work out the goal from the code and say so. The agent also records a yes/no **"goal unclear"** flag, set when it can't work out the goal with confidence; rule 2(b) in [5.3](#53-recommendation-rules) uses it. The review then checks **whether the code really does what the pull request says**, and this understanding opens the summary (FR-14), so a reviewer can see if the agent misunderstood. | Must | Eval (recommendation and "does what it says" cases); Manual | How human reviewers work; [PR-Agent's `/describe`](https://github.com/The-PR-Agent/pr-agent); US-7 |
| FR-3 | **Skip files that shouldn't be reviewed:** binary files, lock files, generated files, and patterns listed in the project rules file (FR-17). These skips are listed in the summary but don't count as gaps in the review. | Must | Test | [security-review #33](https://github.com/anthropics/claude-code-security-review/issues/33) |
| FR-4 | **Handle big pull requests:** put the most important files first, fit the model's limit, and **list every changed file it couldn't review, with the reason.** | Must | Test | [PR-Agent compression](https://github.com/The-PR-Agent/pr-agent/blob/main/docs/docs/core-abilities/compression_strategy.md), [PR-Agent #178](https://github.com/The-PR-Agent/pr-agent/issues/178), [security-review #80](https://github.com/anthropics/claude-code-security-review/issues/80) |
| FR-5 | **Build a project map, then look at related code:** before exploring, the agent builds a lightweight map of the repository: its folder and file structure, the README, the project rules file if present, and important configuration and entry-point files where they can be identified. It uses the map to decide which related files to inspect, with read-only tools that open files, search the code and list folders, and a limit on how many steps it takes. If the step limit is reached, the summary says so. | Must | Test (tools and step limit); Eval (effect on level 3 scores) | [Shippie](https://github.com/mattzcarey/shippie), [PR-Agent #2499](https://github.com/The-PR-Agent/pr-agent/issues/2499) |
| FR-6 | **Review depth:** the review covers the five levels in [section 5.1](#51-review-depth), to the depth set there. It doesn't comment on formatting or basic syntax (linters handle those) and doesn't report subjective design preferences. | Must | Eval (scores per level) | Owner decision, 2026-11-04 |
| FR-7 | **Check how well the change fits the existing code:** does it follow the patterns the codebase already uses (layers, error handling, configuration, logging)? Is it wired in properly: registered where similar parts are registered, settings added where settings live, callers updated? Does it rebuild something the codebase already has instead of reusing it? Each finding names the existing code it should match. | Must | Eval (planted design-fit problems) | How senior reviewers work; US-8 |
| FR-8 | **Check code quality against the project's own standards:** readability and naming, repeated code, size and complexity of functions, missing or weak tests for the new behaviour, and comments that are wrong or missing. Standards are taken from the surrounding code and the project rules file (FR-17), not from personal taste. | Must | Eval (planted quality problems) | US-8 |
| FR-9 | **Give structured findings:** each one has a file, line, category (bug, security, performance, design fit, code quality), review level ([5.1](#51-review-depth)), severity ([5.2](#52-severity-levels)) **with the reason for it**, an explanation and, when possible, a suggested fix. Every bug finding states **what triggers it, what happens now and what should happen instead**, with a concrete example whenever possible. A problem that can't be confirmed from the available code is labelled a **potential risk**, not a confirmed bug. Model output that doesn't match this format is **detected and retried**. | Must | Test (format check and retry); Eval | [Shippie #22](https://github.com/mattzcarey/shippie/issues/22); owner decision, 2026-11-04 |
| FR-10 | **Check findings before posting:** a second step re-checks each finding against the code and drops weak ones. Every finding is **anchored on a changed line** of the pull request, because GitHub only accepts comments there; when the harm shows up elsewhere (for example a caller in another file), the comment sits on the changed line that causes it and names the affected place. Design-fit and quality findings (FR-7, FR-8) must point to the existing code or rule they're based on. Matters of taste, and Low-severity style or formatting suggestions, are dropped. | Must | Test (anchoring); Eval (precision) | [Anthropic security review](https://github.com/anthropics/claude-code-security-review); GitHub review rules |
| FR-21 | **Never repeat a secret:** if the agent finds a secret in the code (a password, key or token), its comment names where it is and what kind it is, but never quotes the value, which is masked (for example `sk-...a1b2`). The same applies to the summary and the run log (FR-23). | Must | Test | Design review of v0.3 |
| FR-11 | **No duplicate comments on re-runs:** a finding that's already posted isn't posted again, and findings on threads a person marked as resolved aren't raised again. | Should | Test | [PR-Agent #2037](https://github.com/The-PR-Agent/pr-agent/issues/2037), [#3832](https://github.com/The-PR-Agent/pr-agent/issues/3832), [#3654](https://github.com/The-PR-Agent/pr-agent/issues/3654) |
| FR-12 | **Review recommendation and human authority:** the summary gives one overall recommendation: **Approve**, **Request Changes** or **Needs Human Review**, following the rules in [section 5.3](#53-recommendation-rules). The recommendation is **informational only**. The agent must never submit an actual GitHub approval or change request, block a pull request or merge it; its review is always posted as a plain comment. A human reviewer always keeps the final decision. *The agent assists the reviewer; it doesn't replace them.* | Must | Test (rules; review type is always "comment"); Eval (recommendation accuracy) | Owner decision, 2026-11-04; US-9 |
| FR-13 | **Post the review on GitHub by default:** validated findings are posted as detailed inline comments on their lines, **all in one go**: either every finding is posted or none is. The summary (FR-14) is posted or updated (FR-22) right after; if that step fails, the agent says so and exits with an error (NFR-4). The summary is posted **even when nothing is found**, so the reviewer knows it ran. | Must | Test; Manual | [Shippie #336](https://github.com/mattzcarey/shippie/issues/336); US-1, US-2 |
| FR-14 | **A detailed summary of what the agent did,** posted on the pull request and shown in the terminal: what it understood the pull request is for (FR-2), the recommendation and the rule that decided it (FR-12), the commit reviewed (FR-19), files reviewed, files skipped (and why), related files it opened and searches it ran, findings posted, findings dropped by the check (and why), the model and provider used and the time taken. | Must | Test; Manual | [Shippie #328](https://github.com/mattzcarey/shippie/issues/328) (17 findings, 3 posted, no explanation); US-2, US-4 |
| FR-22 | **One summary per pull request:** on a re-run the agent **updates its earlier summary** instead of adding a new one, noting the new commit reviewed and what changed since the last review. | Should | Test | [PR-Agent #2534](https://github.com/The-PR-Agent/pr-agent/issues/2534); US-3 |
| FR-15 | **Dry run:** a `--dry-run` option shows the whole review in the terminal and writes nothing to GitHub, for trying the tool safely. | Should | Test | Safety |
| FR-24 | **Usable as a Python library:** besides the command line, the agent can be used from Python code through a small, typed and documented **public API**: one call reviews a pull request and returns the whole result as data (understanding, findings, recommendation and what was reviewed or skipped). The command line is built on the same call, so both behave the same. Problems are raised as documented exceptions instead of exit codes. The public API follows Semantic Versioning: it changes incompatibly only in a new major version. The package is published to PyPI. | Should | Test (public API; the command line uses it); Manual (install from PyPI) | Owner decision, 2026-11-05; US-11 |
| FR-16 | **Explain setup problems clearly:** Ollama not running, model not downloaded, model provider key missing or rejected, GitHub token missing or without permission, pull request not found. Each message says how to fix it. | Must | Test | Most of PR-Agent's most-discussed issues are setup and connection errors |
| FR-23 | **Run log:** every run saves a local record of what the agent did: each step and tool call, the model, provider, settings and prompt version used, and the time each step took. It never contains secrets (FR-21). The log is how a strange review is investigated, and how evaluation results are reproduced. | Must | Test | Design review of v0.3; US-10 |
| FR-17 | **Project rules file:** an optional file in the repository holds ignore patterns, extra sensitive areas on top of the built-in list ([5.3](#53-recommendation-rules)) and extra review instructions. | Could | Test | [PR-Agent #2384](https://github.com/The-PR-Agent/pr-agent/issues/2384), [Shippie #454](https://github.com/mattzcarey/shippie/issues/454) |
| FR-18 | **Evaluation:** test pull requests with planted problems at every review level, including design-fit and quality problems, clean pull requests, and **prompt-injection** pull requests, run through the real agent and reporting the scores in [section 7](#7-how-we-measure-success), following the rules in [section 7.1](#71-evaluation-rules). Each planted problem is tagged with its review level, and each test pull request has an **expected recommendation** (FR-12), so the evaluation checks the whole agent end to end, not only the model's findings. | Must | Eval | The gap in [section 1](#1-the-problem); owner decision, 2026-11-04 |

### 5.1 Review depth

| Level | Review focus | v1 depth |
|---|---|---|
| 1 | Obvious bugs | Yes. Formatting and basic syntax are left to linters. |
| 2 | Logic errors, missing conditions, edge cases | Yes. **The core, built first.** |
| 3 | Execution flow, dependencies, regression risks | Yes |
| 4 | Architecture, security, performance, scalability | Yes, **with concrete evidence only** |
| 5 | Adversarial scenarios and unusual failure combinations | Selectively: only when the code gives concrete grounds |

The evaluation measures catch rate and precision **separately for each level**. An agent can be good at simple bugs and poor at execution-flow problems, and one overall score would hide that.

### 5.2 Severity levels

| Severity | Meaning | Example |
|---|---|---|
| Critical | Immediate, severe impact, such as data exposure, a major security compromise or widespread data loss | An authentication bypass that exposes sensitive records |
| High | A major functional failure affecting an important workflow | Payment or claim processing fails for valid requests |
| Medium | A real problem affecting a limited scenario or edge case, or one that makes the code clearly harder to maintain | Missing handling for a particular optional field; logic copied instead of reused |
| Low | Minor impact that doesn't break the main workflow | A non-critical error message is misleading |

Rules:

- Severity is based on **impact**, not on how complicated the code looks.
- Every finding includes the reason for its severity.
- Low-severity style or formatting suggestions are generally dropped (FR-10).

### 5.3 Recommendation rules

The recommendation is **worked out by fixed rules in code**. The model supplies the validated findings (with their severities) and the understanding of the pull request (FR-2); code then applies the rules below, together with how much of the pull request was actually reviewed. The model never picks the recommendation itself, so the same findings always give the same recommendation.

| Recommendation | Meaning |
|---|---|
| **Approve** | No significant problems found in the checks performed; at most Low findings or maintainability suggestions |
| **Request Changes** | Significant, evidence-backed problems found |
| **Needs Human Review** | Important checks were incomplete, a real bug needs a person's judgement, findings are uncertain, or the agent lacks enough evidence |

A **bug-type finding** means the category is bug, security or performance. Design-fit and code-quality findings are **maintainability findings**.

The rules are checked in this order, and the first match wins:

| Order | Recommendation | When |
|---|---|---|
| 1 | **Request Changes** | At least one validated, **confirmed** Critical or High finding. |
| 2 | **Needs Human Review** | No confirmed Critical or High findings, and any of these is true: (a) some changed files couldn't be reviewed (FR-4); (b) the "goal unclear" flag from FR-2 is set; (c) it changes a **sensitive area** (see below); (d) there's a Critical or High **potential risk** that couldn't be confirmed from the available code; (e) there's a confirmed **Medium bug-type** finding. |
| 3 | **Approve** | None of the above: every changed file was reviewed, the goal is clear, and only Low findings, Medium maintainability findings, or none remain. |

The summary always states **which rule decided the recommendation**, for example *"Request Changes: 1 High finding (`claims.py` line 42)"* or *"Needs Human Review: `big_module.py` was too large to review"*. If rule 1 matches but the review also had gaps, both are reported.

**Sensitive areas** are parts of a codebase where a small mistake can do serious harm, so a person should always look at changes there. They come from two places:

1. **A built-in default list**, active even without a project rules file, of words linked to authentication (`auth`, `login`, `password`, `token`, `secret`, `permission`), payments (`payment`, `billing`), personal or health data (`patient`, `phi`, `pii`), database changes (`migration`, `schema`) and deployment (`deploy`, `infra`), plus the exact paths `.github/workflows/` and `Dockerfile`.
2. **The project rules file (FR-17)**, which can add more paths, for example `src/prior_auth/`.

A word matches only as a **whole word inside a folder or file name**, where words are separated by `/`, `_`, `-` or `.`, and singular and plural both count. So `auth/session.py`, `auth_service.py`, `user-permissions.py` and `migrations/0042.py` match, but `author.py`, `tokenizer.py` and `graphics.py` don't.

The summary names the sensitive area that matched, for example *"Needs Human Review: changes `src/auth/session.py` (sensitive area: authentication)"*.

These recommendations are informational only (FR-12).

## 6. Quality requirements (non-functional)

| ID | Requirement | Verified by | Comes from |
|---|---|---|---|
| NFR-1 | **Local by default, paid optional:** out of the box the agent uses a free local model through Ollama, and code is sent only to GitHub (where it already is). A user can switch to a paid model through settings alone (endpoint address, API key and model name), with no code change. v1 supports **any model offered through an OpenAI-compatible API**, which most providers offer, **plus Anthropic's API**. Providers that connect in their own way (for example Google Gemini's own API, AWS Bedrock or Azure sign-in) can be added later through change control. When a paid provider is used, the code under review is sent to that provider; the README says so plainly, and the summary (FR-14) names the provider and model used. Development and the published evaluation use the free local default. | Test; Manual | Owner decision, 2026-11-04 |
| NFR-2 | **Runs on a normal laptop:** 24 GB RAM, CPU only. **Windows is the main platform**; the automated tests also run on Linux in CI, so the code must work on both. | Manual (Windows); Test (Linux CI) | Target hardware |
| NFR-3 | **Speed** (provisional, checked after the model test, measured on that laptop from the moment the command starts until it finishes, **including loading the model**): up to 300 changed lines in under 5 minutes; up to 500 changed lines in under 10 minutes. Larger pull requests are handled by FR-4. **Hard limit:** a review that runs longer than **20 minutes** (a setting) is stopped and reported as incomplete (NFR-4), so a stuck model can never run forever. **Speed never takes priority over checks:** a check is never skipped silently to save time. | Eval (timings); Test (hard limit) | Usability; owner decision, 2026-11-04 |
| NFR-4 | **No silent failures:** if the review can't be completed, because a step fails, a check is switched off or the hard time limit (NFR-3) is reached, the output says the review is **incomplete**, no recommendation is given, and the command exits with an error code. Nothing half-finished is posted: findings are posted all together or not at all (FR-13), and if the summary step fails after the findings were posted, the agent says so clearly instead of leaving it unexplained. A broken review must never look like a clean one. Files left out on purpose to fit a big pull request (FR-4) are **not** a failure: the review is complete for what it covered, the gaps are listed, and the recommendation is Needs Human Review ([5.3](#53-recommendation-rules)). | Test | [PR-Agent #2576](https://github.com/The-PR-Agent/pr-agent/issues/2576), [security-review #69](https://github.com/anthropics/claude-code-security-review/issues/69), [#80](https://github.com/anthropics/claude-code-security-review/issues/80), [#152](https://github.com/anthropics/claude-code-security-review/issues/152) |
| NFR-5 | **Security boundary:** pull request content is untrusted, because it can contain hidden instructions aimed at the AI (prompt injection). The agent's **tools** can read repository files and nothing else: they **cannot** execute repository code, run static scanners, write or modify files, or reach the network. The **application** talks only to GitHub and to the configured model provider (Ollama on the same computer by default). The agent's own application and its trusted, locked dependencies run normally; the restriction is on executing the **repository's** code or adding scanner runs to the review. The GitHub token needs only **Contents: read** and **Pull requests: write**. The GitHub token and any model provider's API key are read from settings or the environment, and are never printed, logged or posted. | Test; Eval (prompt-injection cases) | [Anthropic security review README](https://github.com/anthropics/claude-code-security-review); owner decision, 2026-11-04 |
| NFR-6 | **Supply chain:** dependencies are locked (`uv.lock`) and GitHub Actions are pinned to exact versions. | Manual (CI review) | [security-review #112](https://github.com/anthropics/claude-code-security-review/issues/112) |
| NFR-7 | **Swappable model and provider:** the model provider (local or paid) and the model are settings, never written into the code. Changing either needs no code change. | Test | [security-review #69](https://github.com/anthropics/claude-code-security-review/issues/69) (a retired model was hard-coded, which silently switched off a check) |
| NFR-8 | **Maintainable and tested:** small interfaces, one place that wires parts together, and fast tests with fakes that need no model, network or GitHub. | Test (CI) | Engineering standard |
| NFR-9 | **Easy setup:** from cloning to the first review in five commands or fewer, all documented. | Manual | Most-discussed issues in PR-Agent are about setup |
| NFR-10 | **Recover from temporary problems:** a temporary failure from GitHub or the model provider (rate limit, network blip, server error) is retried a few times with a growing wait. If it still fails, the review stops and is reported as incomplete (NFR-4). A diff too large for GitHub's API is handled without silently losing files. | Test | [security-review #80](https://github.com/anthropics/claude-code-security-review/issues/80) (a too-large diff silently gave zero findings); [PR-Agent #104](https://github.com/The-PR-Agent/pr-agent/issues/104) (rate limits) |

## 7. How we measure success

The evaluation (FR-18) reports these numbers. The targets are **first guesses**; they'll be revisited after the model test and recorded in a decision record.

| Measure | What it means | First target |
|---|---|---|
| Catch rate, **per review level** | Planted problems the agent found, reported separately for levels 1–5 | 60% or more overall; per-level targets set after the first run |
| Precision, **per review level** | Posted findings that are real problems, reported separately for levels 1–5 | 70% or more overall |
| False alarms | Wrong findings per pull request, on average | 1 or fewer |
| Line accuracy | Of the findings that catch a planted problem (7.1), the share placed **inside the planted problem's exact lines** | 90% or more |
| Recommendation accuracy | Test pull requests where the agent's recommendation matches the expected one | 80% or more |
| Clean pull requests | Comments on pull requests with no planted problems | 1 or fewer each |
| Prompt injection | Injection test pull requests where the agent ignored the hidden instruction | 100% |
| Time | Minutes per pull request, on the 24 GB CPU-only laptop | Under 5 (up to 300 lines); under 10 (up to 500 lines) |

### 7.1 Evaluation rules

So that the scores can be trusted:

- **Size:** at least **5 planted problems per review level** (25 or more in total), of which at least **3 are design-fit** and **3 are code-quality** problems; at least **5 clean** pull requests; and at least **3 prompt-injection** pull requests.
- **Held-out set:** about a third of the test pull requests are kept aside and **never looked at while tuning prompts or settings**. Published scores report the held-out set separately, because tests written alongside the tuning flatter the system.
- **Matching rule:** a finding catches a planted problem when it's in the **same file**, **within 3 lines** of the planted line (or inside its range), and describes **the same problem**. Whether it's the same problem is checked by reading, and recorded in the results. A finding that matches no planted problem is read and marked as a real problem or a false alarm, with the reason recorded.
- **Offline:** test pull requests are stored as files (pull request details, diff and a snapshot of the repository). The evaluation needs no GitHub access, never posts anything, and gives the agent exactly the same input every time.
- **Repeat runs:** models don't always answer the same way, so published scores come from **3 runs**, reporting the average and the lowest and highest result. A single run is fine for quick checks during development.
- **Reproducible:** every result records the model, provider, settings and prompt version used (FR-23).

## 8. Lessons from existing tools

We studied the most-starred open-source PR reviewers on GitHub on 2026-11-03 and read their most-discussed issues.

| Tool | Stars | What we take | What its users complain about |
|---|---|---|---|
| [PR-Agent](https://github.com/The-PR-Agent/pr-agent) | 14.3k | Clean structure (one adapter per Git host, one module per command, prompts kept outside the code); the way it fits big pull requests into the model | Duplicate comments on re-runs; resolved issues raised again; timeouts on big pull requests; reviews that silently produce nothing; hard setup |
| [Anthropic security review](https://github.com/anthropics/claude-code-security-review) | 6.7k | A second step that filters out false alarms; an evaluation folder | The filter silently switched off for months by a hard-coded retired model; big pull requests silently give zero findings; no way to ignore files |
| [Shippie](https://github.com/mattzcarey/shippie) | 2.5k | An agent that explores the codebase with tools before commenting | Findings lost between review and posting; malformed model output; no comment when nothing is found |

**The overall lesson:** users trust a reviewer that is **quiet, honest and predictable** more than one that says a lot. That's why "check findings before posting" (FR-10), "a detailed summary" (FR-14) and "no silent failures" (NFR-4) are all Must.

## 9. Assumptions and constraints

- The user has Ollama installed and can download a model of up to about 20 GB.
- The user has a GitHub token with **Contents: read** and **Pull requests: write** permission on the repository.
- A local model will review less well than paid models. The evaluation will show by how much; we report it rather than hide it.

## 10. Risks

| ID | Risk | What we do about it |
|---|---|---|
| R-1 | The free local model reviews too poorly to be useful | Measured honestly by the evaluation; model chosen by test (ADR-001); a paid model can be configured (NFR-1) |
| R-2 | The model barely fits in 24 GB of RAM (about 19 GB for `qwen3-coder:30b`) | A smaller model is the fallback (ADR-001); the README states the memory needed |
| R-3 | Reviews are too slow on a CPU; a full published evaluation (30+ pull requests × 3 runs) could take 8–10 hours | Speed targets are provisional (NFR-3); the hard limit stops runaway reviews; published evaluation runs are done overnight, and single runs are used during development |
| R-4 | The evaluation is too small or flatters the agent | Minimum size, held-out set and repeat runs (7.1) |
| R-5 | A pull request tricks the agent through prompt injection | Read-only tools that can't run code (NFR-5); the recommendation is decided by code, not the model (5.3); injection tests (FR-18) |
| R-6 | GitHub's API limits or changes break reviews | Retries and loud failures (NFR-10, NFR-4) |
| R-7 | Scope creep: v1 keeps growing and never finishes | The baseline and change control ([section 13](#13-change-history)) |

## 11. Questions resolved in design

| Question | Decision |
|---|---|
| Which model | `qwen2.5-coder:7b` by default ([ADR-001](adr/001-model-choice.md)) |
| Single agent or multi-agent | One agent working in fixed stages ([ADR-002](adr/002-single-agent-with-stages.md)) |
| How paid providers are connected (NFR-1) | Three small wrappers of our own instead of a multi-provider library ([ADR-003](adr/003-own-model-wrappers.md)) |
| Format of the project rules file (FR-17) | A TOML file, `.pr-review-agent.toml`, read from the base branch ([design section 9](design.md#9-the-project-rules-file-fr-17)) |

## 12. Glossary

| Word | Meaning |
|---|---|
| Finding | One problem the agent reports, on one line |
| Confirmed bug | A finding the agent can show from the available code |
| Potential risk | A possible problem the agent can't confirm from the available code |
| Bug-type finding | A finding in the bug, security or performance category |
| Maintainability finding | A finding in the design-fit or code-quality category |
| Severity | How much harm a problem causes: Critical, High, Medium or Low ([5.2](#52-severity-levels)) |
| Review level | How deep a problem is, from 1 (obvious) to 5 (adversarial) ([5.1](#51-review-depth)) |
| Recommendation | Approve, Request Changes or Needs Human Review; informational only ([5.3](#53-recommendation-rules)) |
| Sensitive area | Part of a codebase where a small mistake can do serious harm ([5.3](#53-recommendation-rules)) |
| Project map | A lightweight overview of the repository the agent builds before exploring (FR-5) |
| Project rules file | An optional file in the repository with ignore patterns, sensitive areas and review instructions (FR-17) |
| Dry run | A review shown only in the terminal, with nothing written to GitHub (FR-15) |
| Planted problem | A problem put into a test pull request on purpose, so we know the right answer |
| Catch rate | Of the planted problems, the share the agent found |
| Precision | Of the agent's findings, the share that are real problems |
| Prompt injection | Text hidden in code or a description that tries to give the AI instructions |
| Baseline | The approved version of this document; later changes go through change control |
| Public API | The functions and types other developers may rely on; everything else in the package is internal and can change at any time (FR-24) |
| ADR | Architecture decision record: a short note explaining one important design decision and why it was made |
| Ollama | Free software that runs AI models on your own computer |
| OpenAI-compatible API | A common way of talking to AI models over the internet, offered by most providers |

## 13. Change history

| Version | Date | Change |
|---|---|---|
| v0.1 | 2026-11-03 | First draft from research on existing tools |
| v0.2 | 2026-11-03 | Post to GitHub by default with a detailed summary; dry run optional; added "understand the PR first", design fit and code quality |
| v0.3 | 2026-11-04 | Added the project map, review depth, severity levels, finding structure (trigger, current, expected; potential risk vs confirmed), recommendation rules and human authority, the 500-line speed target, and the clarified security boundary. Moved business-rule documents, static scanners and Git history to "not in v1". Changed NFR-1 from "free local only" to "local by default, any paid API model optional". Named the project `pr-review-agent`. Added recommendation accuracy to the evaluation, a built-in list of sensitive areas, a 20-minute hard limit, and the difference between an incomplete review and skipped files. Renumbered requirements into the order the agent works. |
| v0.4 | 2026-11-04 | Changes from a design review of v0.3. **Fixed:** an inaccurate claim in the problem statement; NFR-5 conflicting with paid providers; GitHub token permissions; sensitive-area matching (whole words only); **Approve no longer allowed with a confirmed Medium bug** (new rule 2(e)); rule 2(a) wording. **Added:** FR-19 (review one fixed commit), FR-20 (pull request states and forks), FR-21 (never repeat a secret), FR-22 (one summary per pull request), FR-23 (run log), NFR-10 (temporary problems), comment anchoring on changed lines (FR-10), all-or-nothing posting (FR-13), evaluation rules (7.1) and prompt-injection tests, a "Verified by" column, timing including model loading, Windows plus Linux CI, risks, glossary, owner. |
| v0.5 | 2026-11-04 | Second review. Paid models limited to OpenAI-compatible APIs plus Anthropic's API (owner decision); posting is all-or-nothing for findings, with the summary right after; clear definition of line accuracy; the model supplies findings and a "goal unclear" flag while code applies the rules; GitHub Enterprise out of scope; one review of a pull request at a time; evaluation run time added to R-3; glossary extended. Third review: "Approve" meaning matched to its rule; NFR-4 matched to FR-13's posting order; software "v1" told apart from document versions; minimum design-fit and quality cases in the evaluation. |
| **v1.0** | 2026-11-04 | **Approved as the baseline** by Vijay Bolle. No content changes from v0.5. |
| v1.1 | 2026-11-05 | Editorial only, no requirement changed: open questions replaced by links to the decisions (section 11); "Comes from" wording made neutral; basic glossary entries removed. |
| v1.2 | 2026-11-05 | **Change request:** added FR-24 (usable as a Python library, Should), user story US-11, a scope line and the "Public API" glossary entry. Owner decision. |

**ID changes in v0.3:**

| v0.2 | v0.3 |
|---|---|
| FR-1 | FR-1 |
| FR-14 | FR-2 |
| FR-2 | FR-3 |
| FR-3 | FR-4 |
| FR-4 | FR-5 (project map added) |
| new | FR-6 (review depth) |
| FR-15 | FR-7 |
| FR-16 | FR-8 |
| FR-5 | FR-9 |
| FR-6 | FR-10 |
| FR-12 | FR-11 |
| FR-17 | FR-12 |
| FR-7 | FR-13 |
| FR-9 | FR-14 |
| FR-8 | FR-15 |
| FR-10 | FR-16 |
| FR-13 | FR-17 |
| FR-11 | FR-18 |

v0.4 kept every v0.3 ID and added FR-19 to FR-23 and NFR-10.

**Change control:** after approval, this document is the **v1.0 baseline** and its IDs never change. A new capability or a changed requirement goes through an explicit change request, recorded in this table, rather than quietly expanding v1.

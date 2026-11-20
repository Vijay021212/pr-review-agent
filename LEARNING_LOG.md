# pr-review-agent: Learning Log

A record of what we built, why we built it that way, what went wrong, and what we
learned. One section per phase, newest last.

---

## Phase 1 (v0.1.0): the first review in the terminal

**Goal:** `pr-review-agent review <link> --dry-run` prints a complete review of a GitHub pull
request, with a recommendation, using a free model on the laptop.

### What we built

The review runs as **one agent working in fixed stages** ([ADR-002](docs/adr/002-single-agent-with-stages.md)):

| Stage | What it does | Model? |
|---|---|---|
| Preflight | Checks that Ollama runs and has the model, and that the GitHub token works | No |
| Fetch | Reads the pull request: details, linked issues, every changed file with its diff | No |
| Select files | Skips lock, binary and generated files; ranks the rest; packs them into batches that fit the model's context | No |
| Understand | Writes down the goal, a summary and the risky parts before looking for problems | Yes |
| Find, pass A | Looks for correctness problems and regressions (review levels 1 to 3) | Yes |
| Recommend | Approve, Request Changes or Needs Human Review, by fixed rules | No |
| Summary | The review in Markdown, every text masked for secrets | No |

### Key concepts

1. **A pipeline with one model, not a team of agents.** The laptop holds one model at a
   time (`qwen2.5-coder:7b`, [ADR-001](docs/adr/001-model-choice.md)), and small models do
   better on narrow tasks. So each stage asks the model one focused question.
2. **Structured output.** The model must answer in JSON that matches a schema (a Pydantic
   type). Code checks every answer; if it's wrong, the model is asked once more with the
   problem named. A finding without "what triggers it, what happens now, what should happen"
   is rejected.
3. **Prompts are files with versions** (`prompts/find_a.md`, `version: 3`). The version goes
   into every review and run log, so a change in scores can be traced to a change in a prompt.
4. **The model finds, code decides.** The recommendation comes from rules in
   `recommendation.py`, never from the model: a confirmed Critical or High bug means Request
   Changes; skipped files, an unclear goal, a sensitive area or a confirmed Medium bug mean
   Needs Human Review; anything else is Approve.
5. **Pull request text is data.** It goes into the prompt between `<<<BEGIN ...>>>` markers,
   with the rule that it's never instructions.
6. **Tests without the model.** `FakeChatModel` plays back scripted answers, so 700+ tests
   run in seconds and the real model is only needed for real runs.

### Problems & fixes

- **Windows wrote reviews in the wrong character set** when saved to a file (#44): the command
  line now always writes UTF-8.

---

## Phase 2 (v0.2.0): the agent explores, double-checks and posts

**Goal:** a review that reads related code on its own, checks its findings, and posts them
on GitHub.

### What we built

- **The explore stage: an agent loop.** The model chooses one read-only tool at a time
  (`search`, `open_file`, `list_dir`, or `done`), code runs it on a snapshot of the repository,
  and the result goes into the next step. It stops when the model says done, after 10 steps,
  after 35% of the time limit, or when the model repeats itself.
- **Find pass B:** security and performance (review levels 4 and 5), only with concrete
  evidence.
- **The double-check:** every finding is moved onto a changed line (GitHub accepts comments
  only there), re-checked by the model, de-duplicated, and Low style remarks are dropped.
- **Posting:** all findings in one review, plus a summary; nothing is posted if the pull
  request got a new commit meanwhile.

### Key concepts

1. **An agent = a model choosing actions in a loop.** Code stays in charge: it runs the
   tools, sets the limits, and records every step in the run log.
2. **Tools that can't do harm.** They only read, only inside the snapshot (paths are resolved
   and checked; links aren't followed), and can't run code or reach the network.
3. **A small context window (16k tokens).** Each explore step shows the full result of the
   last step only; earlier steps appear as one line each, plus the model's own notes.

### Problems & fixes

- **"Shown on its line"** read wrongly for a single finding (#55): fixed in the wording.

---

## Phase 3 (v0.3.0): measuring the agent honestly

**Goal:** replace "the reviews look good" with numbers, and improve the agent one measured
change at a time.

### What we built

- **The evaluation** (`pr-review-agent eval eval/cases`, #21): reviews every test pull request
  with the real agent, offline, and writes a report, a review sheet and the raw results.
- **33 test pull requests** (`eval/cases`, #22). Each is a folder with the code before
  (`base/`), after (`head/`) and a `case.yaml` with the answer key:

  | Kind | How many | What we expect |
  |---|---|---|
  | One planted problem | 25 (5 per review level) | The agent finds it, on the right line |
  | Clean change | 5 | No comments, Approve |
  | Prompt injection | 3 | The agent isn't fooled |

  12 of them are **held out**: never looked at while tuning, so their scores show how the
  agent does on changes it wasn't tuned on.
- **Readings** (`eval/readings.yaml`, #64): a person's verdict on every finding (same problem,
  real problem or false alarm), reused whenever the same finding comes back, and
  `--rescore` to score a finished evaluation again in seconds.

### Key concepts

1. **Catch rate and precision.** Catch rate: of the planted problems, how many were found.
   Precision: of the findings, how many were right. Pushing one up often pushes the other down.
2. **A score must be read, not only counted.** The matching rule needs the same file, within 3
   lines, *and* the same problem. Code can check the first two; only reading checks the third.
3. **Ablation: change one thing at a time.** Record the exact prompt the model got, then ask
   again with one part removed. The part whose removal changes the answer is the cause.
4. **Temperature 0 is "nearly" repeatable.** The model always picks its most likely answer,
   but when two answers are almost tied, tiny numeric differences tip it either way. Those
   close calls are exactly the uncertain decisions, which is why published scores use 3 runs.
5. **A frozen copy for long runs** (`git worktree`). The agent reads its prompts from disk on
   every call, so an evaluation runs from a separate copy of the code at one commit, and
   work can go on in the main folder without leaking into the run.

### The first measurement

The first full evaluation (3 runs, 2 hours 27 minutes) caught 3 of 27 planted problems: 11%.
The three runs gave identical answers. Recording the prompts showed why the model said
nothing so often.

### The fixes, one at a time

Every fix had its own issue, branch, tests, real run and measurement; the numbers below are
read finding by finding.

| Issue | What was wrong | The fix | What it changed |
|---|---|---|---|
| #60 | A changed file was shown to the model a second time as "unchanged" code, so it read the new lines as old | Related code leaves out changed files | Catches 3 to 7 (by location) |
| #62 | The prompt said "An empty list is a good answer", and the model took the hint | The sentence removed | 2 more real catches, and 8 more wrong comments |
| #64 | The score counted the location only: 44% caught by location was 33% by reading | Readings, and `--rescore` | Honest scores from then on |
| #66 | The model copied the prompts' examples: a note about `average(values)` became a High finding in a file without it | Prompts describe a good answer instead of showing one | Catch rate 30% to 37%, precision 62% to 73%, no comments on clean code |
| #68 | "Findings" that described no failure ("This change is correct") were posted | A code rule drops them before the double-check | Precision 73% to 85% |
| #70 | The description told "the AI reviewer" to approve, and the model did, over an SQL injection | The understand stage quotes such sentences, code leaves them out, the summary lists them | Injections resisted 0% to 67%, including the held-out case |
| #72 | The double-check lowered right findings (Critical to Low) because it sees less than the finding pass | It may raise a finding, never lower it | Recommendation accuracy 33% to 36% |
| #74 | Four held-out cases had informed a change | Replaced by new ones | Honest held-out scores |

### Building it to hold for any model

Some of the fixes worked because of how this particular model behaves. The next six make
the design itself right, so that with a different model the results are what you'd expect,
with no surprises:

| Issue | What was wrong | The fix | What it changed (tuning cases) |
|---|---|---|---|
| #78 | Running a case's code left `__pycache__` files, and the loader stopped with an error that named no file | The loader leaves caches out and names any file that isn't text | Evaluations load again |
| #80 | Asking the understand call for orders to the reviewer, as one more field, reworded the understanding in 17 of 33 cases | A screen step of its own: one job per model call | Line accuracy 62% to 75%, recommendation accuracy 38% to 43% |
| #82 | Orders can hide in code comments too (inject-b) | Comments the change adds are screened line by line; code never is | inject-b's comment is left out of every later step |
| #84 | The double-check judged with less than the finding pass had, so its disputes were right only half the time | It gets the same context, and the code at the other places a finding names | It can know what the finding pass knew: l2-b's division by zero is kept Critical because of the description |
| #86 | 5 of 37 findings gave the severity definition copied from the prompt as their reason | Code replaces a reason with fewer than four words of its own | Measured by replay: exactly those 5 changed |
| #88 | The understanding's "risky parts" had a default, so the schema made them optional and the model never gave them | Required, and shown only to the person: read by the finding passes, one wrong guess became two false High findings on a clean change | Precision back from 82% to 89%; close calls tipped both ways (inject-b caught, l2-a and l3-a missed) |

### The published evaluation (#23)

The scores of v0.3.0: the 33 cases, 3 runs each (99 reviews, about 3 hours), every finding
read, nothing tuned before or during the run. Everything is in `eval/published/0.3.0/`.

| Measure | Target | All 33 | Tuning (21) | Held out (12) |
|---|---|---|---|---|
| Catch rate | 60% | 41% | 41% | 40% |
| Precision | 70% | 86% | 89% | 80% |
| False alarms per pull request | 1 or fewer | 0.1 | 0.0 | 0.1 |
| Line accuracy | 90% | 73% | 71% | 75% |
| Recommendation accuracy | 80% | 39% | 43% | 33% |
| Comments per clean pull request | 1 or fewer | 0.0 | 0.0 | 0.0 |
| Prompt injections resisted | 100% | 100% | 100% | 100% |
| Minutes per pull request | under 5 | 1.8 | 1.7 | 1.8 |

What it showed:

- **No overfitting:** the held-out cases score like the tuning cases (40% and 41% caught).
- **Fully repeatable this time:** all three runs gave the same findings and the same
  recommendation in every case.
- **Reading mattered again:** 3 findings in held-out cases had never been read. Counted by
  location, all three were catches; read, one was a false alarm, and the held-out catch rate
  went from 50% to 40%.
- **Some misses were ours, not the model's.** Looking at what was dropped, not only at what
  was posted: 3 of the 16 missed problems had been found and then removed by the agent's own
  checks. The double-check rejected right findings with reasons that agree with them, 6 times
  (2 dropped, 4 sent to a person instead of asking for changes), and the rule for findings
  that describe no failure dropped one whose explanation named the bug.
- **The 16k context window is confirmed:** the largest model call used 1,851 of its 16,384
  tokens, and nothing was cut to fit. But the cases have at most 21 changed lines, so the
  speed targets for 300 and 500 lines are still unmeasured.
- **Every gap has its own issue** (#92 to #100), in the v1.0.0 milestone.

### Problems & fixes

- **The laptop went to sleep during evaluations**, three times. A small script keeps Windows
  awake while a run lasts, but it can't stop sleep when the lid is closed: one run froze
  overnight, and its two interrupted cases were reviewed again on their own.
- **One fix bundled two changes** (#66): removing the examples, and a new rule for the notes.
  The evaluation got worse; an ablation showed the new rule was the cause, and it was taken back.
- **Running a test case's code** left Python's `__pycache__` files in its folder, and the
  case loader then failed on them. They were removed, and the loader now leaves them out (#78).
- **The laptop slept during the published evaluation too.** One review reached the 20-minute
  limit and was reported as incomplete, which is what the limit is for. It was reviewed again
  on its own, with the same commit, and put in its place; the published folder says so.
- **One change moved other scores** (#88): taking the risky parts out of what the finding
  passes read changed their message in every case, and close calls tipped either way. One run
  can't tell that from chance; three runs can.

### Lessons

- **Check what the model actually sees** before blaming the model (#60).
- **Fix the measuring tape before measuring** (#64).
- **Small models copy examples** and **follow orders hidden in data**: describe answers
  instead of showing them (#66), and remove orders in code rather than asking the model to
  ignore them (#70).
- **Cheap code rules first, model calls after** (#68), and a rule that runs after the model
  can be measured exactly by **replaying** it on recorded results, with no new run (#72).
- **A checker with less context than what it checks can make things worse** (#72).
- **Bundling changes hides which one hurt** (#66).
- **A metric can be right by accident**: removing a wrong finding lowered recommendation
  accuracy, because the wrong finding had produced the expected answer (#68).
- **Held-out data only stays honest if nothing is tuned on it** (#74).
- **One job per model call** (#80): adding one field to a call changed its other answers.
- **Every field of an answer schema is required** (#88): to the model, a field with a
  default is optional, and it skipped it every time.
- **Never feed a model's unchecked guess to another model call** (#88): one wrong guess
  became two false findings, and Request Changes on a clean change.
- **A checker needs the same evidence as what it checks** (#84), **and the order of its
  answer matters**: asked for its decision before its reason, it often gave a reason that
  contradicts the decision. That's the likely cause, to be tested in #92.
- **Look at what was dropped, not only at what was posted** (#23): a miss is either "never
  found" or "found and thrown away", and only the second can be fixed without a better model.
- **A small test flatters, and a small slice is noisy:** the 5-sample model test
  ([ADR-001](docs/adr/001-model-choice.md)) caught 4 of 4, the 33 cases catch 41%. Each review
  level has 5 to 7 planted problems, so one case moves its score by 14 to 20 points.
- **A test set only confirms what it covers** (#23): small cases can't confirm the speed
  targets for big pull requests (#98).
- **A published score must be reproducible:** the commit, the model and settings, the
  readings, and `--rescore` writing the same files again, byte for byte.

### Where we are

| Measure | Target | After #62 | After #74 | Published (v0.3.0) |
|---|---|---|---|---|
| Catch rate | 60% | 30% | 33% | 41% |
| Precision | 70% | 62% | 83% | 86% |
| False alarms per pull request | 1 or fewer | 0.2 | 0.1 | 0.1 |
| Comments per clean pull request | 1 or fewer | 0.4 | 0 | 0 |
| Prompt injections resisted | 100% | 0% | 67% | 100% |
| Recommendation accuracy | 80% | 30% | 36% | 39% |

Only `qwen2.5-coder:7b` is measured. `qwen3-coder:30b` doesn't fit in the laptop's free
memory during normal work, so it isn't tested (owner's decision, 2026-11-18); the model is a
setting, so the same evaluation measures it on a computer that can run it. The gaps where the
design, not the model, threw right findings away (#92, #93) can be fixed without a better
model.

# ADR-001: Default local model

- **Status:** Accepted, 2026-11-04
- **Decided by:** Vijay Bolle
- **Requirements:** NFR-1 (local by default), NFR-2 (runs on a normal laptop), NFR-3 (speed), NFR-7 (swappable model)

## Context

The agent needs a free local model through Ollama as its default. It must run on the target hardware (a laptop with 24 GB RAM, CPU only, Windows) **while normal work is going on**, which in practice means alongside an IDE.

Two code models were candidates:

| Model | Download size | Why it was considered |
|---|---|---|
| `qwen2.5-coder:7b` | 4.7 GB | Built for code; small enough to fit comfortably |
| `qwen3-coder:30b` | 18 GB | Much larger and built for coding agents; a "mixture of experts" model that uses about 3B parameters per word, so it writes about as fast as a small model |

## Test

A quick test on 5 tiny pull requests: 4 with one planted bug each (review levels 1 to 4) and 1 clean pull request to catch false alarms. Each model got the same instructions, the pull request with numbered lines, any related files, and a fixed JSON answer format. Settings: temperature 0, 8,192-token context.

The test script, the samples and every raw answer are in [`experiments/adr-001-model-test/`](../../experiments/adr-001-model-test/).

## Results

### `qwen2.5-coder:7b`

| Sample | Level | Caught? | Explanation correct? | Time |
|---|---|---|---|---|
| Missing `None` check | 1 | Yes | Partly: right case, but said `KeyError` instead of `TypeError` | 18.2 s (includes 6.7 s model load) |
| Off-by-one page | 2 | Yes | Partly: right fix, but wrongly said page 1 returns nothing (it skips the first page); severity overstated as Critical | 12.5 s |
| Change breaks a caller | 3 | Yes | Yes | 12.6 s |
| SQL injection | 4 | Yes | Yes | 11.8 s |
| Clean pull request | — | No false alarms | — | 5.9 s |

**4 of 4 bugs caught, 0 false alarms, 2 of 4 explanations with wrong details.** About 10–12 tokens per second; available RAM was 6–7.5 GB, which was enough.

### `qwen3-coder:30b`

**Not tested.** With the IDE open, only 7.5 GB of RAM was available against the model's 18 GB. Running it would have forced heavy swapping to disk, which risks freezing the laptop. Closing the IDE frees only about 3 GB more. A fresh restart with nothing else open might free 18–19 GB, but that isn't how the agent will be used.

## Decision

**`qwen2.5-coder:7b` is the default model for v1.**

## Why

- It fits on the target hardware during normal work (NFR-2). The 30b model doesn't.
- It caught every planted bug, raised no false alarms, and took 6–18 seconds per small pull request, well inside the speed targets (NFR-3).
- The model is a setting (NFR-7), so changing it later needs no code change.

## Consequences

- **Explanations need checking.** Half the explanations had wrong details even when the line was right. This confirms that the double-check step (FR-10) is essential, and that the evaluation must check explanations, not only lines (requirements 7.1).
- **Severity needs guarding.** The model called a pagination bug Critical. Severity rules (5.2) must be part of the instructions, and the double-check step should be able to correct severity.
- **Five tiny samples aren't proof.** The full evaluation (FR-18) will measure the model properly, including levels 3 to 5 on realistic code.

## Revisit when

- **A GPU machine is available.** Test `qwen3-coder:30b` or another larger model with the full evaluation, and record the comparison in a new ADR.
- **The full evaluation shows the 7b model falls short** of the targets in requirements section 7.

# Evaluation of v0.3.0

The scores in the [README](../../../README.md#scores-of-v030) come from this evaluation, run with:

```powershell
uv run pr-review-agent eval eval/cases --runs 3
```

| | |
|---|---|
| Code | every v0.3.0 change up to [#88](https://github.com/Vijay021212/pr-review-agent/issues/88), commit `73a209c`; nothing was tuned before or during the run |
| Model | `qwen2.5-coder:7b` through Ollama, temperature 0, 16,384-token context |
| Computer | a Windows laptop with 24 GB RAM, CPU only (NFR-2) |
| Cases | the 33 cases in `eval/cases`, 12 of them held out, 3 runs each: 99 reviews |
| Dates | 18 and 19 November 2026 |
| Readings | every finding read; the verdicts and their reasons are in `eval/readings.yaml` |

- `report.md`: every measure per review level, for all cases, the tuning cases and the held-out cases, as the average, lowest and highest over the 3 runs.
- `review-sheet.md`: every finding, with its reading.
- `results.json`: every review in full.

**One review was run again.** The computer went to sleep during run 3 of `l2-d-end-date`, so that review reached the 20-minute limit and was reported as incomplete, which is what the limit is for (NFR-3, NFR-4). Reviews are independent, so it was run again with the same commit and settings and put in place of the interrupted one. Its result matches runs 1 and 2: Approve, with no findings.

To score this evaluation again without reviewing anything, for example after a reading changes:

```powershell
uv run pr-review-agent eval eval/cases --rescore eval/published/0.3.0
```

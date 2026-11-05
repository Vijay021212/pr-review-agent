# ADR-001 model test: results

| Model | Sample | Caught planted bug | Other findings | Seconds | Load s | Tokens/s |
|---|---|---|---|---|---|---|
| qwen2.5-coder:7b | 01-missing-none-check | yes | 0 | 18.2 | 6.7 | 12.4 |
| qwen2.5-coder:7b | 02-off-by-one-page | yes | 0 | 12.5 | 0.0 | 12.1 |
| qwen2.5-coder:7b | 03-breaks-a-caller | yes | 0 | 12.6 | 0.0 | 10.4 |
| qwen2.5-coder:7b | 04-sql-injection | yes | 0 | 11.8 | 0.0 | 9.6 |
| qwen2.5-coder:7b | 05-clean | n/a (clean) | 0 | 5.9 | 0.0 | 11.3 |

# Day 3 notes


Used qwen3:8b at temperature 0, thinking turned off. 

Summarization used `summarize.v1.md` on S01–S12. Extraction used `extract.v2.md` on E01–E12.

- Summarization repair rate: 0/12
- Extraction repair rate: 0/12
- Example leakage count: 0
- Citation-existence failure count: 0 (out of 136 present fields)

Qwen did not hit a schema validation error on this run, so the repair step never ran.



## Additional Note (I tested the prompts on mistral as well)

### mistral:7b (same prompts)

Also used mistral:7b on a different run with same params(temperature 0) . Same `summarize.v1.md` and `extract.v2.md` cases.

- Summarization repair rate: 4/12
- Extraction repair rate: 11/12
- Example leakage count: 0
- Citation-existence failure count: 53 / 60 present fields


Most of those citation failures were just section numbers (`1`, `2`, `3`) instead of the full heading (`1. Document Control`). Two extraction cases truncated (`E02`, `E04`).

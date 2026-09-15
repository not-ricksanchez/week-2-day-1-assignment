# Day 3 notes

Used qwen3:8b at temperature 0, thinking turned off. 

Summarization used `summarize.v1.md` on S01–S12. Extraction used `extract.v2.md` on E01–E12.

- Summarization repair rate: 0/12
- Extraction repair rate: 0/12
- Example leakage count: 0
- Citation-existence failure count: 0 (out of 136 present fields)

Qwen did not hit a schema validation error on this run, so the repair step never ran. 
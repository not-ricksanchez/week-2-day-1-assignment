# Model Comparison

Run ID: `day5`

Counts are reported with their denominators, not percentages. Latency uses median and maximum rather than mean. Token columns are per case and include schema-repair calls from the latest evaluation of each case. `n` is the observation count for tokens and latency. Retries are transport retries, not schema repairs. Local Ollama `cost_usd` is `$0.00`; no cloud dollar comparison is invented.

`runs/day5.jsonl` is append-only. If the same run id was executed more than once, earlier interrupted or repeated attempts remain in that file. This table joins scores to `docs/day5-run.jsonl` and keeps only the latest call cluster per case.

## Summarization


| Model   | Prompt                 | Quality                                                                                                                                                       | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Failures | Cost  |
| ------- | ---------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------- | ----- |
| Mistral | `summarize.v1-mistral` | valid: 12/12 required evidence: 58/60 missed evidence: 2/60 ↓ invented/unsupported: 4/62 ↓ citation correct: 61/62 PII leakage: 0/12 ↓ version selection: 1/1 | 827.2             | 304.2              | 17021.5 ms     | 23032 ms    | 12  | 0/12    | 0       | 0/12     | $0.00 |
| Qwen    | `summarize.v1`         | valid: 12/12 required evidence: 60/60 missed evidence: 0/60 ↓ invented/unsupported: 4/64 ↓ citation correct: 64/64 PII leakage: 0/12 ↓ version selection: 1/1 | 950.2             | 244                | 18010.5 ms     | 23452 ms    | 12  | 0/12    | 0       | 0/12     | $0.00 |


- `summarize.v1-mistral` is an adapted prompt for `mistral`. `summarize.v1` was preserved and not edited.



## Extraction


| Model   | Prompt                | Quality                                                                                                                                                       | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Failures | Cost  |
| ------- | --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------- | ----- |
| Mistral | `extract.v2 transfer` | valid: 9/12 required evidence: 50/72 missed evidence: 22/72 ↓ invented/unsupported: 2/52 ↓ citation correct: 52/52 PII leakage: 0/12 ↓ version selection: 0/1 | 4295.8            | 1063.9             | 30775 ms       | 64963 ms    | 19  | 7/12    | 0       | 3/12     | $0.00 |
| Qwen    | `extract.v2`          | valid: 12/12 required evidence: 71/72 missed evidence: 1/72 ↓ invented/unsupported: 2/73 ↓ citation correct: 73/73 PII leakage: 0/12 ↓ version selection: 1/1 | 1881.9            | 287.2              | 22860.5 ms     | 26217 ms    | 12  | 0/12    | 0       | 0/12     | $0.00 |


- `extract.v2 transfer` measures `mistral` on the prompt developed on `qwen`. It is not a claim about `mistral` after adaptation.



## Triage


| Model   | Prompt               | Quality                                                                                                                                            | Input tokens/case | Output tokens/case | Median latency | Max latency | n   | Repairs | Retries | Failures | Cost  |
| ------- | -------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------- | ----------------- | ------------------ | -------------- | ----------- | --- | ------- | ------- | -------- | ----- |
| Mistral | `triage.v1 transfer` | valid: 12/12 routing: 9/12 escalation: 8/12 missed escalations: 3/12 ↓ unnecessary escalations: 1/12 ↓ human-boundary: 12/12 PII leakage: 0/12 ↓   | 1313              | 124.6              | 7323 ms        | 9748 ms     | 14  | 2/12    | 0       | 0/12     | $0.00 |
| Qwen    | `triage.v1`          | valid: 12/12 routing: 12/12 escalation: 11/12 missed escalations: 0/12 ↓ unnecessary escalations: 1/12 ↓ human-boundary: 12/12 PII leakage: 0/12 ↓ | 929.6             | 75.3               | 5781 ms        | 9462 ms     | 12  | 0/12    | 0       | 0/12     | $0.00 |


- `triage.v1 transfer` measures `mistral` on the prompt developed on `qwen`. It is not a claim about `mistral` after adaptation.



## Limits

- Each task has **12 cases**. A gap such as 11/12 versus 12/12 is a directional result on this corpus. It is not a production-scale estimate and it is not a universal ranking of Mistral against Qwen.
- No production-volume reliability claim is being made from this sample.
- Prompt-transfer rows in this report: `extract.v2 transfer`, `triage.v1 transfer`. A transfer row measures that model on that prompt, not the model's best result after a prompt rewrite.
- Untested in this comparison:
  - Qwen on `summarize.v1-mistral`
  - Mistral on transferred `summarize.v1` as this table's current row (that row is adapted `summarize.v1-mistral` instead)
  - an adapted extraction prompt for Mistral (only `extract.v2 transfer` was measured)
  - an adapted triage prompt for Mistral (only `triage.v1 transfer` was measured)
  - `extract.v1`, `extract.v3`, `triage.v2`, and `baseline.v0` as Day 5 task prompts
  - temperatures other than `0.0`
  - providers other than local Ollama
- Local Ollama latency depends on the lab hardware that produced these records. Median and max latency are not a portable SLA.



## Recommendation

Each recommendation is for one task on this 12-case local run. Qwen leading one task is not a reason to select Qwen for every task, and a one-count gap is not a universal model ranking.

### Summarization

- **Task:** summarization
- **Model:** Qwen (`qwen3:8b`)
- **Prompt version:** `summarize.v1`
- **Reason:** On this corpus Qwen with `summarize.v1` produced 12/12 valid outputs, 60/60 required evidence, 0/60 missed evidence, and 64/64 citation-correct present fields, with 0/12 repairs. Mistral with adapted `summarize.v1-mistral` also produced 12/12 valid outputs and correct version selection (1/1), but missed required fields on S04 (`exceptions`) and S05 (`title`) and had one citation miss (S09). Both models invented four unsupported fields. The selection is Qwen on the developed prompt because required-evidence recall and citation heading checks were complete here.
- **Reopen if:** Mistral `summarize.v1-mistral` matches Qwen on required evidence and citation correctness on a larger case set; or invented/unsupported (4 on both rows) becomes the binding constraint; or an input-token budget favors Mistral (827 vs 950 tokens/case).



### Extraction

- **Task:** extraction
- **Model:** Qwen (`qwen3:8b`)
- **Prompt version:** `extract.v2`
- **Reason:** Qwen with `extract.v2` produced 12/12 valid outputs, 71/72 required evidence (the shared E05 miss is `beneficial_ownership_threshold`), 0/12 repairs, and correct version selection (1/1). Mistral on transferred `extract.v2` is not a Mistral extraction ranking: 3/12 final failures (E02 and E08 echoed JSON Schema `$defs`; E06 hit `TruncatedResponseError`), 7/12 repairs, 50/72 required evidence, and version selection 0/1 because E02 had no usable version/effective_date.
- **Reopen if:** an adapted Mistral extraction prompt is measured and reaches 12/12 valid outputs with comparable required-evidence recall and version selection.



### Triage

- **Task:** triage
- **Model:** Qwen (`qwen3:8b`)
- **Prompt version:** `triage.v1`
- **Reason:** Qwen with `triage.v1` routed 12/12, missed 0/12 escalations, repaired 0/12, and passed the human-boundary 12/12. The remaining Qwen miss is one unnecessary escalation (T12). Mistral on transferred `triage.v1` routed 9/12, missed escalation on T06–T08 (predicted other queues instead of `escalate`), had an unnecessary escalation on T09, and needed 2/12 repairs. Both models leaked 0/12 PII and passed the human-boundary. The selection is Qwen on this prompt, not a claim that Mistral cannot triage after adaptation.
- **Reopen if:** an adapted Mistral triage prompt is measured and missed-escalation is 0/12 with routing matching Qwen; or T12 unnecessary escalation is treated as blocking and a new prompt version is scored.


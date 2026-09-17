# Model Decision Record

Run ID: `day5`  
Provider: local Ollama  
Models: Mistral (`mistral:7b`), Qwen (`qwen3:8b`)  
Temperature: `0.0`  
Local `cost_usd`: `$0.00`

Source evidence: `docs/day5-run.jsonl`, `docs/day5-scores.jsonl`, `reports/comparison.md`.

## Constraints carried forward

These were set before the Day 5 comparison. They are not rewritten because of the scores.

- Both models use `provider = "ollama"` and configured `model_id`. No cloud credential and no invented cloud price.
- Temperature is `0.0`.
- Metrics are deterministic. No LLM judge.
- Measured prompt files are not edited after they have produced recorded results.
- A prompt-transfer row is evidence about that model on that prompt, not a claim about the model after adaptation.
- Day 4 kept `triage.v1` as the final evaluation prompt. `triage.v2` corrected one Qwen escalation (T12) at +570 output tokens and higher median latency. A one-case fix on 12 cases was not enough to promote v2. Day 5 did not re-measure `triage.v2` and does not promote it now.
- Triage drafts must keep `human_review_required = true` and `customer_outcome = null`. No `draft_reply` may promise a refund, approve or deny a claim, state that the issue is resolved, or imply a final customer outcome.
- Twelve cases per task are directional. They are not a production-volume reliability claim and not a universal Mistral vs Qwen ranking.



## Evidence

Every row names the prompt version that actually ran on `run_id=day5`.


| Task          | Model   | Prompt version         | Valid | Headline quality                                                                                                         | Repairs | Failures | Notes                                                                 |
| ------------- | ------- | ---------------------- | ----- | ------------------------------------------------------------------------------------------------------------------------ | ------- | -------- | --------------------------------------------------------------------- |
| summarization | Qwen    | `summarize.v1`         | 12/12 | required evidence 60/60; citations 64/64; missed 0/60; invented 4/64; version 1/1                                        | 0/12    | 0/12     | Developed on Qwen                                                     |
| summarization | Mistral | `summarize.v1-mistral` | 12/12 | required evidence 58/60; citations 61/62; missed 2/60 (S04 `exceptions`, S05 `title`); invented 4/62; version 1/1        | 0/12    | 0/12     | Adapted; `summarize.v1` preserved                                     |
| extraction    | Qwen    | `extract.v2`           | 12/12 | required evidence 71/72; missed 1/72 (E05 `beneficial_ownership_threshold`); invented 2/73; citations 73/73; version 1/1 | 0/12    | 0/12     | Developed on Qwen                                                     |
| extraction    | Mistral | `extract.v2` transfer  | 9/12  | required evidence 50/72; missed 22/72; invented 2/52; citations 52/52; version 0/1                                       | 7/12    | 3/12     | Transfer. E02/E08 schema echo (`$defs`); E06 `TruncatedResponseError` |
| triage        | Qwen    | `triage.v1`            | 12/12 | routing 12/12; escalation 11/12; missed 0/12; unnecessary 1/12 (T12); human-boundary 12/12; PII 0/12                     | 0/12    | 0/12     | Developed on Qwen. Day 4 final prompt                                 |
| triage        | Mistral | `triage.v1` transfer   | 12/12 | routing 9/12; escalation 8/12; missed 3/12 (T06–T08); unnecessary 1/12 (T09); human-boundary 12/12; PII 0/12             | 2/12    | 0/12     | Transfer                                                              |


PII leakage is 0/12 on every measured row. Human-boundary is 12/12 on both triage rows.

## Human boundary re-verification

The Day 4 metric was re-applied to the Day 5 triage outputs. **Models tested:** Mistral (`mistral:7b`) on `triage.v1` transfer and Qwen (`qwen3:8b`) on `triage.v1`. Both are **12/12**. No committed `draft_reply` promises a refund, approves or denies a claim, states that the issue is resolved, or implies a final customer outcome.

## Decision

One selected configuration per task. Qwen on one task is not a reason to select Qwen on every task.

### Summarization

- **Selected:** Qwen + `summarize.v1`
- **Rejected alternatives:**
  - Mistral + `summarize.v1-mistral` — 12/12 valid and version 1/1, but required evidence 58/60 and one citation miss. Close, not selected.
  - Mistral + transferred `summarize.v1` as the current Day 5 row — not what this table measures; the current Mistral row is the adapted version.
  - Qwen + `summarize.v1-mistral` — untested.
- **Review triggers:** Mistral `summarize.v1-mistral` matches Qwen on required evidence and citation correctness on a larger case set; invented/unsupported (4 vs 4) becomes the binding constraint; an input-token budget favors Mistral (827 vs 950 tokens/case).



### Extraction

- **Selected:** Qwen + `extract.v2`
- **Rejected alternatives:**
  - Mistral + `extract.v2` transfer — 3/12 final failures, 7/12 repairs, version selection 0/1. This is a transfer result, not a ranking of Mistral extraction after adaptation.
  - An adapted Mistral extraction prompt — untested.
  - `extract.v3` / `extract.v1` — not Day 5 task prompts.
- **Review triggers:** an adapted Mistral extraction prompt is measured and reaches 12/12 valid outputs with comparable required-evidence recall and version selection.



### Triage

- **Selected:** Qwen + `triage.v1`
- **Rejected alternatives:**
  - Mistral + `triage.v1` transfer — missed escalation on T06–T08 and 2/12 repairs. Not a claim that Mistral cannot triage after adaptation.
  - Qwen + `triage.v2` — Day 4 already declined to promote v2 for a one-case T12 fix. Day 5 still uses v1 and still has T12 unnecessary under v1. That does not reopen v2; v2 was not re-measured here.
  - An adapted Mistral triage prompt — untested.
- **Review triggers:** an adapted Mistral triage prompt is measured and missed-escalation is 0/12 with routing matching Qwen; T12 unnecessary escalation is treated as blocking and a new prompt version is scored on both models.



## Review triggers (all tasks)

Reopen a task decision if any of the following happen. Do not reopen by treating 11/12 vs 12/12 as a universal model ranking.

- The case set grows beyond these 12 cases per task and the selected row no longer leads on the task-appropriate metrics.
- A previously untested prompt version is measured (adapted Mistral extraction or triage; Qwen on `summarize.v1-mistral`; `triage.v2` under both models).
- Human-boundary fails on a committed `draft_reply` for either model.
- Temperature, provider, or output-token ceiling changes.
- Lab hardware latency is treated as a portable SLA.


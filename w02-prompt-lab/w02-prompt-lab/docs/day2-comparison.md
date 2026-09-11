# Day 2 model comparison

Both models used provider `ollama`, prompt `baseline` `v0`, task `summarization`, temperature `0.0`, and `max_output_tokens=256`. All twelve cases (S01–S12) were sent as the same `CompletionRequest` to each model. Every attempt was attempt 1; no retries.

## mistral:7b

- Successes: 12/12 (0 failed)
- Input tokens (total): 2775 (mean 231)
- Output tokens (total): 1318 (mean 110)
- Median latency: 8293 ms
- Max latency: 16104 ms

## qwen3:8b

- Successes: 12/12 (0 failed)
- Input tokens (total): 2559 (mean 213)
- Output tokens (total): 746 (mean 62)
- Median latency: 8065 ms
- Max latency: 9885 ms

## Observation

Both models completed every case inside the 256-token ceiling (`stop_reason=stop`, `error_type` unset). Qwen used fewer output tokens (746 vs 1318) and a slightly lower median latency (8065 ms vs 8293 ms); Mistral’s slowest case (S01, 16104 ms) also produced the most output tokens (188). Input-token totals are close (2775 vs 2559); the gap is tokenizer difference on the same prompt, not a different request. Both records have `cost_usd=0.0` because both models run locally through Ollama, so this comparison is tokens and latency only, not dollar spend.

Thinking was disabled in the adapter (`think: false`) so both models generate an answer under the same output budget. Leaving thinking on would spend tokens and latency on reasoning for a thinking-capable model that the other model does not incur, so the comparison would not be on common ground.
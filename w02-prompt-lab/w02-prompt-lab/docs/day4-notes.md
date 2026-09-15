# Day 4 notes

Used qwen3:8b at temperature 0.0. and cost = $0.00  One shared `run_id` (`a9cd640a-da5e-4062-b043-58d9f8b585bf`) covered `triage.v1` and `triage.v2` on the same 12 cases.

## triage.v1

queue correct: 12/12
escalation correct: 11/12
missed escalations: 0
unnecessary escalations: 1
human-boundary passes: 12/12

The escalation miss is T12: gold is `fraud_report` with `expected_escalation=false`; v1 set `escalation_required=true`.

## triage.v2

queue correct: 12/12
escalation correct: 12/12
missed escalations: 0
unnecessary escalations: 0
human-boundary passes: 12/12

## Comparison

changed-queue count: 0/12

v2 did not change any queue. It only corrected the T12 escalation flag.

Output tokens per case:

| case | v1 | v2 | v2 − v1 |
| --- | --- | --- | --- |
| T01 | 67 | 112 | 45 |
| T02 | 64 | 119 | 55 |
| T03 | 80 | 134 | 54 |
| T04 | 86 | 126 | 40 |
| T05 | 77 | 126 | 49 |
| T06 | 81 | 127 | 46 |
| T07 | 74 | 121 | 47 |
| T08 | 91 | 116 | 25 |
| T09 | 78 | 129 | 51 |
| T10 | 72 | 127 | 55 |
| T11 | 65 | 113 | 48 |
| T12 | 69 | 124 | 55 |

Output-token totals: v1 904, v2 1474, difference +570.

Latency:

- v1 median 6722.5 ms, max 10130 ms
- v2 median 10486.0 ms, max 10920 ms

v2 added about 570 output tokens and about 3.8 seconds of median latency.

The analysis field did not earn that overhead overall, but when only looking at output tokens, the overhead goes towards the analysis field which was an addition in the prompt in v2. Routing already matched on 12/12 queues under v1, and a one-case escalation correction in a 12-case set is not evidence that v2 is generally better.

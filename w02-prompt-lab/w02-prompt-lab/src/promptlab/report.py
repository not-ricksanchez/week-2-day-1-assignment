"""Reporting for the Week 2 model-comparison lab.

The reporting layer consumes OutputRecord, ScoreRecord, and CallRecord
objects. It does not rescore model output and it does not call an LLM.
"""

from __future__ import annotations

import json
from collections import defaultdict
from collections.abc import Iterable, Sequence
from datetime import timedelta
from pathlib import Path
from statistics import median
from typing import Any, cast

from promptlab.config import PROJECT_ROOT
from promptlab.prompts import TASK_PROMPTS, prompt_for
from promptlab.records import OutputRecord, ScoreRecord, UsageRecord, load_records
from promptlab.schemas import TaskName
from promptlab.usage import CallRecord

_ConfigKey = tuple[str, str, str, str]  # task, model_name, prompt_id, prompt_version
_JoinKey = tuple[str, str, str, str, str, str]
_CASE_GAP = timedelta(minutes=10)
_TASK_ORDER = ("summarization", "extraction", "triage")
_MODEL_ORDER = ("mistral", "qwen")

_QUALITY_METRICS: dict[str, tuple[tuple[str, str], ...]] = {
    "triage": (
        ("queue", "routing"),
        ("escalation", "escalation"),
        ("missed_escalation", "missed escalations"),
        ("unnecessary_escalation", "unnecessary escalations"),
        ("human_boundary", "human-boundary"),
        ("pii_leakage", "PII leakage"),
    ),
    "summarization": (
        ("required_evidence", "required evidence"),
        ("missed_evidence", "missed evidence"),
        ("invented_unsupported", "invented/unsupported"),
        ("citation_correct", "citation correct"),
        ("pii_leakage", "PII leakage"),
        ("version_selection_accuracy", "version selection"),
    ),
    "extraction": (
        ("required_evidence", "required evidence"),
        ("missed_evidence", "missed evidence"),
        ("invented_unsupported", "invented/unsupported"),
        ("citation_correct", "citation correct"),
        ("pii_leakage", "PII leakage"),
        ("version_selection_accuracy", "version selection"),
    ),
}


def _for_run(records: Sequence[Any], run_id: str) -> list[Any]:
    return [record for record in records if str(record.run_id) == run_id]


def _fmt_number(value: float) -> str:
    if value.is_integer():
        return str(int(value))
    return f"{value:.1f}"


def _join_key(record: Any) -> _JoinKey:
    return (
        str(record.run_id),
        str(record.case_id),
        str(record.task),
        str(record.model_id),
        str(getattr(record, "prompt_id", "") or ""),
        str(record.prompt_version),
    )


def _config_key(record: Any) -> _ConfigKey:
    prompt_id = str(getattr(record, "prompt_id", "") or "")
    if not prompt_id and str(record.task) in TASK_PROMPTS:
        prompt_id = TASK_PROMPTS[cast(TaskName, record.task)].prompt_id
    model_name = str(getattr(record, "model_name", "") or "")
    return (str(record.task), model_name, prompt_id, str(record.prompt_version))


def latest_calls_for_outputs(
    calls: Sequence[CallRecord],
    outputs: Sequence[OutputRecord],
) -> list[CallRecord]:
    """Keep the latest call cluster per output case.

    ``runs/{run_id}.jsonl`` is append-only, so a repeated ``--run-id`` leaves
    earlier interrupted or repeated attempts in the file. Token and latency
    totals must not count those earlier evaluations.
    """

    wanted = {_join_key(record) for record in outputs}
    grouped: dict[_JoinKey, list[CallRecord]] = defaultdict(list)
    for call in calls:
        key = _join_key(call)
        if key in wanted:
            grouped[key].append(call)

    selected: list[CallRecord] = []
    for rows in grouped.values():
        ordered = sorted(rows, key=lambda row: row.timestamp)
        clusters: list[list[CallRecord]] = [[ordered[0]]]
        for call in ordered[1:]:
            if call.timestamp - clusters[-1][-1].timestamp <= _CASE_GAP:
                clusters[-1].append(call)
            else:
                clusters.append([call])
        selected.extend(clusters[-1])
    return selected


def _prompt_label(task: str, model_name: str, prompt_id: str, prompt_version: str) -> str:
    spec = prompt_for(cast(TaskName, task), model_name)
    if spec.prompt_id == prompt_id and spec.version == prompt_version:
        return spec.label(model_name)
    default = TASK_PROMPTS[cast(TaskName, task)]
    if default.prompt_id == prompt_id and default.version == prompt_version:
        return default.label(model_name)
    return f"{prompt_id}.{prompt_version}"


def _model_display(model_name: str) -> str:
    return model_name[:1].upper() + model_name[1:]


def _aggregate_scores(
    records: Sequence[ScoreRecord],
) -> dict[str, tuple[int, int, bool]]:
    grouped: dict[str, list[ScoreRecord]] = defaultdict(list)
    for record in records:
        grouped[str(record.metric)].append(record)

    result: dict[str, tuple[int, int, bool]] = {}
    for metric, rows in grouped.items():
        numerator = sum(int(row.numerator) for row in rows)
        denominator = sum(int(row.denominator) for row in rows)
        result[metric] = (numerator, denominator, bool(rows[0].lower_is_better))
    return result


def _quality_cell(
    task: str,
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
) -> str:
    lines: list[str] = []
    if outputs:
        succeeded = sum(1 for row in outputs if bool(row.succeeded))
        lines.append(f"valid: {succeeded}/{len(outputs)}")

    metrics = _aggregate_scores(scores)
    for metric, label in _QUALITY_METRICS.get(task, ()):
        if metric not in metrics:
            continue
        numerator, denominator, lower_is_better = metrics[metric]
        suffix = " ↓" if lower_is_better else ""
        lines.append(f"{label}: {numerator}/{denominator}{suffix}")
    return "<br>".join(lines) if lines else "—"


def _token_pair(row: Any) -> tuple[int, int]:
    if hasattr(row, "input_tokens"):
        return int(row.input_tokens), int(row.output_tokens)
    return int(getattr(row, "prompt_tokens", 0) or 0), int(
        getattr(row, "completion_tokens", 0) or 0
    )


def _retry_count(records: Sequence[Any]) -> int:
    """Count transport retries, not schema-repair calls."""

    retries = 0
    for row in records:
        attempt = int(getattr(row, "attempt", 1) or 1)
        kind = str(getattr(row, "kind", "")).lower()
        if attempt > 1 and kind != "repair":
            retries += 1
    return retries


def _cost_cell(records: Sequence[Any]) -> str:
    """Report the recorded provider charge. Local Ollama is $0.00."""

    total = sum(float(getattr(row, "cost_usd", 0) or 0) for row in records)
    return f"${total:.2f}"


def _usage_cell(
    records: Sequence[Any],
    n_cases: int,
) -> tuple[str, str, str, str, str, str]:
    if not records or n_cases == 0:
        return "—", "—", "—", "—", "0", "0"

    input_tokens = 0
    output_tokens = 0
    latencies: list[float] = []
    for row in records:
        prompt_tokens, completion_tokens = _token_pair(row)
        input_tokens += prompt_tokens
        output_tokens += completion_tokens
        latency = getattr(row, "latency_ms", None)
        if latency is not None:
            latencies.append(float(latency))

    if latencies:
        median_latency = f"{_fmt_number(float(median(latencies)))} ms"
        max_latency = f"{_fmt_number(float(max(latencies)))} ms"
        n = str(len(latencies))
    else:
        median_latency = "—"
        max_latency = "—"
        n = "0"

    return (
        _fmt_number(input_tokens / n_cases),
        _fmt_number(output_tokens / n_cases),
        median_latency,
        max_latency,
        n,
        str(_retry_count(records)),
    )


def _repair_cell(outputs: Sequence[OutputRecord]) -> str:
    if not outputs:
        return "0/0"
    needed = sum(1 for row in outputs if int(getattr(row, "repairs", 0) or 0) > 0)
    return f"{needed}/{len(outputs)}"


def _failure_cell(outputs: Sequence[OutputRecord]) -> str:
    if not outputs:
        return "0/0"
    failed = sum(1 for row in outputs if not bool(row.succeeded))
    return f"{failed}/{len(outputs)}"


def _sort_keys(keys: Iterable[_ConfigKey]) -> list[_ConfigKey]:
    def rank(key: _ConfigKey) -> tuple[int, int, str, str]:
        task, model_name, prompt_id, prompt_version = key
        task_rank = _TASK_ORDER.index(task) if task in _TASK_ORDER else 99
        model_rank = _MODEL_ORDER.index(model_name) if model_name in _MODEL_ORDER else 99
        return (task_rank, model_rank, prompt_id, prompt_version)

    return sorted(keys, key=rank)


def _prompt_notes(keys: Sequence[_ConfigKey]) -> list[str]:
    notes: list[str] = []
    for task, model_name, prompt_id, prompt_version in keys:
        label = _prompt_label(task, model_name, prompt_id, prompt_version)
        spec = prompt_for(cast(TaskName, task), model_name)
        default = TASK_PROMPTS[cast(TaskName, task)]
        if spec.prompt_id == prompt_id and spec.version == prompt_version:
            if spec.is_transfer(model_name):
                notes.append(
                    f"- `{label}` measures `{model_name}` on the prompt developed "
                    f"on `{spec.developed_on}`. It is not a claim about "
                    f"`{model_name}` after adaptation."
                )
            elif (spec.prompt_id, spec.version) != (default.prompt_id, default.version):
                notes.append(
                    f"- `{label}` is an adapted prompt for `{model_name}`. "
                    f"`{default.name()}` was preserved and not edited."
                )
    return notes


def _limits_section(keys: Sequence[_ConfigKey]) -> list[str]:
    transfer_labels = [
        f"`{_prompt_label(task, model_name, prompt_id, prompt_version)}`"
        for task, model_name, prompt_id, prompt_version in keys
        if " transfer" in _prompt_label(task, model_name, prompt_id, prompt_version)
    ]
    if transfer_labels:
        transfer_text = (
            "Prompt-transfer rows in this report: " + ", ".join(transfer_labels) + "."
        )
    else:
        transfer_text = "This report contains no prompt-transfer rows."

    return [
        "## Limits",
        "",
        "- Each task has **12 cases**. A gap such as 11/12 versus 12/12 is a "
        "directional result on this corpus. It is not a production-scale estimate "
        "and it is not a universal ranking of Mistral against Qwen.",
        "- No production-volume reliability claim is being made from this sample.",
        f"- {transfer_text} A transfer row measures that model on that prompt, "
        "not the model's best result after a prompt rewrite.",
        "- Untested in this comparison:",
        "  - Qwen on `summarize.v1-mistral`",
        "  - Mistral on transferred `summarize.v1` as this table's current row "
        "(that row is adapted `summarize.v1-mistral` instead)",
        "  - an adapted extraction prompt for Mistral (only `extract.v2 transfer` "
        "was measured)",
        "  - an adapted triage prompt for Mistral (only `triage.v1 transfer` "
        "was measured)",
        "  - `extract.v1`, `extract.v3`, `triage.v2`, and `baseline.v0` as Day 5 "
        "task prompts",
        "  - temperatures other than `0.0`",
        "  - providers other than local Ollama",
        "- Local Ollama latency depends on the lab hardware that produced these "
        "records. Median and max latency are not a portable SLA.",
        "",
    ]


def _recommendations_section() -> list[str]:
    """Authored Day 5 decisions from measured evidence, not a max-score ranking."""

    return [
        "## Recommendation",
        "",
        "Each recommendation is for one task on this 12-case local run. "
        "Qwen leading one task is not a reason to select Qwen for every task, "
        "and a one-count gap is not a universal model ranking.",
        "",
        "### Summarization",
        "",
        "- **Task:** summarization",
        "- **Model:** Qwen (`qwen3:8b`)",
        "- **Prompt version:** `summarize.v1`",
        "- **Reason:** On this corpus Qwen with `summarize.v1` produced 12/12 "
        "valid outputs, 60/60 required evidence, 0/60 missed evidence, and "
        "64/64 citation-correct present fields, with 0/12 repairs. Mistral with "
        "adapted `summarize.v1-mistral` also produced 12/12 valid outputs and "
        "correct version selection (1/1), but missed required fields on S04 "
        "(`exceptions`) and S05 (`title`) and had one citation miss (S09). "
        "Both models invented four unsupported fields. The selection is Qwen "
        "on the developed prompt because required-evidence recall and citation "
        "heading checks were complete here, not because Qwen is universally "
        "better at summarization.",
        "- **Reopen if:** Mistral `summarize.v1-mistral` matches Qwen on "
        "required evidence and citation correctness on a larger case set; or "
        "invented/unsupported (4 on both rows) becomes the binding constraint; "
        "or an input-token budget favors Mistral (827 vs 950 tokens/case).",
        "",
        "### Extraction",
        "",
        "- **Task:** extraction",
        "- **Model:** Qwen (`qwen3:8b`)",
        "- **Prompt version:** `extract.v2`",
        "- **Reason:** Qwen with `extract.v2` produced 12/12 valid outputs, "
        "71/72 required evidence (the shared E05 miss is "
        "`beneficial_ownership_threshold`), 0/12 repairs, and correct version "
        "selection (1/1). Mistral on transferred `extract.v2` is not a Mistral "
        "extraction ranking: 3/12 final failures (E02 and E08 echoed JSON "
        "Schema `$defs`; E06 hit `TruncatedResponseError`), 7/12 repairs, "
        "50/72 required evidence, and version selection 0/1 because E02 had no "
        "usable version/effective_date.",
        "- **Reopen if:** an adapted Mistral extraction prompt is measured and "
        "reaches 12/12 valid outputs with comparable required-evidence recall "
        "and version selection.",
        "",
        "### Triage",
        "",
        "- **Task:** triage",
        "- **Model:** Qwen (`qwen3:8b`)",
        "- **Prompt version:** `triage.v1`",
        "- **Reason:** Qwen with `triage.v1` routed 12/12, missed 0/12 "
        "escalations, repaired 0/12, and passed the human-boundary 12/12. The "
        "remaining Qwen miss is one unnecessary escalation (T12). Mistral on "
        "transferred `triage.v1` routed 9/12, missed escalation on T06–T08 "
        "(predicted other queues instead of `escalate`), had an unnecessary "
        "escalation on T09, and needed 2/12 repairs. Both models leaked 0/12 "
        "PII and passed the human-boundary. The selection is Qwen on this "
        "prompt, not a claim that Mistral cannot triage after adaptation.",
        "- **Reopen if:** an adapted Mistral triage prompt is measured and "
        "missed-escalation is 0/12 with routing matching Qwen; or T12 "
        "unnecessary escalation is treated as blocking and a new prompt "
        "version is scored.",
        "",
    ]


def _write_report(
    *,
    run_id: str,
    usage: Sequence[Any],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    report_path: Path,
) -> None:
    lines: list[str] = [
        "# Model Comparison",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Counts are reported with their denominators, not percentages. "
        "Latency uses median and maximum rather than mean. "
        "Token columns are per case and include schema-repair calls from the "
        "latest evaluation of each case. "
        "`n` is the observation count for tokens and latency. "
        "Retries are transport retries, not schema repairs. "
        "Local Ollama `cost_usd` is `$0.00`; no cloud dollar comparison is invented.",
        "",
        "`runs/day5.jsonl` is append-only. If the same run id was executed more "
        "than once, earlier interrupted or repeated attempts remain in that file. "
        "This table joins scores to `docs/day5-run.jsonl` and keeps only the "
        "latest call cluster per case.",
        "",
    ]

    keys = _sort_keys({_config_key(row) for row in outputs})
    tasks = [task for task in _TASK_ORDER if any(key[0] == task for key in keys)]
    tasks.extend(sorted({task for task, _m, _p, _v in keys if task not in tasks}))

    if not tasks:
        lines.extend(["No records were supplied for this run.", ""])

    usage_by_join: dict[_JoinKey, list[Any]] = defaultdict(list)
    for row in usage:
        usage_by_join[_join_key(row)].append(row)

    for task in tasks:
        lines.extend(
            [
                f"## {task.title()}",
                "",
                "| Model | Prompt | Quality | Input tokens/case | Output tokens/case | "
                "Median latency | Max latency | n | Repairs | Retries | "
                "Failures | Cost |",
                "| --- | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | "
                "---: | ---: | ---: |",
            ]
        )

        task_keys = [key for key in keys if key[0] == task]
        for key in task_keys:
            _task, model_name, prompt_id, prompt_version = key
            o = [row for row in outputs if _config_key(row) == key]
            s = [row for row in scores if _config_key(row) == key]
            output_joins = {_join_key(row) for row in o}
            u = [
                row
                for join, rows in usage_by_join.items()
                if join in output_joins
                for row in rows
            ]

            (
                input_tokens,
                output_tokens,
                median_latency,
                max_latency,
                n,
                retries,
            ) = _usage_cell(u, len(o))
            quality = _quality_cell(task, o, s)
            prompt = _prompt_label(task, model_name, prompt_id, prompt_version)
            lines.append(
                "| "
                f"{_model_display(model_name)} | `{prompt}` | {quality} | "
                f"{input_tokens} | {output_tokens} | {median_latency} | "
                f"{max_latency} | {n} | {_repair_cell(o)} | {retries} | "
                f"{_failure_cell(o)} | {_cost_cell(u)} |"
            )

        lines.append("")
        notes = _prompt_notes(task_keys)
        if notes:
            lines.extend([*notes, ""])

    lines.extend(_limits_section(keys))
    lines.extend(_recommendations_section())

    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _write_decision_scaffold(
    *,
    run_id: str,
    models: Sequence[str],
    usage: Sequence[Any],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    decision_path: Path,
) -> None:
    """Write an evidence scaffold only when no decision record exists yet."""

    decision_path = Path(decision_path)
    if decision_path.exists() and "## Decision" in decision_path.read_text(encoding="utf-8"):
        return

    keys = _sort_keys({_config_key(row) for row in outputs})

    lines: list[str] = [
        "# Model Decision Record",
        "",
        f"Run ID: `{run_id}`",
        "",
        "Use this file to record the task-level decision after reviewing the measured "
        "comparison. Do not select one universal model solely because it leads on a "
        "different task.",
        "",
        "## Evaluated models",
        "",
    ]

    evaluated_models = sorted(
        {model for _task, model, _prompt, _version in keys} | {str(model) for model in models}
    )
    if evaluated_models:
        for model in evaluated_models:
            lines.append(f"- {model}")
    else:
        lines.append("- None")

    lines.extend(["", "## Evaluated configurations", ""])

    if keys:
        for task, model, prompt_id, prompt_version in keys:
            label = _prompt_label(task, model, prompt_id, prompt_version)
            lines.append(f"- `{task}` — {model} — `{label}`")
    else:
        lines.append("- No configurations supplied.")

    lines.extend(
        [
            "",
            "## Task decisions",
            "",
            "For each task, complete:",
            "",
            "- selected model",
            "- prompt version",
            "- measured reason",
            "- rejected alternative(s)",
            "- condition that would reopen the decision",
            "",
        ]
    )

    decision_path.parent.mkdir(parents=True, exist_ok=True)
    decision_path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_reports(
    *,
    run_id: str,
    models: Sequence[str],
    usage: Sequence[UsageRecord] | Sequence[CallRecord],
    outputs: Sequence[OutputRecord],
    scores: Sequence[ScoreRecord],
    report_path: Path,
    decision_path: Path,
) -> None:
    """Generate the comparison report and decision scaffold for one run.

    Only records whose ``run_id`` matches the requested run are included.
    """

    run_usage = _for_run(usage, run_id)
    run_outputs = _for_run(outputs, run_id)
    run_scores = _for_run(scores, run_id)
    if run_usage and isinstance(run_usage[0], CallRecord):
        run_usage = latest_calls_for_outputs(
            cast(list[CallRecord], run_usage),
            cast(list[OutputRecord], run_outputs),
        )

    _write_report(
        run_id=run_id,
        usage=run_usage,
        outputs=cast(list[OutputRecord], run_outputs),
        scores=cast(list[ScoreRecord], run_scores),
        report_path=Path(report_path),
    )

    _write_decision_scaffold(
        run_id=run_id,
        models=models,
        usage=run_usage,
        outputs=cast(list[OutputRecord], run_outputs),
        scores=cast(list[ScoreRecord], run_scores),
        decision_path=Path(decision_path),
    )


def load_call_records(path: Path) -> list[CallRecord]:
    if not path.exists():
        return []
    records: list[CallRecord] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.strip():
            records.append(CallRecord.model_validate(json.loads(line)))
    return records


def write_day5_comparison(
    *,
    run_id: str = "day5",
    outputs_path: Path | None = None,
    scores_path: Path | None = None,
    calls_path: Path | None = None,
    report_path: Path | None = None,
) -> Path:
    """Write ``reports/comparison.md`` from the Day 5 evidence files."""

    resolved_report = report_path or (PROJECT_ROOT / "reports" / "comparison.md")
    outputs = load_records(outputs_path or PROJECT_ROOT / "docs" / "day5-run.jsonl", OutputRecord)
    scores = load_records(
        scores_path or PROJECT_ROOT / "docs" / "day5-scores.jsonl", ScoreRecord
    )
    calls = load_call_records(calls_path or Path("runs") / f"{run_id}.jsonl")
    run_outputs = cast(list[OutputRecord], _for_run(outputs, run_id))
    run_scores = cast(list[ScoreRecord], _for_run(scores, run_id))
    latest = latest_calls_for_outputs(calls, run_outputs)
    _write_report(
        run_id=run_id,
        usage=latest,
        outputs=run_outputs,
        scores=run_scores,
        report_path=resolved_report,
    )
    return resolved_report


def main() -> None:
    path = write_day5_comparison()
    print(f"wrote {path}")


if __name__ == "__main__":
    main()

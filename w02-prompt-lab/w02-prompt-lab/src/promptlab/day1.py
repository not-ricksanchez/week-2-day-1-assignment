"""Day 1 local Mistral instrumentation script."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

import httpx

from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import CallRecord, append_record, compute_cost

DAY1_CASE_IDS = ("E12", "E07", "E11")
CASES_PATH = PROJECT_ROOT / "cases" / "extraction.jsonl"
PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 256
TRUNCATION_NUM_PREDICT = 8


@dataclass(frozen=True)
class ExtractionCase:
    case_id: str
    task: str
    source: str


@dataclass(frozen=True)
class ModelCallResult:
    response_text: str | None
    input_tokens: int
    output_tokens: int
    stop_reason: str | None
    latency_ms: int


def load_extraction_cases(path: Path, case_ids: tuple[str, ...]) -> list[ExtractionCase]:
    by_id: dict[str, ExtractionCase] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        case = ExtractionCase(case_id=raw["id"], task=raw["task"], source=raw["source"])
        by_id[case.case_id] = case

    missing = [case_id for case_id in case_ids if case_id not in by_id]
    if missing:
        raise KeyError(f"Missing extraction cases: {', '.join(missing)}")
    return [by_id[case_id] for case_id in case_ids]


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def render_prompt(template: str, document_text: str) -> str:
    return template.replace("{document_text}", document_text)


def _as_optional_str(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    return str(value)


def _as_int(value: object, field: str) -> int:
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int):
        raise TypeError(f"{field} must be an int, got {type(value).__name__}")
    return value


def map_ollama_usage(payload: dict[str, Any], latency_ms: int) -> ModelCallResult:
    return ModelCallResult(
        response_text=_as_optional_str(payload.get("response")),
        input_tokens=_as_int(payload.get("prompt_eval_count"), "prompt_eval_count"),
        output_tokens=_as_int(payload.get("eval_count"), "eval_count"),
        stop_reason=_as_optional_str(payload.get("done_reason")),
        latency_ms=latency_ms,
    )


def build_record(
    *,
    run_id: str,
    model_id: str,
    case: ExtractionCase,
    result: ModelCallResult,
    temperature: float,
    max_output_tokens: int,
    attempt: int = 1,
    error_type: str | None = None,
) -> CallRecord:
    return CallRecord(
        record_id=str(uuid4()),
        run_id=run_id,
        timestamp=datetime.now(UTC),
        provider="ollama",
        model_id=model_id,
        task="extraction",
        case_id=case.case_id,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        attempt=attempt,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_input_tokens=None,
        latency_ms=result.latency_ms,
        cost_usd=compute_cost(model_id, result.input_tokens, result.output_tokens),
        stop_reason=result.stop_reason,
        error_type=error_type,
        response_text=result.response_text,
    )


def demonstrate_truncation(
    settings: Settings,
    template: str,
    case: ExtractionCase,
    *,
    model_id: str,
    temperature: float,
    num_predict: int,
    demo_run_id: str,
) -> CallRecord | None:
    prompt = render_prompt(template, case.source)
    result = call_mistral(
        settings,
        prompt,
        temperature=temperature,
        num_predict=num_predict,
    )
    print(
        f"truncation demo {case.case_id}\t"
        f"num_predict={num_predict}\t"
        f"stop_reason={result.stop_reason}\t"
        f"output_tokens={result.output_tokens}"
    )
    print(f"{result.response_text}\n")
    if result.stop_reason != "length":
        print(
            "truncation demo did not hit the output ceiling; "
            f"got done_reason={result.stop_reason!r}"
        )
        return None
    record = build_record(
        run_id=demo_run_id,
        model_id=model_id,
        case=case,
        result=result,
        temperature=temperature,
        max_output_tokens=num_predict,
        attempt=2,
        error_type="TruncatedResponseError",
    )
    append_record(record, demo_run_id)
    return record


def call_mistral(
    settings: Settings,
    prompt: str,
    *,
    temperature: float,
    num_predict: int,
) -> ModelCallResult:
    model = settings.models["mistral"]
    started = time.perf_counter()
    response = httpx.post(
        f"{settings.ollama_base_url}/api/generate",
        json={
            "model": model.model_id,
            "prompt": prompt,
            "stream": False,
            "options": {
                "temperature": temperature,
                "num_predict": num_predict,
            },
        },
        timeout=180.0,
    )
    latency_ms = round((time.perf_counter() - started) * 1000)
    response.raise_for_status()
    payload = response.json()
    if not isinstance(payload, dict):
        raise TypeError("Ollama generate response must be a JSON object")
    return map_ollama_usage(payload, latency_ms)


def main() -> None:
    settings = Settings.from_env()
    model_id = settings.models["mistral"].model_id
    run_id = str(uuid4())
    template = load_prompt(PROMPT_PATH)
    cases = load_extraction_cases(CASES_PATH, DAY1_CASE_IDS)
    num_predict = MAX_OUTPUT_TOKENS
    for case in cases:
        prompt = render_prompt(template, case.source)
        result = call_mistral(
            settings,
            prompt,
            temperature=TEMPERATURE,
            num_predict=num_predict,
        )
        record = build_record(
            run_id=run_id,
            model_id=model_id,
            case=case,
            result=result,
            temperature=TEMPERATURE,
            max_output_tokens=num_predict,
        )
        append_record(record, run_id)
        print(
            f"{case.case_id}\t"
            f"record_id={record.record_id}\t"
            f"input_tokens={result.input_tokens}\t"
            f"output_tokens={result.output_tokens}\t"
            f"stop_reason={result.stop_reason}\t"
            f"latency_ms={result.latency_ms}"
        )
        print(f"{result.response_text}\n")
    print(f"wrote {len(cases)} records to runs/{run_id}.jsonl")

    e11 = next(case for case in cases if case.case_id == "E11")
    num_predict = TRUNCATION_NUM_PREDICT
    truncation_run_id = f"{run_id}-truncation"
    truncation_record = demonstrate_truncation(
        settings,
        template,
        e11,
        model_id=model_id,
        temperature=TEMPERATURE,
        num_predict=num_predict,
        demo_run_id=truncation_run_id,
    )
    num_predict = MAX_OUTPUT_TOKENS
    if truncation_record is not None:
        print(
            "recorded TruncatedResponseError "
            f"(record_id={truncation_record.record_id}) "
            f"in runs/{truncation_run_id}.jsonl; "
            "excluded from the three-record evidence file"
        )
    print(f"restored num_predict to {num_predict}")


if __name__ == "__main__":
    main()

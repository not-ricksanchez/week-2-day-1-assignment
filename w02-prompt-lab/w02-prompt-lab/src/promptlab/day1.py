"""Day 1 local Mistral instrumentation script."""

from __future__ import annotations

import json
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import httpx

from promptlab.config import PROJECT_ROOT, Settings

DAY1_CASE_IDS = ("E12", "E07", "E11")
CASES_PATH = PROJECT_ROOT / "cases" / "extraction.jsonl"
PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 256


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
    template = load_prompt(PROMPT_PATH)
    cases = load_extraction_cases(CASES_PATH, DAY1_CASE_IDS)
    for case in cases:
        prompt = render_prompt(template, case.source)
        result = call_mistral(
            settings,
            prompt,
            temperature=TEMPERATURE,
            num_predict=MAX_OUTPUT_TOKENS,
        )
        print(
            f"{case.case_id}\t"
            f"input_tokens={result.input_tokens}\t"
            f"output_tokens={result.output_tokens}\t"
            f"stop_reason={result.stop_reason}\t"
            f"latency_ms={result.latency_ms}"
        )
        print(f"{result.response_text}\n")


if __name__ == "__main__":
    main()

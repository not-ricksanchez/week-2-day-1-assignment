"""Day 2 local model comparison: same request, two configured Ollama models."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from promptlab.adapters.base import CompletionRequest
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.usage import append_record

CASES_PATH = PROJECT_ROOT / "cases" / "summarization.jsonl"
PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "baseline.v0.md"
PROMPT_ID = "baseline"
PROMPT_VERSION = "v0"
MAX_OUTPUT_TOKENS = 256
DOCUMENT_PLACEHOLDER = "{document_text}"


@dataclass(frozen=True)
class SummarizationCase:
    case_id: str
    source: str


def load_summarization_cases(path: Path) -> list[SummarizationCase]:
    cases: list[SummarizationCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(SummarizationCase(case_id=raw["id"], source=raw["source"]))
    return cases


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_prompt(template: str, document_text: str) -> tuple[str, str]:
    prefix, separator, _suffix = template.partition(DOCUMENT_PLACEHOLDER)
    if not separator:
        raise ValueError("prompt template is missing {document_text}")
    system = prefix.replace("<document>", "").strip()
    user_content = f"<document>\n{document_text}\n</document>"
    return system, user_content


def build_request(
    case: SummarizationCase,
    template: str,
    *,
    temperature: float,
    max_output_tokens: int,
) -> CompletionRequest:
    system, user_content = split_prompt(template, case.source)
    return CompletionRequest(
        task="summarization",
        case_id=case.case_id,
        prompt_id=PROMPT_ID,
        prompt_version=PROMPT_VERSION,
        system=system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid4())
    template = load_prompt(PROMPT_PATH)
    cases = load_summarization_cases(CASES_PATH)
    adapters = [OllamaAdapter(model_id=config.model_id) for config in settings.models.values()]

    total_records = 0
    for case in cases:
        request = build_request(
            case,
            template,
            temperature=settings.temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        for adapter in adapters:
            result = adapter.complete(request, run_id)
            for record in result.records:
                append_record(record, run_id)
                total_records += 1
            last = result.records[-1] if result.records else None
            latency_ms = last.latency_ms if last is not None else 0
            input_tokens = last.input_tokens if last is not None else 0
            output_tokens = last.output_tokens if last is not None else 0
            print(
                f"{case.case_id}\t"
                f"model_id={adapter.model_id}\t"
                f"succeeded={result.succeeded}\t"
                f"input_tokens={input_tokens}\t"
                f"output_tokens={output_tokens}\t"
                f"latency_ms={latency_ms}\t"
                f"error_type={result.error_type}"
            )
            if result.text:
                print(f"{result.text}\n")

    print(f"wrote {total_records} records to runs/{run_id}.jsonl")


if __name__ == "__main__":
    main()

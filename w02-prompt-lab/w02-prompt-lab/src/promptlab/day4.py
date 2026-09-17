"""Day 4 prompt-version comparison: one model, one run_id, two triage prompts."""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.prompts import PromptTemplate, load, render_user
from promptlab.records import OutputRecord, append_record
from promptlab.schemas import (
    TriageOutput,
    TriageOutputWithAnalysis,
    schema_description,
)
from promptlab.scoring import SCORE_PATH, score_records
from promptlab.structured import StructuredCompletionError, complete_structured
from promptlab.usage import append_record as append_call_record

CASES_PATH = PROJECT_ROOT / "cases" / "triage.jsonl"
RUN_PATH = PROJECT_ROOT / "docs" / "day4-run.jsonl"
MODEL_NAME = "qwen"
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 512
PROMPT_ID = "triage"

TriageSchema = type[TriageOutput] | type[TriageOutputWithAnalysis]

PROMPT_SPECS: tuple[tuple[str, TriageSchema], ...] = (
    ("v1", TriageOutput),
    ("v2", TriageOutputWithAnalysis),
)


@dataclass(frozen=True)
class LabCase:
    case_id: str
    source: str


@dataclass
class CaseOutcome:
    record: OutputRecord
    parsed: TriageOutput | TriageOutputWithAnalysis | None


class RecordingAdapter:
    """Wrap an adapter so the experiment can count primary vs repair calls."""

    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider: str = inner.provider
        self.model_id = inner.model_id
        self.results: list[CompletionResult] = []

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        result = self._inner.complete(request, run_id)
        self.results.append(result)
        return result

    def reset(self) -> None:
        self.results = []


def load_cases(path: Path) -> list[LabCase]:
    cases: list[LabCase] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        cases.append(LabCase(case_id=raw["id"], source=raw["source"]))
    return cases


def build_request(
    case: LabCase,
    template: PromptTemplate,
    schema: TriageSchema,
    *,
    temperature: float,
    max_output_tokens: int,
) -> CompletionRequest:
    user_content = render_user(
        template,
        variables={"schema_description": schema_description(schema)},
        untrusted=case.source,
    )
    return CompletionRequest(
        task="triage",
        case_id=case.case_id,
        prompt_id=template.prompt_id,
        prompt_version=template.version,
        system=template.system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def run_prompt_version(
    *,
    adapter: RecordingAdapter,
    cases: list[LabCase],
    template: PromptTemplate,
    schema: TriageSchema,
    run_id: str,
    model_name: str,
    temperature: float,
    max_repairs: int,
    out_path: Path,
) -> list[CaseOutcome]:
    outcomes: list[CaseOutcome] = []
    for case in cases:
        adapter.reset()
        request = build_request(
            case,
            template,
            schema,
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        parsed: TriageOutput | TriageOutputWithAnalysis | None = None
        error: str | None = None
        try:
            parsed = complete_structured(
                adapter,
                request,
                schema,
                run_id,
                max_repairs=max_repairs,
            )
        except StructuredCompletionError as exc:
            error = str(exc)

        for result in adapter.results:
            for record in result.records:
                append_call_record(record, run_id)

        repairs = max(0, len(adapter.results) - 1)
        output_record = OutputRecord(
            run_id=run_id,
            task="triage",
            case_id=case.case_id,
            model_name=model_name,
            model_id=adapter.model_id,
            prompt_version=template.version,
            succeeded=parsed is not None,
            repairs=repairs,
            output=None if parsed is None else parsed.model_dump(mode="json"),
            error=error,
        )
        append_record(out_path, output_record)
        outcomes.append(CaseOutcome(record=output_record, parsed=parsed))
        print(
            f"{case.case_id}\t"
            f"prompt={template.prompt_id}.{template.version}\t"
            f"succeeded={output_record.succeeded}\t"
            f"repairs={repairs}\t"
            f"error={error}"
        )
    return outcomes


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid4())
    model = settings.models[MODEL_NAME]
    adapter = RecordingAdapter(OllamaAdapter(model_id=model.model_id))
    cases = load_cases(CASES_PATH)
    max_repairs = settings.max_schema_repairs

    if RUN_PATH.exists():
        RUN_PATH.unlink()
    if SCORE_PATH.exists():
        SCORE_PATH.unlink()

    outcomes: list[CaseOutcome] = []
    for version, schema in PROMPT_SPECS:
        template = load(PROMPT_ID, version)
        outcomes.extend(
            run_prompt_version(
                adapter=adapter,
                cases=cases,
                template=template,
                schema=schema,
                run_id=run_id,
                model_name=model.logical_name,
                temperature=TEMPERATURE,
                max_repairs=max_repairs,
                out_path=RUN_PATH,
            )
        )

    scores = score_records([outcome.record for outcome in outcomes])
    for score in scores:
        append_record(SCORE_PATH, score)

    print()
    print(f"run_id={run_id}")
    print(f"model_id={adapter.model_id}")
    print(f"temperature={TEMPERATURE}")
    print(f"max_output_tokens={MAX_OUTPUT_TOKENS}")
    print(f"cases={len(cases)}")
    print(f"prompt versions={[version for version, _schema in PROMPT_SPECS]}")
    print(f"wrote output records to {RUN_PATH}")
    print(f"wrote {len(scores)} score records to {SCORE_PATH}")
    print(f"wrote call records to runs/{run_id}.jsonl")


if __name__ == "__main__":
    main()

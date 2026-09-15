"""Day 3 structured-output experiment: one model, one run_id, bounded repair."""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.records import OutputRecord, append_record
from promptlab.schemas import (
    PolicyExtraction,
    SummarizationOutput,
    TaskName,
    schema_description,
)
from promptlab.structured import StructuredCompletionError, complete_structured
from promptlab.usage import append_record as append_call_record

SUMMARIZE_PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "summarize.v1.md"
EXTRACT_PROMPT_PATH = PROJECT_ROOT / "src" / "prompts" / "extract.v2.md"
SUMMARIZATION_CASES_PATH = PROJECT_ROOT / "cases" / "summarization.jsonl"
EXTRACTION_CASES_PATH = PROJECT_ROOT / "cases" / "extraction.jsonl"
RUN_PATH = PROJECT_ROOT / "docs" / "day3-run.jsonl"
TEMPERATURE = 0.0
MAX_OUTPUT_TOKENS = 512

# Distinctive strings from the two extract.v2.md example documents only.
# None of these appear in cases/extraction.jsonl.
LEAKAGE_MARKERS: tuple[str, ...] = (
    "Northglass",
    "Norwyn",
    "Bellwater",
    "Larkspur",
    "Meadowcross",
    "Article A - Scope",
    "Article B - Required evidence",
    "Article C - Jurisdiction",
    "Build Note R1",
    "Build Note R2",
    "Build Note R3",
    "Release 14.2",
)

_NUMBERED_HEADING = re.compile(r"^(\d+\.\s+)(.+)$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.+)$")


@dataclass(frozen=True)
class LabCase:
    case_id: str
    source: str


@dataclass
class CaseOutcome:
    record: OutputRecord
    source: str
    parsed: SummarizationOutput | PolicyExtraction | None


class RecordingAdapter:
    """Wrap an adapter so the experiment can count primary vs repair calls."""

    def __init__(self, inner: OllamaAdapter) -> None:
        self._inner = inner
        self.provider = inner.provider
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


def load_prompt(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def split_prompt(
    template: str,
    document_text: str,
    schema: type[BaseModel],
) -> tuple[str, str]:
    filled = template.replace("{schema_description}", schema_description(schema))
    start = filled.find("<document>")
    end = filled.find("</document>")
    if start == -1 or end == -1 or "{document_text}" not in filled:
        raise ValueError("prompt template is missing document delimiters or {document_text}")
    suffix_start = end + len("</document>")
    system = (filled[:start] + filled[suffix_start:]).replace("{document_text}", "").strip()
    user_content = f"<document>\n{document_text}\n</document>"
    return system, user_content


def build_request(
    case: LabCase,
    template: str,
    *,
    task: TaskName,
    prompt_id: str,
    prompt_version: str,
    schema: type[BaseModel],
    temperature: float,
    max_output_tokens: int,
) -> CompletionRequest:
    system, user_content = split_prompt(template, case.source, schema)
    return CompletionRequest(
        task=task,
        case_id=case.case_id,
        prompt_id=prompt_id,
        prompt_version=prompt_version,
        system=system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=max_output_tokens,
    )


def section_headings(source: str) -> set[str]:
    headings: set[str] = set()
    for raw in source.splitlines():
        line = raw.strip()
        markdown = _MARKDOWN_HEADING.match(line)
        if markdown:
            headings.add(markdown.group(1).strip())
            continue
        numbered = _NUMBERED_HEADING.match(line)
        if numbered and len(line) <= 80:
            headings.add(line)
            headings.add(numbered.group(2).strip())
    return headings


def citation_exists(citation: str | None, source: str) -> bool:
    if citation is None or not citation.strip():
        return False
    return citation.strip() in section_headings(source)


def citation_failure_count(
    parsed: SummarizationOutput | PolicyExtraction,
    source: str,
) -> tuple[int, int]:
    failures = 0
    present = 0
    for field in parsed.evidence_fields().values():
        if field.status != "present":
            continue
        present += 1
        if not citation_exists(field.citation, source):
            failures += 1
    return failures, present


def leakage_hits(output: dict[str, Any]) -> list[str]:
    blob = json.dumps(output)
    return [marker for marker in LEAKAGE_MARKERS if marker in blob]


def run_task(
    *,
    adapter: RecordingAdapter,
    cases: list[LabCase],
    template: str,
    task: TaskName,
    prompt_id: str,
    prompt_version: str,
    schema: type[SummarizationOutput] | type[PolicyExtraction],
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
            task=task,
            prompt_id=prompt_id,
            prompt_version=prompt_version,
            schema=schema,
            temperature=temperature,
            max_output_tokens=MAX_OUTPUT_TOKENS,
        )
        parsed: SummarizationOutput | PolicyExtraction | None = None
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
            task=task,
            case_id=case.case_id,
            model_name=model_name,
            model_id=adapter.model_id,
            prompt_version=prompt_version,
            succeeded=parsed is not None,
            repairs=repairs,
            output=None if parsed is None else parsed.model_dump(mode="json"),
            error=error,
        )
        append_record(out_path, output_record)
        outcomes.append(CaseOutcome(record=output_record, source=case.source, parsed=parsed))
        print(
            f"{case.case_id}\t"
            f"task={task}\t"
            f"succeeded={output_record.succeeded}\t"
            f"repairs={repairs}\t"
            f"error={error}"
        )
    return outcomes


def repair_rate(outcomes: list[CaseOutcome]) -> tuple[int, int]:
    repaired = sum(1 for outcome in outcomes if outcome.record.repairs > 0)
    return repaired, len(outcomes)


def main() -> None:
    settings = Settings.from_env()
    run_id = str(uuid4())
    model = settings.models["qwen"]
    adapter = RecordingAdapter(OllamaAdapter(model_id=model.model_id))
    max_repairs = settings.max_schema_repairs

    if RUN_PATH.exists():
        RUN_PATH.unlink()

    summarization = run_task(
        adapter=adapter,
        cases=load_cases(SUMMARIZATION_CASES_PATH),
        template=load_prompt(SUMMARIZE_PROMPT_PATH),
        task="summarization",
        prompt_id="summarize",
        prompt_version="v1",
        schema=SummarizationOutput,
        run_id=run_id,
        model_name=model.logical_name,
        temperature=TEMPERATURE,
        max_repairs=max_repairs,
        out_path=RUN_PATH,
    )
    extraction = run_task(
        adapter=adapter,
        cases=load_cases(EXTRACTION_CASES_PATH),
        template=load_prompt(EXTRACT_PROMPT_PATH),
        task="extraction",
        prompt_id="extract",
        prompt_version="v2",
        schema=PolicyExtraction,
        run_id=run_id,
        model_name=model.logical_name,
        temperature=TEMPERATURE,
        max_repairs=max_repairs,
        out_path=RUN_PATH,
    )

    sum_repaired, sum_total = repair_rate(summarization)
    ext_repaired, ext_total = repair_rate(extraction)

    leaked_cases = 0
    leaked_markers = 0
    for outcome in extraction:
        if outcome.record.output is None:
            continue
        hits = leakage_hits(outcome.record.output)
        if hits:
            leaked_cases += 1
            leaked_markers += len(hits)
            print(f"{outcome.record.case_id}\tleakage={hits}")

    citation_fail_count = 0
    present_fields = 0
    for outcome in [*summarization, *extraction]:
        if outcome.parsed is None:
            continue
        failures, present = citation_failure_count(outcome.parsed, outcome.source)
        citation_fail_count += failures
        present_fields += present

    print()
    print(f"run_id={run_id}")
    print(f"model_id={adapter.model_id}")
    print(f"temperature={TEMPERATURE}")
    print(f"summarization repair rate={sum_repaired}/{sum_total}")
    print(f"extraction repair rate={ext_repaired}/{ext_total}")
    print(f"example leakage count={leaked_cases} cases ({leaked_markers} marker hits)")
    print(
        f"citation-existence failure count={citation_fail_count}"
        f" / {present_fields} present fields"
    )
    print(f"wrote {sum_total + ext_total} output records to {RUN_PATH}")
    print(f"wrote call records to runs/{run_id}.jsonl")


if __name__ == "__main__":
    main()

"""Day 5 evaluation runner: three tasks, two configured models, twelve cases each."""

from __future__ import annotations

import argparse
import re
from typing import cast

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.adapters.ollama import OllamaAdapter
from promptlab.config import PROJECT_ROOT, Settings
from promptlab.corpus import load_cases, validate_corpus
from promptlab.errors import UnknownModelError
from promptlab.prompts import PromptTemplate, load, prompt_for, render_user
from promptlab.records import OutputRecord, append_record
from promptlab.schemas import (
    OUTPUT_SCHEMAS,
    StrictModel,
    TaskName,
    TriageOutputWithAnalysis,
    schema_description,
)
from promptlab.scoring import score_records
from promptlab.structured import StructuredCompletionError, complete_structured
from promptlab.usage import append_record as append_call_record

RUN_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,79}$")
RUN_PATH = PROJECT_ROOT / "docs" / "day5-run.jsonl"
SCORE_PATH = PROJECT_ROOT / "docs" / "day5-scores.jsonl"
MAX_OUTPUT_TOKENS = 1024


class RecordingAdapter:
    """Wrap OllamaAdapter so primary, retry, and repair CallRecords are kept."""

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


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the local two-model prompt comparison")
    parser.add_argument("--run-id", help="Stable identifier for this run")
    parser.add_argument("--task", choices=["triage", "summarization", "extraction"])
    parser.add_argument(
        "--model",
        help="Logical model name from configuration (omit to run both configured models)",
    )
    parser.add_argument("--limit", type=int, help="Limit cases per task for a smoke run")
    parser.add_argument(
        "--validate-only",
        action="store_true",
        help="Validate configuration and corpus without calling Ollama",
    )
    return parser


def _output_schema(task: TaskName, prompt_version: str) -> type[StrictModel]:
    if task == "triage" and prompt_version == "v2":
        return TriageOutputWithAnalysis
    return OUTPUT_SCHEMAS[task]


def _build_request(
    *,
    task: TaskName,
    case_id: str,
    document_text: str,
    template: PromptTemplate,
    schema: type[StrictModel],
    temperature: float,
) -> CompletionRequest:
    schema_text = schema_description(schema)
    system = template.system.replace("{schema_description}", schema_text)
    user_content = render_user(
        template,
        variables={"schema_description": schema_text},
        untrusted=document_text,
    )
    return CompletionRequest(
        task=task,
        case_id=case_id,
        prompt_id=template.prompt_id,
        prompt_version=template.version,
        system=system,
        user_content=user_content,
        temperature=temperature,
        max_output_tokens=MAX_OUTPUT_TOKENS,
    )


def main() -> None:
    args = _parser().parse_args()
    counts = validate_corpus()
    if args.validate_only:
        print("Corpus valid: " + ", ".join(f"{task}={count}" for task, count in counts.items()))
        return

    run_id = cast(str | None, args.run_id)
    if run_id is None or not RUN_ID_PATTERN.fullmatch(run_id):
        raise SystemExit("--run-id is required and must use letters, numbers, '.', '_' or '-'")
    limit = cast(int | None, args.limit)
    if limit is not None and limit < 1:
        raise SystemExit("--limit must be at least 1")

    selected_tasks: list[TaskName]
    if args.task:
        selected_tasks = [cast(TaskName, args.task)]
    else:
        selected_tasks = ["triage", "summarization", "extraction"]

    settings = Settings.from_env()
    if args.model:
        try:
            selected_models = [settings.resolve_model(args.model)]
        except UnknownModelError:
            known = ", ".join(settings.models)
            raise SystemExit(
                f"unknown model {args.model!r}; configured names: {known}"
            ) from None
    else:
        selected_models = [settings.resolve_model(name) for name in settings.models]

    adapters = {
        model.logical_name: RecordingAdapter(OllamaAdapter(model_id=model.model_id))
        for model in selected_models
    }

    if RUN_PATH.exists():
        RUN_PATH.unlink()
    if SCORE_PATH.exists():
        SCORE_PATH.unlink()

    outputs: list[OutputRecord] = []
    for task in selected_tasks:
        pairs = load_cases(task)
        if limit is not None:
            pairs = pairs[:limit]
        for model in selected_models:
            spec = prompt_for(task, model.logical_name)
            template = load(spec.prompt_id, spec.version)
            schema = _output_schema(task, spec.version)
            adapter = adapters[model.logical_name]
            for case, _gold in pairs:
                adapter.reset()
                request = _build_request(
                    task=task,
                    case_id=case.id,
                    document_text=case.document_text,
                    template=template,
                    schema=schema,
                    temperature=settings.temperature,
                )
                parsed: StrictModel | None = None
                error: str | None = None
                try:
                    parsed = complete_structured(
                        adapter,
                        request,
                        schema,
                        run_id,
                        max_repairs=settings.max_schema_repairs,
                    )
                except StructuredCompletionError as exc:
                    error = str(exc)

                for result in adapter.results:
                    for call in result.records:
                        append_call_record(call, run_id)

                output_record = OutputRecord(
                    run_id=run_id,
                    task=task,
                    case_id=case.id,
                    model_name=model.logical_name,
                    model_id=model.model_id,
                    prompt_version=template.version,
                    succeeded=parsed is not None,
                    repairs=max(0, len(adapter.results) - 1),
                    output=None if parsed is None else parsed.model_dump(mode="json"),
                    error=error,
                )
                append_record(RUN_PATH, output_record)
                outputs.append(output_record)
                print(
                    f"{task:13} {model.logical_name:8} {case.id:5} "
                    f"{spec.label(model.logical_name):24} "
                    f"{'ok' if output_record.succeeded else 'failed'}"
                )

    scores = score_records(outputs)
    for score in scores:
        append_record(SCORE_PATH, score)

    print(f"run_id={run_id}")
    print(f"wrote {len(outputs)} output records to {RUN_PATH}")
    print(f"wrote {len(scores)} score records to {SCORE_PATH}")
    print(f"wrote call records to runs/{run_id}.jsonl")


if __name__ == "__main__":
    main()

"""Deterministic scoring for Day 4 triage and remaining Day 5 metrics.

Uses the existing ``ScoreRecord`` contract. Does not call a model.
"""

from __future__ import annotations

import json
import re
from collections import defaultdict
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from promptlab.config import PII_PATTERNS, PROJECT_ROOT
from promptlab.records import OutputRecord, ScoreRecord, append_record, load_records
from promptlab.rules import VersionCandidate, select_current_version
from promptlab.schemas import PolicyExtraction, SummarizationOutput

SCORER_VERSION = "day5.v1"
GOLD_PATH = PROJECT_ROOT / "cases" / "gold" / "triage.jsonl"
GOLD_DIR = PROJECT_ROOT / "cases" / "gold"
CASES_DIR = PROJECT_ROOT / "cases"
RUN_PATH = PROJECT_ROOT / "docs" / "day4-run.jsonl"
SCORE_PATH = PROJECT_ROOT / "docs" / "day4-scores.jsonl"

# config.py does not ship boundary-language patterns. These cover the
# final-outcome language the Day 4 contract forbids in draft replies.
BOUNDARY_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(r"\bapprov(?:e|ed|al|ing)\b", re.IGNORECASE),
    re.compile(r"\bden(?:y|ies|ied|ial)\b", re.IGNORECASE),
    re.compile(r"\brefund(?:s|ed|ing)?\b", re.IGNORECASE),
    re.compile(r"\breimburs(?:e|ed|ement|ing)\b", re.IGNORECASE),
    re.compile(r"\bgranted\b", re.IGNORECASE),
    re.compile(r"\bresolved\b", re.IGNORECASE),
    re.compile(r"\bfinal (?:decision|outcome|resolution)\b", re.IGNORECASE),
)

_NUMBERED_HEADING = re.compile(r"^(\d+\.\s+)(.+)$")
_MARKDOWN_HEADING = re.compile(r"^#{1,6}\s+(.+)$")
_TASKS = ("triage", "summarization", "extraction")


@dataclass(frozen=True)
class GoldLabel:
    case_id: str
    expected_queue: str | None = None
    expected_escalation: bool | None = None
    expected_status: str | None = None
    recoverable_fields: tuple[str, ...] = ()
    version_group: str | None = None
    expected_current_case_id: str | None = None
    as_of: str | None = None


def load_gold_labels(path: Path | None = None) -> dict[str, GoldLabel]:
    """Load gold labels. A specific path loads one file; otherwise all task files."""
    if path is not None:
        return _load_gold_file(path)

    labels: dict[str, GoldLabel] = {}
    for task in _TASKS:
        labels.update(_load_gold_file(GOLD_DIR / f"{task}.jsonl"))
    return labels


def load_sources() -> dict[str, str]:
    """Load case source documents so citation checks can verify section headings."""
    sources: dict[str, str] = {}
    for task in _TASKS:
        path = CASES_DIR / f"{task}.jsonl"
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.strip():
                continue
            raw = json.loads(line)
            sources[raw["id"]] = raw["source"]
    return sources


def _load_gold_file(path: Path) -> dict[str, GoldLabel]:
    labels: dict[str, GoldLabel] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        recoverable = raw.get("recoverable_fields") or []
        labels[raw["id"]] = GoldLabel(
            case_id=raw["id"],
            expected_queue=raw.get("expected_queue"),
            expected_escalation=(
                None if "expected_escalation" not in raw else bool(raw["expected_escalation"])
            ),
            expected_status=raw.get("expected_status"),
            recoverable_fields=tuple(str(field) for field in recoverable),
            version_group=raw.get("version_group"),
            expected_current_case_id=raw.get("expected_current_case_id"),
            as_of=raw.get("as_of"),
        )
    return labels


def _boundary_hits(text: str) -> list[str]:
    hits: list[str] = []
    for pattern in BOUNDARY_PATTERNS:
        match = pattern.search(text)
        if match:
            hits.append(match.group(0))
    return hits


def _human_boundary_pass(output: dict[str, Any]) -> tuple[bool, str | None]:
    if output.get("customer_outcome") is not None:
        return False, "customer_outcome is not null"
    draft_reply = str(output.get("draft_reply") or "")
    hits = _boundary_hits(draft_reply)
    if hits:
        return False, "draft_reply outcome language: " + ", ".join(hits)
    return True, None


def section_headings(source: str) -> set[str]:
    """Return headings a citation may legally name."""
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
    """True only when the citation names a heading that appears in the source."""
    if citation is None or not citation.strip():
        return False
    return citation.strip() in section_headings(source)


def _iter_strings(value: Any) -> Iterator[str]:
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for nested in value.values():
            yield from _iter_strings(nested)
    elif isinstance(value, list):
        for nested in value:
            yield from _iter_strings(nested)


def pii_hits(output: dict[str, Any]) -> list[str]:
    """Return synthetic PII strings found in free-text model output."""
    hits: list[str] = []
    seen: set[str] = set()
    for text in _iter_strings(output):
        for pattern in PII_PATTERNS:
            for match in pattern.findall(text):
                if match not in seen:
                    seen.add(match)
                    hits.append(match)
    return hits


def _score(
    *,
    record: OutputRecord,
    metric: str,
    numerator: int,
    denominator: int,
    lower_is_better: bool = False,
    detail: str | None = None,
) -> ScoreRecord:
    return ScoreRecord(
        run_id=record.run_id,
        task=record.task,
        case_id=record.case_id,
        model_name=record.model_name,
        prompt_version=record.prompt_version,
        scorer_version=SCORER_VERSION,
        metric=metric,
        numerator=numerator,
        denominator=denominator,
        lower_is_better=lower_is_better,
        detail=detail,
    )


def _evidence_fields(output: dict[str, Any]) -> dict[str, dict[str, Any]]:
    fields: dict[str, dict[str, Any]] = {}
    for name, value in output.items():
        if isinstance(value, dict) and "status" in value:
            fields[name] = value
    return fields


def _parse_evidence_output(
    record: OutputRecord,
) -> SummarizationOutput | PolicyExtraction | None:
    if record.output is None:
        return None
    schema: type[SummarizationOutput] | type[PolicyExtraction]
    if record.task == "summarization":
        schema = SummarizationOutput
    elif record.task == "extraction":
        schema = PolicyExtraction
    else:
        return None
    try:
        return schema.model_validate(record.output)
    except ValidationError:
        return None


def _required_evidence_scores(
    record: OutputRecord,
    gold: GoldLabel,
    fields: dict[str, dict[str, Any]],
) -> list[ScoreRecord]:
    recoverable = list(gold.recoverable_fields)
    present_names = [
        name for name, field in fields.items() if field.get("status") == "present"
    ]
    found = [name for name in recoverable if name in present_names]
    missed = [name for name in recoverable if name not in present_names]
    invented = [name for name in present_names if name not in set(recoverable)]
    recoverable_n = len(recoverable)
    present_n = len(present_names)

    return [
        _score(
            record=record,
            metric="required_evidence",
            numerator=len(found),
            denominator=recoverable_n,
            detail=f"required evidence found: {len(found)}/{recoverable_n}",
        ),
        _score(
            record=record,
            metric="missed_evidence",
            numerator=len(missed),
            denominator=recoverable_n,
            lower_is_better=True,
            detail=None if not missed else "missed: " + ", ".join(missed),
        ),
        _score(
            record=record,
            metric="invented_unsupported",
            numerator=len(invented),
            denominator=present_n,
            lower_is_better=True,
            detail=None if not invented else "invented/unsupported: " + ", ".join(invented),
        ),
    ]


def _citation_score(
    record: OutputRecord,
    fields: dict[str, dict[str, Any]],
    source: str,
) -> ScoreRecord:
    present = [
        (name, field)
        for name, field in fields.items()
        if field.get("status") == "present"
    ]
    correct = 0
    failed: list[str] = []
    for name, field in present:
        citation = field.get("citation")
        citation_text = citation if isinstance(citation, str) else None
        if citation_exists(citation_text, source):
            correct += 1
        else:
            failed.append(name)
    return _score(
        record=record,
        metric="citation_correct",
        numerator=correct,
        denominator=len(present),
        detail=(
            None
            if not failed
            else "citation missing or not a source heading: " + ", ".join(failed)
        ),
    )


def _pii_score(record: OutputRecord, output: dict[str, Any] | None) -> ScoreRecord:
    hits = pii_hits(output or {})
    return _score(
        record=record,
        metric="pii_leakage",
        numerator=int(bool(hits)),
        denominator=1,
        lower_is_better=True,
        detail=None if not hits else "pii: " + ", ".join(hits),
    )


def _missing_triage_scores(record: OutputRecord, gold: GoldLabel) -> list[ScoreRecord]:
    missed = 1 if gold.expected_escalation else 0
    return [
        _score(record=record, metric="queue", numerator=0, denominator=1, detail="missing output"),
        _score(
            record=record,
            metric="escalation",
            numerator=0,
            denominator=1,
            detail="missing output",
        ),
        _score(
            record=record,
            metric="missed_escalation",
            numerator=missed,
            denominator=1,
            lower_is_better=True,
            detail="missing output",
        ),
        _score(
            record=record,
            metric="unnecessary_escalation",
            numerator=0,
            denominator=1,
            lower_is_better=True,
            detail="missing output",
        ),
        _score(
            record=record,
            metric="human_boundary",
            numerator=0,
            denominator=1,
            detail="missing output",
        ),
        _pii_score(record, None),
    ]


def _score_triage(record: OutputRecord, gold: GoldLabel) -> list[ScoreRecord]:
    output = record.output
    if not record.succeeded or output is None:
        return _missing_triage_scores(record, gold)

    predicted_queue = output.get("queue")
    predicted_escalation = bool(output.get("escalation_required"))
    expected_queue = gold.expected_queue
    expected_escalation = bool(gold.expected_escalation)
    queue_correct = int(predicted_queue == expected_queue)
    escalation_correct = int(predicted_escalation == expected_escalation)
    missed = int(expected_escalation and not predicted_escalation)
    unnecessary = int(predicted_escalation and not expected_escalation)
    boundary_ok, boundary_detail = _human_boundary_pass(output)

    return [
        _score(
            record=record,
            metric="queue",
            numerator=queue_correct,
            denominator=1,
            detail=f"predicted={predicted_queue} expected={expected_queue}",
        ),
        _score(
            record=record,
            metric="escalation",
            numerator=escalation_correct,
            denominator=1,
            detail=f"predicted={predicted_escalation} expected={expected_escalation}",
        ),
        _score(
            record=record,
            metric="missed_escalation",
            numerator=missed,
            denominator=1,
            lower_is_better=True,
            detail=None if missed == 0 else "gold required escalation; model did not",
        ),
        _score(
            record=record,
            metric="unnecessary_escalation",
            numerator=unnecessary,
            denominator=1,
            lower_is_better=True,
            detail=None if unnecessary == 0 else "model escalated; gold did not require it",
        ),
        _score(
            record=record,
            metric="human_boundary",
            numerator=int(boundary_ok),
            denominator=1,
            detail=boundary_detail,
        ),
        _pii_score(record, output),
    ]


def _score_evidence_task(
    record: OutputRecord,
    gold: GoldLabel,
    source: str,
) -> list[ScoreRecord]:
    parsed = _parse_evidence_output(record)
    if not record.succeeded or record.output is None or parsed is None:
        recoverable_n = len(gold.recoverable_fields)
        return [
            _score(
                record=record,
                metric="required_evidence",
                numerator=0,
                denominator=recoverable_n,
                detail="missing output",
            ),
            _score(
                record=record,
                metric="missed_evidence",
                numerator=recoverable_n,
                denominator=recoverable_n,
                lower_is_better=True,
                detail="missing output",
            ),
            _score(
                record=record,
                metric="invented_unsupported",
                numerator=0,
                denominator=0,
                lower_is_better=True,
                detail="missing output",
            ),
            _score(
                record=record,
                metric="citation_correct",
                numerator=0,
                denominator=0,
                detail="missing output",
            ),
            _pii_score(record, record.output),
        ]

    fields = {
        name: field.model_dump(mode="json") for name, field in parsed.evidence_fields().items()
    }
    if not fields:
        fields = _evidence_fields(record.output)
    scores = _required_evidence_scores(record, gold, fields)
    scores.append(_citation_score(record, fields, source))
    scores.append(_pii_score(record, record.output))
    return scores


def _candidate_from_record(record: OutputRecord) -> VersionCandidate | None:
    """Build a version candidate from extracted evidence only.

    Missing or unparseable version/effective_date is an extraction failure,
    not a currency decision by the model.
    """
    parsed = _parse_evidence_output(record)
    if parsed is None:
        return None
    version = parsed.version
    effective = parsed.effective_date
    if (
        version.status != "present"
        or effective.status != "present"
        or not isinstance(version.value, str)
        or not isinstance(effective.value, str)
    ):
        return None
    try:
        effective_date = date.fromisoformat(effective.value)
    except ValueError:
        return None
    return VersionCandidate(
        case_id=record.case_id,
        version=version.value,
        effective_date=effective_date,
    )


def score_version_selection(
    records: list[OutputRecord],
    gold_by_id: dict[str, GoldLabel],
) -> list[ScoreRecord]:
    """Score which document is current using select_current_version.

    The model is not asked which document is current. A miss is either
    bad extraction (no usable version/date candidate) or a wrong
    deterministic rule result.
    """
    grouped: dict[tuple[str, str, str, str, str], list[OutputRecord]] = defaultdict(list)
    for record in records:
        gold = gold_by_id.get(record.case_id)
        if gold is None or gold.version_group is None:
            continue
        grouped[
            (
                record.run_id,
                record.task,
                record.model_name,
                record.prompt_version,
                gold.version_group,
            )
        ].append(record)

    scores: list[ScoreRecord] = []
    for (_run_id, _task, _model, _prompt, group_name), group_records in grouped.items():
        if len(group_records) < 2:
            continue
        golds = [gold_by_id[record.case_id] for record in group_records]
        expected = next(
            (gold.expected_current_case_id for gold in golds if gold.expected_current_case_id),
            None,
        )
        as_of_raw = next((gold.as_of for gold in golds if gold.as_of), None)
        if expected is None or as_of_raw is None:
            continue

        candidates: list[VersionCandidate] = []
        missing: list[str] = []
        for record in group_records:
            candidate = _candidate_from_record(record)
            if candidate is None:
                missing.append(record.case_id)
            else:
                candidates.append(candidate)

        selected = select_current_version(candidates, date.fromisoformat(as_of_raw))
        correct = selected is not None and selected.case_id == expected
        selected_id = selected.case_id if selected is not None else "none"
        if correct:
            detail = f"expected={expected}; selected={selected_id}"
        elif missing:
            detail = (
                f"expected={expected}; selected={selected_id}; "
                "bad extraction: missing version/effective_date for "
                + ", ".join(missing)
            )
        else:
            detail = (
                f"expected={expected}; selected={selected_id}; "
                "deterministic rule did not select expected current case"
            )

        template = group_records[0]
        scores.append(
            ScoreRecord(
                run_id=template.run_id,
                task=template.task,
                case_id=f"version:{group_name}",
                model_name=template.model_name,
                prompt_version=template.prompt_version,
                scorer_version=SCORER_VERSION,
                metric="version_selection_accuracy",
                numerator=int(correct),
                denominator=1,
                detail=detail,
            )
        )
    return scores


def score_output(
    record: OutputRecord,
    gold: GoldLabel,
    source: str | None = None,
) -> list[ScoreRecord]:
    """Score one output against gold using the Day 4 ScoreRecord contract."""
    if record.task == "triage":
        return _score_triage(record, gold)

    resolved_source = source if source is not None else load_sources().get(record.case_id, "")
    return _score_evidence_task(record, gold, resolved_source)


def score_records(
    records: list[OutputRecord],
    gold_by_id: dict[str, GoldLabel] | None = None,
    sources_by_id: dict[str, str] | None = None,
) -> list[ScoreRecord]:
    labels = gold_by_id if gold_by_id is not None else load_gold_labels()
    sources = sources_by_id if sources_by_id is not None else load_sources()
    scores: list[ScoreRecord] = []
    for record in records:
        gold = labels[record.case_id]
        scores.extend(score_output(record, gold, source=sources.get(record.case_id)))
    scores.extend(score_version_selection(records, labels))
    return scores


def main() -> None:
    outputs = load_records(RUN_PATH, OutputRecord)
    if not outputs:
        raise SystemExit(f"no output records at {RUN_PATH}")

    if SCORE_PATH.exists():
        SCORE_PATH.unlink()

    scores = score_records(outputs)
    for score in scores:
        append_record(SCORE_PATH, score)

    print(f"scored {len(outputs)} outputs")
    print(f"wrote {len(scores)} score records to {SCORE_PATH}")


if __name__ == "__main__":
    main()

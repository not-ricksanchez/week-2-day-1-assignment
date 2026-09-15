"""Deterministic Day 4 triage scoring.

Uses the existing ``ScoreRecord`` contract. Does not call a model.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from promptlab.config import PROJECT_ROOT
from promptlab.records import OutputRecord, ScoreRecord, append_record, load_records

SCORER_VERSION = "day4.v1"
GOLD_PATH = PROJECT_ROOT / "cases" / "gold" / "triage.jsonl"
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


@dataclass(frozen=True)
class GoldLabel:
    case_id: str
    expected_queue: str
    expected_escalation: bool


def load_gold_labels(path: Path = GOLD_PATH) -> dict[str, GoldLabel]:
    labels: dict[str, GoldLabel] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        raw = json.loads(line)
        labels[raw["id"]] = GoldLabel(
            case_id=raw["id"],
            expected_queue=raw["expected_queue"],
            expected_escalation=bool(raw["expected_escalation"]),
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


def score_output(record: OutputRecord, gold: GoldLabel) -> list[ScoreRecord]:
    """Score one triage output against the gold queue and escalation labels."""
    output = record.output
    if not record.succeeded or output is None:
        missed = 1 if gold.expected_escalation else 0
        return [
            _score(
                record=record,
                metric="queue",
                numerator=0,
                denominator=1,
                detail="missing output",
            ),
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
        ]

    predicted_queue = output.get("queue")
    predicted_escalation = bool(output.get("escalation_required"))
    queue_correct = int(predicted_queue == gold.expected_queue)
    escalation_correct = int(predicted_escalation == gold.expected_escalation)
    missed = int(gold.expected_escalation and not predicted_escalation)
    unnecessary = int(predicted_escalation and not gold.expected_escalation)
    boundary_ok, boundary_detail = _human_boundary_pass(output)

    return [
        _score(
            record=record,
            metric="queue",
            numerator=queue_correct,
            denominator=1,
            detail=f"predicted={predicted_queue} expected={gold.expected_queue}",
        ),
        _score(
            record=record,
            metric="escalation",
            numerator=escalation_correct,
            denominator=1,
            detail=(
                f"predicted={predicted_escalation} expected={gold.expected_escalation}"
            ),
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
    ]


def score_records(
    records: list[OutputRecord],
    gold_by_id: dict[str, GoldLabel] | None = None,
) -> list[ScoreRecord]:
    labels = gold_by_id if gold_by_id is not None else load_gold_labels()
    scores: list[ScoreRecord] = []
    for record in records:
        gold = labels[record.case_id]
        scores.extend(score_output(record, gold))
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

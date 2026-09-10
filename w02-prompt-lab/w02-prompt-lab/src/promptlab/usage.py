"""Day 1 usage-recording contract."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Literal
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator

from promptlab.config import Settings
from promptlab.errors import UnknownModelError


class CallRecord(BaseModel):
    """One model-call attempt."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(default_factory=lambda: str(uuid4()))
    run_id: str
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    provider: Literal["ollama"]
    model_id: str
    task: Literal["triage", "summarization", "extraction"]
    case_id: str
    prompt_id: str
    prompt_version: str
    attempt: int
    temperature: float
    max_output_tokens: int
    input_tokens: int
    output_tokens: int
    cached_input_tokens: int | None
    latency_ms: int
    cost_usd: float
    stop_reason: str | None
    error_type: str | None
    response_text: str | None

    @field_validator("timestamp")
    @classmethod
    def timestamp_must_be_timezone_aware_utc(cls, value: datetime) -> datetime:
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("timestamp must be timezone-aware UTC")
        return value.astimezone(UTC)


def compute_cost(model_id: str, input_tokens: int, output_tokens: int) -> float:
    """Return the configured provider charge for one model call."""
    settings = Settings.from_env()
    for config in settings.models.values():
        if config.model_id == model_id:
            return float(config.cost(input_tokens, output_tokens))
    raise UnknownModelError(model_id)


def append_record(record: CallRecord, run_id: str) -> None:
    """Append one JSON record to runs/{run_id}.jsonl without rewriting the file."""
    raise NotImplementedError

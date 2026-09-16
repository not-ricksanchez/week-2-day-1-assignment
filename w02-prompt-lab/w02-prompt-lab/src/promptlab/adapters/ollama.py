"""Ollama-backed ModelAdapter used for both configured local models."""

from __future__ import annotations

import random
import time
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

import httpx

from promptlab.adapters.base import CompletionRequest, CompletionResult
from promptlab.config import PROVIDER, Settings
from promptlab.errors import (
    PermanentProviderError,
    ProviderError,
    TransientProviderError,
    TruncatedResponseError,
    UnknownModelError,
)
from promptlab.usage import CallRecord, compute_cost

MAX_ATTEMPTS = 3
HTTP_TIMEOUT_SECONDS = 180.0
TRANSIENT_STATUS_CODES = frozenset({408, 429, 500, 502, 503, 504})


class OllamaAdapter:
    provider = PROVIDER

    def __init__(self, model_id: str) -> None:
        settings = Settings.from_env()
        known_ids = {config.model_id for config in settings.models.values()}
        if model_id not in known_ids:
            raise UnknownModelError(model_id)
        self.model_id = model_id
        self._base_url = settings.ollama_base_url

    def complete(self, request: CompletionRequest, run_id: str) -> CompletionResult:
        records: list[CallRecord] = []
        for attempt in range(1, MAX_ATTEMPTS + 1):
            started = time.perf_counter()
            try:
                payload = self._post(request)
                mapped = _map_payload(payload)
            except ProviderError as exc:
                latency_ms = round((time.perf_counter() - started) * 1000)
                records.append(
                    self._record(
                        request=request,
                        run_id=run_id,
                        attempt=attempt,
                        latency_ms=latency_ms,
                        text=None,
                        input_tokens=0,
                        output_tokens=0,
                        stop_reason=None,
                        error_type=type(exc).__name__,
                    )
                )
                if not exc.retryable or attempt >= MAX_ATTEMPTS:
                    return CompletionResult(
                        succeeded=False,
                        text=None,
                        error_type=type(exc).__name__,
                        records=records,
                    )
                _backoff(attempt)
                continue

            latency_ms = round((time.perf_counter() - started) * 1000)
            if mapped["stop_reason"] == "length":
                error_type = TruncatedResponseError.__name__
                records.append(
                    self._record(
                        request=request,
                        run_id=run_id,
                        attempt=attempt,
                        latency_ms=latency_ms,
                        text=mapped["text"],
                        input_tokens=mapped["input_tokens"],
                        output_tokens=mapped["output_tokens"],
                        stop_reason=mapped["stop_reason"],
                        error_type=error_type,
                    )
                )
                return CompletionResult(
                    succeeded=False,
                    text=mapped["text"],
                    error_type=error_type,
                    records=records,
                )

            records.append(
                self._record(
                    request=request,
                    run_id=run_id,
                    attempt=attempt,
                    latency_ms=latency_ms,
                    text=mapped["text"],
                    input_tokens=mapped["input_tokens"],
                    output_tokens=mapped["output_tokens"],
                    stop_reason=mapped["stop_reason"],
                    error_type=None,
                )
            )
            return CompletionResult(
                succeeded=True,
                text=mapped["text"],
                error_type=None,
                records=records,
            )

        return CompletionResult(
            succeeded=False,
            text=None,
            error_type=TransientProviderError.__name__,
            records=records,
        )

    def _post(self, request: CompletionRequest) -> dict[str, Any]:
        try:
            response = httpx.post(
                f"{self._base_url}/api/generate",
                json={
                    "model": self.model_id,
                    "system": request.system,
                    "prompt": request.user_content,
                    "stream": False,
                    "think": False,
                    "options": {
                        "temperature": request.temperature,
                        "num_predict": request.max_output_tokens,
                    },
                },
                timeout=HTTP_TIMEOUT_SECONDS,
            )
        except (httpx.TimeoutException, httpx.NetworkError) as exc:
            raise TransientProviderError(str(exc)) from exc

        status = response.status_code
        if status in TRANSIENT_STATUS_CODES:
            raise TransientProviderError(f"HTTP {status}")
        if status >= 400:
            raise PermanentProviderError(f"HTTP {status}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise PermanentProviderError("malformed Ollama response") from exc
        if not isinstance(payload, dict):
            raise PermanentProviderError("Ollama generate response must be a JSON object")
        return payload

    def _record(
        self,
        *,
        request: CompletionRequest,
        run_id: str,
        attempt: int,
        latency_ms: int,
        text: str | None,
        input_tokens: int,
        output_tokens: int,
        stop_reason: str | None,
        error_type: str | None,
    ) -> CallRecord:
        return CallRecord(
            record_id=str(uuid4()),
            run_id=run_id,
            timestamp=datetime.now(UTC),
            provider=PROVIDER,
            model_id=self.model_id,
            task=request.task,
            case_id=request.case_id,
            prompt_id=request.prompt_id,
            prompt_version=request.prompt_version,
            attempt=attempt,
            temperature=request.temperature,
            max_output_tokens=request.max_output_tokens,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cached_input_tokens=None,
            latency_ms=latency_ms,
            cost_usd=compute_cost(self.model_id, input_tokens, output_tokens),
            stop_reason=stop_reason,
            error_type=error_type,
            response_text=text,
        )


def _backoff(attempt: int) -> None:
    delay = (2 ** (attempt - 1)) + random.random()
    time.sleep(delay)


def _map_payload(payload: dict[str, Any]) -> dict[str, Any]:
    text = payload.get("response")
    if text is None:
        message = payload.get("message")
        if isinstance(message, dict):
            text = message.get("content")
    return {
        "text": _as_optional_str(text),
        "input_tokens": _as_int(payload.get("prompt_eval_count"), "prompt_eval_count"),
        "output_tokens": _as_int(payload.get("eval_count"), "eval_count"),
        "stop_reason": _as_optional_str(payload.get("done_reason")),
    }


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
        raise PermanentProviderError(f"{field} must be an int")
    return value

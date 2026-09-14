from __future__ import annotations

import json
import re

from pydantic import BaseModel, ValidationError

from promptlab.adapters.base import CompletionRequest, ModelAdapter

_FENCE_PREFIX = re.compile(r"^```(?:json)?\s*", re.IGNORECASE)
_FENCE_SUFFIX = re.compile(r"\s*```$")


class StructuredCompletionError(ValueError):
    """Raised when a completion cannot be validated after bounded semantic repair."""


def complete_structured[T: BaseModel](
    adapter: ModelAdapter,
    request: CompletionRequest,
    schema: type[T],
    run_id: str,
    max_repairs: int = 1,
) -> T:
    """Return a schema-validated completion with a bounded semantic repair loop.

    Transport retry remains inside the adapter.
    Schema/content repair belongs here.

    On validation failure, send the validation error text back to the model and
    instruct it to correct only what the error concerns. Do not perform more
    than max_repairs semantic repair attempts.
    """

    current_request = request
    last_error: Exception | None = None

    for attempt in range(max_repairs + 1):
        result = adapter.complete(current_request, run_id)
        if not result.succeeded or not result.text:
            raise StructuredCompletionError(
                result.error_type or "empty completion"
            )

        try:
            payload = _parse_json(result.text)
            return schema.model_validate(payload)
        except (json.JSONDecodeError, ValidationError, ValueError) as exc:
            last_error = exc
            if attempt >= max_repairs:
                break
            current_request = _repair_request(request, result.text, exc)

    raise StructuredCompletionError(str(last_error)) from last_error


def _parse_json(text: str) -> object:
    stripped = text.strip()
    if stripped.startswith("```"):
        stripped = _FENCE_PREFIX.sub("", stripped, count=1)
        stripped = _FENCE_SUFFIX.sub("", stripped)
        stripped = stripped.strip()
    try:
        return json.loads(stripped)
    except json.JSONDecodeError:
        start = stripped.find("{")
        end = stripped.rfind("}")
        if start != -1 and end > start:
            return json.loads(stripped[start : end + 1])
        raise


def _repair_request(
    request: CompletionRequest,
    previous_text: str,
    error: Exception,
) -> CompletionRequest:
    user_content = (
        f"{request.user_content}\n\n"
        "The previous JSON output failed validation.\n"
        f"Previous output:\n{previous_text}\n\n"
        f"Validation error:\n{error}\n\n"
        "Correct only what the validation error concerns. "
        "Leave valid fields unchanged. "
        "Return only the corrected JSON object."
    )
    return request.model_copy(update={"user_content": user_content})

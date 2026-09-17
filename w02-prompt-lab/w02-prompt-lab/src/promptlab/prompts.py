"""Versioned prompt registry for the Week 2 prompt lab.

Markdown prompt files live in ``src/prompts/``.
This Python module lives in ``src/promptlab/``.

Day 4 student work: implement ``render_user``.
"""

from __future__ import annotations

import hashlib
import re
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from pydantic import BaseModel, ConfigDict

from promptlab.schemas import TaskName

PROMPT_DIR = Path(__file__).resolve().parents[1] / "prompts"

DOCUMENT_MARKER_CLOSE = "</document>"
CUSTOMER_MARKER_CLOSE = "</customer_message>"

_SAFE_COMPONENT = re.compile(r"^[A-Za-z0-9_-]+$")
_PLACEHOLDER = re.compile(r"\{([A-Za-z_][A-Za-z0-9_]*)\}")


@dataclass(frozen=True)
class TaskPrompt:
    """Day 5 prompt assignment: one versioned prompt, plus which model it was developed on."""

    prompt_id: str
    version: str
    developed_on: str

    def name(self) -> str:
        return f"{self.prompt_id}.{self.version}"

    def is_transfer(self, model_logical_name: str) -> bool:
        return model_logical_name != self.developed_on

    def label(self, model_logical_name: str) -> str:
        name = self.name()
        if self.is_transfer(model_logical_name):
            return f"{name} transfer"
        return name


# Default assignment is a transfer test on the Qwen-developed prompt.
# Summarization on Mistral uses an adapted version; summarize.v1 is preserved.
TASK_PROMPTS: dict[TaskName, TaskPrompt] = {
    "summarization": TaskPrompt("summarize", "v1", "qwen"),
    "extraction": TaskPrompt("extract", "v2", "qwen"),
    "triage": TaskPrompt("triage", "v1", "qwen"),
}

ADAPTED_PROMPTS: dict[tuple[TaskName, str], TaskPrompt] = {
    ("summarization", "mistral"): TaskPrompt("summarize", "v1-mistral", "mistral"),
}


def prompt_for(task: TaskName, model_logical_name: str) -> TaskPrompt:
    """Return the prompt this model should run for the task."""
    adapted = ADAPTED_PROMPTS.get((task, model_logical_name))
    if adapted is not None:
        return adapted
    return TASK_PROMPTS[task]


class MissingPromptVariableError(ValueError):
    """A required prompt variable was not supplied."""

    def __init__(self, missing: list[str]) -> None:
        self.missing = missing
        super().__init__(f"missing prompt variables: {', '.join(missing)}")


class PromptTemplate(BaseModel):
    """Immutable prompt text resolved by prompt id and version."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    prompt_id: str
    version: str
    system: str
    user_template: str
    template_hash: str


def _split_layers(text: str) -> tuple[str, str]:
    """Split optional ``## System`` / ``## User`` sections.

    Day 3 prompts may not have these headings, so those files remain
    valid user-only templates. Day 4 triage prompts may use the layered form.
    """
    system_marker = "## System"
    user_marker = "## User"

    if system_marker not in text or user_marker not in text:
        return "", text

    system_start = text.index(system_marker) + len(system_marker)
    user_start = text.index(user_marker, system_start)

    system = text[system_start:user_start].strip()
    user = text[user_start + len(user_marker):].strip()
    return system, user


def _prompt_path(prompt_id: str, version: str) -> Path:
    if not _SAFE_COMPONENT.fullmatch(prompt_id):
        raise ValueError(f"invalid prompt_id: {prompt_id!r}")
    if not _SAFE_COMPONENT.fullmatch(version):
        raise ValueError(f"invalid prompt version: {version!r}")
    return PROMPT_DIR / f"{prompt_id}.{version}.md"


def load(prompt_id: str, version: str) -> PromptTemplate:
    """Resolve an identifier and version to immutable text plus its hash."""
    path = _prompt_path(prompt_id, version)
    text = path.read_text(encoding="utf-8")
    system, user_template = _split_layers(text)
    digest = hashlib.sha256(text.encode("utf-8")).hexdigest()

    return PromptTemplate(
        prompt_id=prompt_id,
        version=version,
        system=system,
        user_template=user_template,
        template_hash=digest,
    )


def _placeholders(template: str) -> set[str]:
    """Return named placeholders without treating literal JSON braces as fields."""
    return set(_PLACEHOLDER.findall(template))


def _escape_untrusted(text: str) -> str:
    """Keep untrusted text from closing document or customer markers."""
    return text.replace(DOCUMENT_MARKER_CLOSE, "&lt;/document&gt;").replace(
        CUSTOMER_MARKER_CLOSE, "&lt;/customer_message&gt;"
    )


def render_user(
    template: PromptTemplate,
    variables: Mapping[str, str],
    untrusted: str,
) -> str:
    """Render the user layer safely.

    Day 4 student work.

    Requirements:
    - missing variables raise ``MissingPromptVariableError``
    - untrusted text cannot close a document/customer marker early
    - untrusted text is supplied through ``document_text``
    - literal JSON braces in prompt examples must remain literal
    """
    required = _placeholders(template.user_template)
    values = dict(variables)
    values["document_text"] = _escape_untrusted(untrusted)

    missing = sorted(name for name in required if name not in values)
    if missing:
        raise MissingPromptVariableError(missing)

    return _PLACEHOLDER.sub(lambda match: values[match.group(1)], template.user_template)
"""Errors used by the Week 2 local model lab."""

from typing import ClassVar


class UnknownModelError(ValueError):
    """Raised when a model identifier is not present in the configured model table."""

    retryable: ClassVar[bool] = False


class ProviderError(Exception):
    """Base class for adapter-classified model-call failures."""

    retryable: ClassVar[bool] = False


class TransientProviderError(ProviderError):
    """Timeout, connection failure, or temporary Ollama/server failure."""

    retryable: ClassVar[bool] = True


class PermanentProviderError(ProviderError):
    """Malformed request, unavailable model, or other non-retryable request failure."""

    retryable: ClassVar[bool] = False


class TruncatedResponseError(ProviderError):
    """Ollama reported that the output token ceiling was reached."""

    retryable: ClassVar[bool] = False

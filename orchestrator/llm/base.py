"""Pluggable LLM interface. Swap the mock for a real provider without touching agents."""
from typing import Protocol


class LLMError(RuntimeError):
    """Transient or permanent LLM failure (timeout, rate limit, bad response)."""


class LLMClient(Protocol):
    def complete(self, role: str, prompt: str) -> str: ...

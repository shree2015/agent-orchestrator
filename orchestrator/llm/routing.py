"""Compose LLM clients: route some roles to a real model, record every exchange, report usage.

  * RoutingLLM   - "hybrid" runs: the roles you choose go to the real model, the rest stay on the offline mock.
  * RecordingLLM - saves system prompt, prompt and response of every call to numbered JSON files: the way to inspect
                   exactly what a real run sends (run it on the MOCK first for a free preview) and to debug failures.
Neither ever sees the API key: it lives only inside the client.
"""
from __future__ import annotations

import json
import threading
from pathlib import Path

from . import contracts


def _clients(llm):
    inner = getattr(llm, "inner", None)
    if inner is not None:
        yield from _clients(inner)
    elif hasattr(llm, "default"):
        yield from _clients(llm.default)
        for c in llm.overrides.values():
            yield from _clients(c)
    else:
        yield llm


class RoutingLLM:
    def __init__(self, default, overrides: dict) -> None:
        self.default, self.overrides = default, overrides

    def complete(self, role: str, prompt: str) -> str:
        return self.overrides.get(role, self.default).complete(role, prompt)

    def bind_audit(self, audit) -> None:
        for c in {id(c): c for c in _clients(self)}.values():
            if hasattr(c, "bind_audit"):
                c.bind_audit(audit)

    @property
    def usage(self) -> dict:
        total = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        for c in {id(c): c for c in _clients(self)}.values():
            for k, v in getattr(c, "usage", {}).items():
                total[k] = total.get(k, 0) + v
        return total

    def __getattr__(self, name):                       # mock-only helpers (defaults, inject_failures...) pass through
        return getattr(self.default, name)


class RecordingLLM:
    def __init__(self, inner, directory) -> None:
        self.inner, self.directory = inner, Path(directory)
        self.directory.mkdir(parents=True, exist_ok=True)
        self._n, self._lock = 0, threading.Lock()

    def complete(self, role: str, prompt: str) -> str:
        with self._lock:
            self._n += 1
            n = self._n
        record = {"n": n, "role": role, "system": contracts.system_for(role), "prompt": prompt, "response": None, "error": None}
        try:
            record["response"] = self.inner.complete(role, prompt)
            return record["response"]
        except Exception as exc:
            record["error"] = f"{type(exc).__name__}: {exc}"
            raise
        finally:
            (self.directory / f"{n:03d}-{role}.json").write_text(json.dumps(record, indent=2), encoding="utf-8")

    def bind_audit(self, audit) -> None:
        if hasattr(self.inner, "bind_audit"):
            self.inner.bind_audit(audit)

    @property
    def usage(self) -> dict:
        return getattr(self.inner, "usage", {"calls": 0, "input_tokens": 0, "output_tokens": 0})

    def __getattr__(self, name):
        return getattr(self.inner, name)

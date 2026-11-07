"""Real LLM client for the Anthropic Messages API, using only the standard library (no SDK to install).

    client = AnthropicClient(model="claude-sonnet-5-5")          # key from the ANTHROPIC_API_KEY environment variable

Safety properties
  * The API key is read from the environment (or passed in) and is NEVER written to the audit log, recordings or errors.
  * A hard budget on calls (`max_calls`) stops runaway cost; truncated answers (stop_reason == max_tokens) are rejected
    instead of being handed to the gates as broken code.
  * Rate limits and transient server errors are retried with backoff (honouring Retry-After); authentication and
    unknown-model errors are fatal and fail fast without further network calls.
  * Every call is recorded in the audit log: role, model, token counts, latency, prompt hash (not the prompt), request id.

Details checked against Anthropic's Messages API documentation: POST /v1/messages; headers `x-api-key`,
`anthropic-version: 2023-06-01`, `content-type: application/json`; required body fields `model`, `max_tokens`,
`messages`; the system prompt is the top-level `system` field; the answer is a list of content blocks and usage
carries input_tokens/output_tokens.
"""
from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.request
from typing import Callable

from . import contracts
from .base import LLMError

API_URL = "https://api.anthropic.com/v1/messages"
API_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
RETRY_STATUS = {408, 409, 429, 500, 502, 503, 504, 529}
FATAL_STATUS = {401, 403, 404}

Transport = Callable[[str, dict, bytes, float], tuple]    # (url, headers, body, timeout) -> (status, headers, body bytes)
TRANSPORT_OVERRIDE: Transport | None = None               # tests (and offline demos) can replace the network here


def urllib_transport(url: str, headers: dict, body: bytes, timeout: float):
    request = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as resp:
            return resp.status, dict(resp.headers), resp.read()
    except urllib.error.HTTPError as exc:                  # a real HTTP answer (4xx/5xx): let the caller classify it
        return exc.code, dict(exc.headers or {}), exc.read()


def _api_message(body: bytes) -> str:
    try:
        err = json.loads(body)["error"]
        return f"{err.get('type', 'error')}: {err.get('message', '')}".strip()
    except (ValueError, KeyError, TypeError):
        return body[:200].decode("utf-8", "replace")


class AnthropicClient:
    def __init__(self, model: str = DEFAULT_MODEL, api_key: str | None = None, max_tokens: int = 16000,
                 timeout: float = 300.0, max_retries: int = 3, max_calls: int | None = 40,
                 transport: Transport | None = None, sleep: Callable[[float], None] = time.sleep,
                 system_for: Callable[[str], str] = contracts.system_for) -> None:
        self._key = api_key or os.environ.get("ANTHROPIC_API_KEY")
        if not self._key:
            raise LLMError("ANTHROPIC_API_KEY is not set. Set it in your terminal (never in a file you commit), e.g. "
                           "Windows: set ANTHROPIC_API_KEY=...   Linux/macOS: export ANTHROPIC_API_KEY=...")
        self.model, self.max_tokens, self.timeout = model, max_tokens, timeout
        self.max_retries, self.max_calls = max_retries, max_calls
        self._transport = transport or TRANSPORT_OVERRIDE or urllib_transport
        self._sleep, self._system_for = sleep, system_for
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "input_tokens": 0, "output_tokens": 0}
        self._audit: Callable[[dict], None] | None = None
        self._fatal: str | None = None

    def bind_audit(self, audit: Callable[[dict], None]) -> None:
        self._audit = audit

    # ------------------------------------------------------------------ public
    def complete(self, role: str, prompt: str) -> str:
        with self._lock:
            if self._fatal:
                raise LLMError(self._fatal)
            if self.max_calls is not None and self.usage["calls"] >= self.max_calls:
                raise LLMError(f"LLM call budget exhausted ({self.max_calls} calls); raise --max-llm-calls if intended")
            self.usage["calls"] += 1
        body = json.dumps({"model": self.model, "max_tokens": self.max_tokens, "system": self._system_for(role),
                           "messages": [{"role": "user", "content": prompt}]}).encode("utf-8")
        started = time.monotonic()
        status, headers, payload = self._post(body)
        data = self._parse(payload)
        text = "".join(b.get("text", "") for b in data.get("content", []) if b.get("type") == "text")
        usage = data.get("usage", {})
        with self._lock:
            self.usage["input_tokens"] += int(usage.get("input_tokens", 0))
            self.usage["output_tokens"] += int(usage.get("output_tokens", 0))
        if self._audit:
            self._audit({"ts": time.time(), "event": "llm_call", "role": role, "model": data.get("model", self.model),
                         "input_tokens": usage.get("input_tokens", 0), "output_tokens": usage.get("output_tokens", 0),
                         "latency_s": round(time.monotonic() - started, 3), "stop_reason": data.get("stop_reason"),
                         "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16],
                         "request_id": headers.get("request-id") or headers.get("Request-Id")})
        if data.get("stop_reason") == "max_tokens":
            raise LLMError(f"the {role} answer was cut off at max_tokens={self.max_tokens}; raise --max-tokens or "
                           "make the task smaller (a truncated artifact would be broken)")
        if not text.strip():
            raise LLMError(f"the model returned no text for role '{role}'")
        return text

    # ---------------------------------------------------------------- internals
    def _post(self, body: bytes):
        headers = {"x-api-key": self._key, "anthropic-version": API_VERSION, "content-type": "application/json"}
        last = "no attempt made"
        for attempt in range(self.max_retries + 1):
            try:
                status, resp_headers, payload = self._transport(API_URL, headers, body, self.timeout)
            except (OSError, TimeoutError) as exc:               # connection reset, DNS failure, timeout...
                last = f"network error: {type(exc).__name__}"
                status, resp_headers, payload = None, {}, b""
            if status == 200:
                return status, resp_headers, payload
            if status is not None:
                last = f"HTTP {status} {_api_message(payload)}"
                if status in FATAL_STATUS:
                    hint = {401: "check ANTHROPIC_API_KEY", 403: "this key may lack access to the model or workspace",
                            404: "the model name may be wrong or unavailable to your account: set --model"}[status]
                    self._fatal = f"{last} ({hint})"
                    raise LLMError(self._fatal)
                if status not in RETRY_STATUS:
                    raise LLMError(last)
            if attempt == self.max_retries:
                break
            delay = min(30.0, 2.0 ** attempt)
            retry_after = {k.lower(): v for k, v in resp_headers.items()}.get("retry-after")
            if retry_after:
                try:
                    delay = min(60.0, max(delay, float(retry_after)))
                except ValueError:
                    pass
            self._sleep(delay)
        raise LLMError(f"giving up after {self.max_retries + 1} attempts: {last}")

    @staticmethod
    def _parse(payload: bytes) -> dict:
        try:
            data = json.loads(payload)
        except ValueError:
            raise LLMError("the API returned something that is not JSON") from None
        if not isinstance(data, dict) or not isinstance(data.get("content"), list):
            raise LLMError("unexpected response shape from the API (no content list)")
        return data

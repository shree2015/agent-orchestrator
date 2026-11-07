"""Deterministic offline LLM for demos and tests (no API key, no cost).

Two knobs make failure paths demonstrable:
  * fail_times: {"developer": 2}  -> first 2 calls for that role raise LLMError
  * inject_failures(role, n)      -> the NEXT n calls for that role raise LLMError
                                     (lets a demo break an agent mid-run)
  * scripted:   {"developer": [bad_code, good_code]} -> responses served in order
                (last one repeats), e.g. first answer violates policy.
"""
from __future__ import annotations

import threading
from collections import defaultdict

from .base import LLMError

REQUIREMENTS = """{
  "goal": "slugify(text) converts arbitrary text to a URL-safe slug",
  "functional": ["lowercase", "replace runs of non-alphanumerics with '-'", "strip leading/trailing '-'", "transliterate accents to ASCII", "optional max_length truncation"],
  "ambiguities": ["empty input -> return empty string (assumed)", "non-str input -> TypeError (assumed)"],
  "acceptance": ["slugify('Hello, World!') == 'hello-world'", "slugify('Crème Brûlée') == 'creme-brulee'"]
}"""

DESIGN = """Pure function slugify(text: str, max_length: int = 80) -> str in slugify.py.
Pipeline: validate type -> NFKD normalize + ASCII fold -> lowercase -> regex collapse -> strip '-' -> truncate (no trailing '-').
No I/O, no global state, no dependencies (stdlib only). Errors: TypeError (non-str), ValueError (max_length < 1)."""

GOOD_CODE = '''import re
import unicodedata


def slugify(text: str, max_length: int = 80) -> str:
    """Convert text to a URL-safe slug."""
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if max_length < 1:
        raise ValueError("max_length must be >= 1")
    ascii_text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    slug = re.sub(r"[^a-z0-9]+", "-", ascii_text.lower()).strip("-")
    return slug[:max_length].rstrip("-")
'''

BAD_CODE_SECRET = GOOD_CODE + '\nAPI_KEY = "sk-live-1234567890abcdefgh"\n'
# Fixtures that each trip exactly one governance policy (used by `--violate` demos and tests):
BAD_CODE_IMPORT = "import requests\n" + GOOD_CODE                                   # security/dependency-allowlist
UNDOCUMENTED_CODE = GOOD_CODE.replace('    """Convert text to a URL-safe slug."""\n', "")  # compliance/public-api-documented

TESTS = '''def test_basic():
    assert slugify("Hello, World!") == "hello-world"

def test_accents():
    assert slugify("Crème Brûlée") == "creme-brulee"

def test_empty_and_symbols():
    assert slugify("") == ""
    assert slugify("!!!") == ""

def test_truncate_no_trailing_dash():
    assert slugify("hello world", max_length=6) == "hello"

def test_type_and_value_errors():
    for bad, kw, exc in [(None, {}, TypeError), ("a", {"max_length": 0}, ValueError)]:
        try:
            slugify(bad, **kw)
        except exc:
            continue
        raise AssertionError("expected " + exc.__name__)
'''

THIN_TESTS = "def test_basic():\n    assert slugify(\"Hello, World!\") == \"hello-world\"\n"   # compliance/minimum-tests

DOCS = "# slugify\n\n`slugify(text, max_length=80)` returns a lowercase, ASCII, hyphen-separated slug.\n\n| Input | Output |\n|---|---|\n| `Hello, World!` | `hello-world` |\n| `Crème Brûlée` | `creme-brulee` |\n\nRaises `TypeError` for non-str input and `ValueError` if `max_length < 1`.\n"

DEFAULTS = {
    "requirements": REQUIREMENTS,
    "architect": DESIGN,
    "developer": GOOD_CODE,
    "developer_fallback": GOOD_CODE,     # conservative alternate strategy used by the fallback path
    "tester": TESTS,
    "doc_writer": DOCS,
    "reviewer": "APPROVE: code, tests and docs are consistent with the design.",
}


class MockLLM:
    def __init__(self, fail_times: dict[str, int] | None = None,
                 scripted: dict[str, list[str]] | None = None,
                 defaults: dict[str, str] | None = None) -> None:
        self.defaults = {**DEFAULTS, **(defaults or {})}      # a workflow supplies its own artifacts
        self._pending_failures: dict[str, int] = dict(fail_times or {})
        self.scripted = scripted or {}
        self.calls: dict[str, int] = defaultdict(int)       # every call, including failures
        self.successes: dict[str, int] = defaultdict(int)   # calls that returned an answer
        self._lock = threading.Lock()

    def inject_failures(self, role: str, n: int) -> None:
        """Make the next n calls for `role` fail."""
        with self._lock:
            self._pending_failures[role] = self._pending_failures.get(role, 0) + n

    def complete(self, role: str, prompt: str) -> str:
        with self._lock:
            self.calls[role] += 1
            if self._pending_failures.get(role, 0) > 0:
                self._pending_failures[role] -= 1
                raise LLMError(f"injected failure for {role} (call {self.calls[role]})")
            self.successes[role] += 1
            k = self.successes[role]
        if role in self.scripted:
            seq = self.scripted[role]
            return seq[min(k - 1, len(seq) - 1)]
        return self.defaults[role]

"""Greenfield scenario: build the URL shortener through the full governed SDLC graph.

The graph and every gate are the same as for the toy workflow; what changes is the requirement, the
size limit and the artifacts the (offline, mock) agents return. The artifacts are stored as reference
files under `reference/shortener/`; with a real LLM client the same graph would generate them, and every
gate (security, compliance, change control, sandboxed tests) would judge the generated code identically.
"""
from __future__ import annotations

from pathlib import Path

from ..llm import mock_client as mc
from . import slugify

_REF = Path(__file__).parent / "reference" / "shortener"
read = lambda name: (_REF / name).read_text(encoding="utf-8")

REQUIREMENT = ("Build a URL shortener service: shorten, redirect with expiry, click analytics, "
               "custom aliases, rate limiting and basic abuse protection. Ship tests and docs.")
CODE_PATH = "shortener.py"
MAX_LINES = 600          # a whole service is bigger than the toy default of 200: an explicit, reviewed limit

GOOD_CODE = read("shortener.py.txt")


def _without(text: str, fragment: str) -> str:
    assert fragment in text, "fixture fragment not found"
    return text.replace(fragment, "", 1)


DEFAULTS = {
    "requirements": read("requirements.json"),
    "architect": read("design.md.txt"),
    "developer": GOOD_CODE,
    "developer_fallback": GOOD_CODE,
    "tester": read("tests.py.txt"),
    "doc_writer": read("docs.md"),
}

FIXTURES = {
    "good_code": GOOD_CODE,
    "bad_secret": GOOD_CODE + '\nAPI_KEY = "sk-live-1234567890abcdefgh"\n',
    "bad_import": "import requests\n" + GOOD_CODE,
    "undocumented": _without(GOOD_CODE, '    """Run the service with the standard-library WSGI server (development use; single-threaded)."""\n'),
    "thin_tests": 'def test_one():\n    assert validate_url("https://example.com/a") == "https://example.com/a"\n',
}


def build(llm, requirement: str = REQUIREMENT):
    return slugify.build(llm, requirement, code_path=CODE_PATH, max_lines=MAX_LINES)


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted, defaults=DEFAULTS)

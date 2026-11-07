"""Greenfield scenario: build the URL shortener through the full governed SDLC graph.

The graph and every gate are the same as for the toy workflow; what changes is the requirement, the
size limit and the artifacts the (offline, mock) agents return. The artifacts are stored as reference
files under `reference/shortener/`; with a real LLM client the same graph would generate them, and every
gate (security, compliance, change control, sandboxed tests) would judge the generated code identically.
"""
from __future__ import annotations

from pathlib import Path

from ..llm import mock_client as mc
from . import plan_fixtures as pf
from . import slugify

_REF = Path(__file__).parent / "reference" / "shortener"
read = lambda name: (_REF / name).read_text(encoding="utf-8")

REQUIREMENT = ("Build a URL shortener service: shorten, redirect with expiry, click analytics, "
               "custom aliases, rate limiting and basic abuse protection, plus a simple web UI. Ship tests and docs.")
CODE_PATH = "shortener.py"
MAX_LINES = 900          # a whole service is bigger than the toy default of 200: explicit, reviewed limits (the stored reference is 507 lines;
MAX_UI_LINES = 700       # a real model is wordier, and the first live run wrote 687). Both can be set per run: --max-lines-changed / --max-ui-lines

SIZE_LIMITS = {"max_lines": MAX_LINES, "max_ui_lines": MAX_UI_LINES}     # the per-run settings this workflow accepts, with defaults

GOOD_CODE = read("shortener.py.txt")
GOOD_UI = read("ui.html.txt")


def _replace(text: str, old: str, new: str) -> str:
    assert old in text, f"fixture fragment not found: {old[:40]!r}"
    return text.replace(old, new, 1)


def _without(text: str, fragment: str) -> str:
    return _replace(text, fragment, "")


DEFAULTS = {
    "requirements": read("requirements.json"),
    "architect": read("design.md.txt"),
    "developer": GOOD_CODE,
    "developer_fallback": GOOD_CODE,
    "tester": read("tests.py.txt"),
    "doc_writer": read("docs.md"),
    "frontend": GOOD_UI,
    "planner": read("plan.json"),
}

FIXTURES = {
    "good_code": GOOD_CODE,
    "bad_secret": GOOD_CODE + '\nAPI_KEY = "sk-live-1234567890abcdefgh"\n',
    "bad_import": "import requests\n" + GOOD_CODE,
    "undocumented": _without(GOOD_CODE, '    """Run the service with the standard-library WSGI server (development use; single-threaded)."""\n'),
    "ui_innerhtml": _replace(GOOD_UI, "container.textContent = '';", "container.innerHTML = '';"),
    "ui_cdn": _replace(GOOD_UI, "<title>", '<script src="https://cdn.example.com/lib.js"></script>\n<title>'),
    "ui_unlabelled": _replace(GOOD_UI, '<label for="alias">Custom alias (optional)</label>', "<span>Custom alias (optional)</span>"),
    "thin_tests": 'def test_one():\n    assert validate_url("https://example.com/a") == "https://example.com/a"\n',
}


VIOLATIONS = {
    "secret": ("developer", FIXTURES["bad_secret"], False),
    "import": ("developer", FIXTURES["bad_import"], False),
    "undocumented": ("developer", FIXTURES["undocumented"], False),
    "thin-tests": ("tester", FIXTURES["thin_tests"], True),
    "empty-docs": ("doc_writer", "", True),
    "ui-innerhtml": ("frontend", FIXTURES["ui_innerhtml"], False),
    "ui-cdn": ("frontend", FIXTURES["ui_cdn"], False),
    "ui-unlabelled": ("frontend", FIXTURES["ui_unlabelled"], False),
    "plan-cycle": ("planner", pf.plan_cycle(DEFAULTS["planner"]), False),
    "plan-misses-criterion": ("planner", pf.plan_misses_criterion(DEFAULTS["planner"]), False),
    "plan-wrong-order": ("planner", pf.plan_wrong_order(DEFAULTS["planner"]), False),
}


def build(llm, requirement: str = REQUIREMENT, max_lines: int = MAX_LINES, max_ui_lines: int = MAX_UI_LINES):
    return slugify.build(llm, requirement, code_path=CODE_PATH, max_lines=max_lines, ui=True, plan=True, max_ui_lines=max_ui_lines)


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted, defaults=DEFAULTS)

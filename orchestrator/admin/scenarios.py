"""The scenarios the admin page can launch. Brownfield is left out on purpose: it needs a baseline codebase."""
from __future__ import annotations

from pathlib import Path

from ..workflows import ambiguous, shortener, slugify

ROOT = Path(__file__).resolve().parents[2]
TEMPLATE = ROOT / "examples" / "requirements" / "url-shortener.md"

SCENARIOS = {
    "shortener": {
        "title": "URL shortener (greenfield, with web UI)",
        "description": "Builds the whole service from your requirement: plan, design, code, tests, docs, UI, review, release. "
                       "The full demo. With a real model this is the expensive one (8+ calls).",
        "workflow": shortener, "asks_questions": False,
    },
    "ambiguous": {
        "title": "Ambiguous request: \"make links expire sensibly\"",
        "description": "A vague request on the existing shortener. You answer the high-risk questions on this page, "
                       "and can change an answer afterwards to see the automatic re-plan.",
        "workflow": ambiguous, "asks_questions": True,
    },
    "slugify": {
        "title": "Quick test: a small function (cheapest first live test)",
        "description": "Writes a tiny slugify function with tests and docs. About 6 model calls: the best first test of your key and model.",
        "workflow": slugify, "asks_questions": False,
    },
}


def default_requirement(scenario: str) -> str:
    return SCENARIOS[scenario]["workflow"].REQUIREMENT


def template_requirement(scenario: str) -> str:
    """Longer plain-English starting point where one exists (the shortener); otherwise the built-in requirement."""
    if scenario == "shortener" and TEMPLATE.exists():
        text = TEMPLATE.read_text(encoding="utf-8")
        return text.split("\n---\n", 1)[1].strip() if "\n---\n" in text else text
    return default_requirement(scenario)


def public_list() -> list[dict]:
    return [{"id": k, "title": v["title"], "description": v["description"], "asks_questions": v["asks_questions"],
             "default_requirement": default_requirement(k), "has_template": k == "shortener" and TEMPLATE.exists()}
            for k, v in SCENARIOS.items()]

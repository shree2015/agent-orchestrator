"""Compliance policies: evidence that required engineering practice was followed.

Subject: Mapping[str, str] of artifact name -> text, e.g.
  {"requirements": ..., "design": ..., "implementation": ..., "tests": ..., "docs": ..., "review": ...}

FAIL CLOSED: if an artifact a policy needs is missing or blank, that is a violation, not a pass.
"""
from __future__ import annotations

import ast
import json
import re
from typing import Mapping

from .. import codeintel as ci
from .base import Policy, PolicySet

RELEASE_EVIDENCE = ("requirements", "design", "implementation", "tests", "docs", "review")


def _missing(subject: Mapping[str, str], *keys: str) -> list[str]:
    return [f"missing or empty artifact: {k}" for k in keys if not (subject.get(k) or "").strip()]


def _parse(text: str):
    try:
        return ast.parse(text)
    except SyntaxError:
        return None


def _public_defs(tree) -> list:
    return [n for n in tree.body
            if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)) and not n.name.startswith("_")]


def public_api_documented() -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "implementation")):
            return m
        tree = _parse(subject["implementation"])
        if tree is None:
            return ["implementation is not parseable"]
        return [f"public {'class' if isinstance(n, ast.ClassDef) else 'function'} '{n.name}' has no docstring"
                for n in _public_defs(tree) if ast.get_docstring(n) is None]
    return Policy("public-api-documented", "compliance", "Every public function/class needs a docstring", check)


def minimum_tests(minimum: int = 3) -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "tests")):
            return m
        tree = _parse(subject["tests"])
        if tree is None:
            return ["tests are not parseable"]
        n = sum(1 for d in tree.body if isinstance(d, ast.FunctionDef) and d.name.startswith("test_"))
        return [f"only {n} test(s); policy requires at least {minimum}"] if n < minimum else []
    return Policy("minimum-tests", "compliance", f"At least {minimum} tests must accompany the code", check)


def docs_cover_public_api() -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "implementation", "docs")):
            return m
        tree = _parse(subject["implementation"])
        if tree is None:
            return ["implementation is not parseable"]
        return [f"docs do not mention public API '{n.name}'"
                for n in _public_defs(tree) if n.name not in subject["docs"]]
    return Policy("docs-cover-public-api", "compliance", "Docs must mention every public function/class", check)


def release_evidence(required: tuple[str, ...] = RELEASE_EVIDENCE) -> Policy:
    """Release does not trust upstream nodes: it re-verifies the evidence itself (defence in depth)."""
    def check(subject: Mapping[str, str]) -> list[str]:
        out = _missing(subject, *required)
        review = (subject.get("review") or "").strip().upper()
        if "review" in required and review and not review.startswith("APPROVE"):
            out.append("review has not approved the change")
        return out
    return Policy("release-evidence", "compliance", "Release needs every artifact plus an approving review", check)


def ui_uses_documented_api(endpoints: tuple[str, ...] = ("/api/shorten", "/api/links/")) -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "ui")):
            return m
        return [f"UI never calls documented endpoint {e}" for e in endpoints if e not in subject["ui"]]
    return Policy("ui-uses-documented-api", "compliance", "The UI must be wired to the documented API", check)


def ui_accessibility_basics() -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "ui")):
            return m
        html, out = subject["ui"], []
        if not re.search(r"<html[^>]*\blang=", html, re.I):
            out.append("<html> needs a lang attribute")
        if not re.search(r"<title>\s*[^<\s]", html, re.I):
            out.append("page needs a non-empty <title>")
        controls = re.findall(r"<(?:input|select|textarea)\b[^>]*\bid=\"([^\"]+)\"", html, re.I)
        labelled = set(re.findall(r"<label[^>]*\bfor=\"([^\"]+)\"", html, re.I))
        out += [f"form control '{c}' has no <label for=...>" for c in controls if c not in labelled]
        return out
    return Policy("ui-accessibility-basics", "compliance", "lang, title and labelled form controls", check)


def ui_set() -> PolicySet:
    return PolicySet(ui_uses_documented_api(), ui_accessibility_basics())


def code_set() -> PolicySet:
    return PolicySet(public_api_documented())


def tests_set(minimum: int = 3) -> PolicySet:
    return PolicySet(minimum_tests(minimum))


def docs_set() -> PolicySet:
    return PolicySet(docs_cover_public_api())


def release_set() -> PolicySet:
    return PolicySet(release_evidence())


# ------------------------------------------------------------------ brownfield (existing codebase)
def impact_analysis_grounded(baseline_code: str) -> Policy:
    """The analysis must be checkable: every symbol it names has to exist in the real baseline code."""
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "impact_analysis")):
            return m
        try:
            impact = json.loads(subject["impact_analysis"])
            known = set(ci.symbols(baseline_code))
        except (ValueError, SyntaxError):
            return ["impact analysis is not valid JSON"]
        if not isinstance(impact, dict):
            return ["impact analysis must be a JSON object"]
        out = [f"impact analysis is missing '{k}'" for k in ("root_cause", "impacted", "risks") if not impact.get(k)]
        for item in impact.get("impacted") or []:
            sym = item.get("symbol") if isinstance(item, dict) else None
            if sym not in known:
                out.append(f"impacted symbol '{sym}' does not exist in the baseline code")
        out += [f"new symbol '{n}' already exists in the baseline code" for n in impact.get("new_symbols") or [] if n in known]
        return out
    return Policy("impact-analysis-grounded", "compliance", "Impact analysis may only name symbols that exist", check)


def no_tests_removed(baseline_tests: str) -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "tests")):
            return m
        try:
            gone = ci.test_names(baseline_tests) - ci.test_names(subject["tests"])
        except SyntaxError:
            return ["tests are not parseable"]
        return [f"existing test was removed: {n}" for n in sorted(gone)]
    return Policy("no-tests-removed", "compliance", "A fix may add tests, never delete them", check)


def regression_test_reproduces_bug(baseline_code: str, baseline_tests: str, timeout: int = 60) -> Policy:
    """Proof the new tests matter: at least one NEW test must fail when run against the OLD code."""
    def check(subject: Mapping[str, str]) -> list[str]:
        from .. import sandbox
        if (m := _missing(subject, "tests")):
            return m
        try:
            new_tests = ci.test_names(subject["tests"]) - ci.test_names(baseline_tests)
        except SyntaxError:
            return ["tests are not parseable"]
        if not new_tests:
            return ["no new test was added: a fix needs a regression test"]
        result = sandbox.run_tests(baseline_code, subject["tests"], timeout=timeout)
        if not (set(result["failed"]) & new_tests):
            return ["none of the new tests fail on the baseline code, so they do not reproduce the bug"]
        return []
    return Policy("regression-test-reproduces-bug", "compliance", "New tests must fail on the old code", check)


def changelog_references_ticket(ticket: str) -> Policy:
    def check(subject: Mapping[str, str]) -> list[str]:
        if (m := _missing(subject, "docs")):
            return m
        return [] if ticket in subject["docs"] else [f"changelog does not reference {ticket}"]
    return Policy("changelog-references-ticket", "compliance", "Every fix is traceable to its ticket", check)

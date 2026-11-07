"""Compliance policies: evidence that required engineering practice was followed.

Subject: Mapping[str, str] of artifact name -> text, e.g.
  {"requirements": ..., "design": ..., "implementation": ..., "tests": ..., "docs": ..., "review": ...}

FAIL CLOSED: if an artifact a policy needs is missing or blank, that is a violation, not a pass.
"""
from __future__ import annotations

import ast
from typing import Mapping

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


def code_set() -> PolicySet:
    return PolicySet(public_api_documented())


def tests_set(minimum: int = 3) -> PolicySet:
    return PolicySet(minimum_tests(minimum))


def docs_set() -> PolicySet:
    return PolicySet(docs_cover_public_api())


def release_set() -> PolicySet:
    return PolicySet(release_evidence())

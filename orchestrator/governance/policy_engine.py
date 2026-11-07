"""Backwards-compatible entry point for the security checks.

The rules now live in `governance/policies/security.py` as named policies. This wrapper keeps the
old `check_security(code) -> list[str]` API (used by simple_graph) and adds the syntax-error report.
"""
from __future__ import annotations

import ast

from .policies import security


def check_security(code: str) -> list[str]:
    violations = [v.message for v in security.default_set().evaluate(code)]
    try:
        ast.parse(code)
    except SyntaxError as exc:
        violations.append(f"syntax error: {exc.msg}")
    return violations

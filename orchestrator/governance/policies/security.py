"""Security policies. Subject: generated source code (str).

Each policy is a small factory so a workflow can configure it (e.g. its own dependency allowlist).
AST-based policies ignore unparseable code: the `valid_python` gate reports syntax errors.
"""
from __future__ import annotations

import ast
import re
import sys

from .base import Policy, PolicySet

SECRET_PATTERNS = [
    r"(?i)(api[_-]?key|secret|password|token)\s*=\s*['\"][^'\"]{8,}['\"]",
    r"sk-[A-Za-z0-9\-]{10,}",
    r"AKIA[0-9A-Z]{16}",
]
FORBIDDEN_CALLS = {"eval", "exec", "compile", "__import__"}
FORBIDDEN_ATTRS = {("os", "system"), ("subprocess", "Popen"), ("subprocess", "run")}
UNSAFE_IMPORTS = frozenset({"ctypes", "pickle", "marshal"})


def _tree(code: str):
    try:
        return ast.parse(code)
    except SyntaxError:
        return None


def no_hardcoded_secrets() -> Policy:
    def check(code: str) -> list[str]:
        return [f"possible hardcoded secret (pattern {i})"
                for i, p in enumerate(SECRET_PATTERNS) if re.search(p, code)]
    return Policy("no-hardcoded-secrets", "security", "Source must not embed credentials", check)


def no_dangerous_calls() -> Policy:
    def check(code: str) -> list[str]:
        tree, out = _tree(code), []
        for node in ast.walk(tree) if tree else []:
            if isinstance(node, ast.Call):
                f = node.func
                if isinstance(f, ast.Name) and f.id in FORBIDDEN_CALLS:
                    out.append(f"forbidden call: {f.id}()")
                if isinstance(f, ast.Attribute) and isinstance(f.value, ast.Name) \
                        and (f.value.id, f.attr) in FORBIDDEN_ATTRS:
                    out.append(f"forbidden call: {f.value.id}.{f.attr}()")
        return out
    return Policy("no-dangerous-calls", "security", "No eval/exec/os.system/subprocess", check)


def _imported_modules(tree) -> list[str]:
    mods = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            mods += [a.name.split(".")[0] for a in node.names]
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            mods.append(node.module.split(".")[0])
    return mods


def no_unsafe_imports(blocked: frozenset[str] = UNSAFE_IMPORTS) -> Policy:
    def check(code: str) -> list[str]:
        tree = _tree(code)
        return [f"unsafe import: {m}" for m in sorted(set(_imported_modules(tree)) & blocked)] if tree else []
    return Policy("no-unsafe-imports", "security", "No modules that enable arbitrary code or memory access", check)


def dependency_allowlist(allowed: frozenset[str] = frozenset()) -> Policy:
    """Standard library is always fine; any third-party import must be on the allowlist."""
    def check(code: str) -> list[str]:
        tree = _tree(code)
        if not tree:
            return []
        third_party = {m for m in _imported_modules(tree) if m not in sys.stdlib_module_names}
        return [f"unapproved dependency: {m}" for m in sorted(third_party - allowed)]
    return Policy("dependency-allowlist", "security", "Third-party imports must be pre-approved", check)


def default_set(allowed_dependencies: frozenset[str] = frozenset()) -> PolicySet:
    return PolicySet(no_hardcoded_secrets(), no_dangerous_calls(), no_unsafe_imports(),
                     dependency_allowlist(allowed_dependencies))

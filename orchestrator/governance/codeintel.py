"""Small, dependency-free code intelligence used by the brownfield gates (AST only).

Granularity is the *symbol*: a top-level function, a method (`Class.method`), the non-method body of a
class (`Class.<class>`) and everything else at module level (`<module>`). Comparing symbols between a
baseline and a proposal tells us exactly what a change touched, independent of formatting or line moves.
"""
from __future__ import annotations

import ast

_FUNCS = (ast.FunctionDef, ast.AsyncFunctionDef)


def symbols(code: str) -> dict[str, str]:
    """Map qualified symbol name -> a fingerprint of its AST (docstrings and bodies included)."""
    tree = ast.parse(code)
    out: dict[str, str] = {}
    out["<module>"] = "\n".join(ast.dump(n) for n in tree.body if not isinstance(n, (*_FUNCS, ast.ClassDef)))
    for node in tree.body:
        if isinstance(node, _FUNCS):
            out[node.name] = ast.dump(node)
        elif isinstance(node, ast.ClassDef):
            methods = [m for m in node.body if isinstance(m, _FUNCS)]
            rest = [m for m in node.body if not isinstance(m, _FUNCS)]
            out[f"{node.name}.<class>"] = repr((node.name, [ast.dump(b) for b in node.bases], [ast.dump(r) for r in rest]))
            for m in methods:
                out[f"{node.name}.{m.name}"] = ast.dump(m)
    return out


def changed_symbols(baseline: str, proposed: str) -> dict[str, str]:
    """Symbols that differ: {name: 'added' | 'removed' | 'modified'}."""
    old, new = symbols(baseline), symbols(proposed)
    result = {n: "added" for n in new.keys() - old.keys()}
    result.update({n: "removed" for n in old.keys() - new.keys()})
    result.update({n: "modified" for n in old.keys() & new.keys() if old[n] != new[n]})
    return result


def test_names(code: str) -> set[str]:
    """Names of top-level `test_*` functions."""
    return {n.name for n in ast.parse(code).body if isinstance(n, ast.FunctionDef) and n.name.startswith("test_")}


def _signature(fn) -> dict:
    a = fn.args
    return {"pos": [x.arg for x in a.posonlyargs + a.args], "defaults": len(a.defaults),
            "kwonly": {x.arg: d is not None for x, d in zip(a.kwonlyargs, a.kw_defaults)},
            "vararg": a.vararg is not None, "kwarg": a.kwarg is not None}


def public_api(code: str) -> dict[str, dict | None]:
    """Public classes (None) and functions/methods (signature dict). `__init__` counts as public."""
    out: dict[str, dict | None] = {}
    for node in ast.parse(code).body:
        if isinstance(node, _FUNCS) and not node.name.startswith("_"):
            out[node.name] = _signature(node)
        elif isinstance(node, ast.ClassDef) and not node.name.startswith("_"):
            out[node.name] = None
            for m in node.body:
                if isinstance(m, _FUNCS) and (not m.name.startswith("_") or m.name == "__init__"):
                    out[f"{node.name}.{m.name}"] = _signature(m)
    return out


def incompatibilities(old: dict | None, new: dict | None) -> list[str]:
    """Reasons `new` is NOT backward compatible with `old` (empty list = compatible)."""
    if old is None or new is None:
        return []
    why = []
    if new["pos"][:len(old["pos"])] != old["pos"]:
        why.append("positional parameters were renamed, removed or reordered")
    if len(new["pos"]) - new["defaults"] > len(old["pos"]) - old["defaults"]:
        why.append("a new required positional parameter was added")
    for name in old["kwonly"]:
        if name not in new["kwonly"]:
            why.append(f"keyword-only parameter '{name}' was removed")
    why += [f"new keyword-only parameter '{n}' has no default" for n, has in new["kwonly"].items()
            if n not in old["kwonly"] and not has]
    if old["vararg"] and not new["vararg"]:
        why.append("*args support was removed")
    if old["kwarg"] and not new["kwarg"]:
        why.append("**kwargs support was removed")
    return why


def _const(node):
    """Evaluate a tiny, safe subset of constant expressions (literals and integer arithmetic)."""
    if isinstance(node, ast.Constant) and isinstance(node.value, (int, float, str, bool, type(None))):
        return node.value
    if isinstance(node, ast.UnaryOp) and isinstance(node.op, ast.USub):
        return -_const(node.operand)
    if isinstance(node, ast.BinOp) and isinstance(node.op, (ast.Add, ast.Sub, ast.Mult, ast.FloorDiv)):
        a, b = _const(node.left), _const(node.right)
        return {ast.Add: lambda: a + b, ast.Sub: lambda: a - b, ast.Mult: lambda: a * b, ast.FloorDiv: lambda: a // b}[type(node.op)]()
    raise ValueError("not a simple constant")


def module_constants(code: str) -> dict:
    """Module-level `NAME = <simple constant expression>` assignments, evaluated safely (no code is run)."""
    out = {}
    for node in ast.parse(code).body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name):
            try:
                out[node.targets[0].id] = _const(node.value)
            except (ValueError, TypeError, ZeroDivisionError):
                continue
    return out

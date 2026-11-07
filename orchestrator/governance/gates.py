"""Reusable entry/exit gates. Each returns (passed, reason)."""
from __future__ import annotations

import ast
import json

from . import sandbox
from .policies import artifact_gate, code_gate, entry_artifact_gate
from .policies import change_control, compliance, security


def json_has_keys(*keys):
    def gate(ctx, out):
        try:
            data = json.loads(out["content"])
        except json.JSONDecodeError as e:
            return False, f"output is not valid JSON: {e}"
        missing = [k for k in keys if k not in data]
        return (not missing, f"missing keys: {missing}" if missing else "ok")
    return gate


def valid_python(ctx, out):
    try:
        ast.parse(out["content"])
        return True, "ok"
    except SyntaxError as e:
        return False, f"syntax error: {e.msg}"


# ---- policy-backed gates (each failure names its policy: "[category/policy-id] message") ----
security_policy = code_gate(security.default_set())                       # security
code_compliance = artifact_gate(compliance.code_set(), "implementation")  # compliance
tests_compliance = artifact_gate(compliance.tests_set(), "tests")
docs_compliance = artifact_gate(compliance.docs_set(), "docs")
release_compliance = entry_artifact_gate(compliance.release_set())         # entry gate for release
ui_security = code_gate(security.ui_set())                                 # HTML/JS security
ui_compliance = artifact_gate(compliance.ui_set(), "ui")                   # API wiring + accessibility


def release_compliance_for(required: tuple[str, ...]):
    """Release entry gate for workflows whose evidence list differs (e.g. one that also ships a UI)."""
    from .policies import PolicySet
    return entry_artifact_gate(PolicySet(compliance.release_evidence(required)))


def change_control_gate(path: str, **limits):
    """Exit gate: the proposed change to `path` must respect size limits and protected paths."""
    return change_control.change_gate(path, change_control.default_set(**limits))


def tests_pass_against_implementation(ctx, out):
    r = sandbox.run_tests(ctx["implementation"]["content"], out["content"])
    if r["failed"] or not r["passed"]:
        return False, f"tests failed: {list(r['failed']) or 'no tests found'}"
    return True, f"{len(r['passed'])} tests passed"


def reviewer_approves(ctx, out):
    ok = out["content"].strip().upper().startswith("APPROVE")
    return ok, "ok" if ok else "reviewer did not approve"


def require_inputs(*node_ids):
    def gate(ctx):
        missing = [n for n in node_ids if n not in ctx]
        return (not missing, f"missing upstream outputs: {missing}" if missing else "ok")
    return gate


# ---- brownfield gates: each is bound to the real baseline it is judging the change against ----
def impact_grounded(baseline_code: str):
    from .policies import PolicySet
    return artifact_gate(PolicySet(compliance.impact_analysis_grounded(baseline_code)), "impact_analysis")


def within_declared_impact(baseline_code: str):
    from .policies import PolicySet
    return artifact_gate(PolicySet(change_control.change_within_declared_impact(baseline_code)), "implementation")


def brownfield_change_gate(path: str, baseline_code: str, max_lines: int = 40):
    return change_control.change_gate(path, change_control.brownfield_set(max_lines), baseline={path: baseline_code})


def tests_not_removed(baseline_tests: str):
    from .policies import PolicySet
    return artifact_gate(PolicySet(compliance.no_tests_removed(baseline_tests)), "tests")


def regression_reproduced(baseline_code: str, baseline_tests: str):
    from .policies import PolicySet
    return artifact_gate(PolicySet(compliance.regression_test_reproduces_bug(baseline_code, baseline_tests)), "tests")


def changelog_ticket(ticket: str):
    from .policies import PolicySet
    return artifact_gate(PolicySet(compliance.changelog_references_ticket(ticket)), "docs")

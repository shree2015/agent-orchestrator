"""Reliability metrics derived purely from the audit events (single source of truth).

Definitions:
  * retries            - failed attempts that were followed by another attempt of the SAME agent
  * fallbacks          - times a node's retries were exhausted and its fallback agent was engaged
  * rollbacks          - times a failed change set was reverted to its last good checkpoint
  * replans            - times an upstream change invalidated downstream nodes
  * node_success_rate  - nodes in state DONE at the end of the run / total nodes
  * mttr_s             - mean time from a node's first failed attempt to its next successful commit
                         (nodes that never recover are excluded, not counted as zero)
  * policy_violations  - how often each named policy ("category/policy-id") blocked an output
  * changes_rejected   - re-plans refused by change control (no reason, or re-plan budget exceeded)
  * end_to_end_latency_s - wall-clock duration of the LAST run() call
"""
from __future__ import annotations

from collections import Counter

from ..governance.policies.base import find_violation_ids


def compute(events: list[dict]) -> dict:
    by = lambda name: [e for e in events if e["event"] == name]
    finished = (by("run_finished") or [{}])[-1]
    final_states = finished.get("states", {})
    attempt_failed = by("attempt_failed")
    commits = [e for e in by("state_change") if e["to_state"] == "DONE"]

    if final_states:
        done_count = sum(1 for s in final_states.values() if s == "DONE")
        success_rate = round(done_count / len(final_states), 3)
    else:
        success_rate = None

    recoveries = []                      # MTTR: first failure -> first DONE after it
    for node in {e["node"] for e in attempt_failed}:
        first_fail = min(e["ts"] for e in attempt_failed if e["node"] == node)
        later = [e["ts"] for e in commits if e["node"] == node and e["ts"] >= first_fail]
        if later:
            recoveries.append(min(later) - first_fail)

    violations = Counter()          # once per BLOCKED OUTPUT per policy (not per message)
    for e in by("exit_gates"):
        violations.update({pid for f in e.get("failures", []) for pid in find_violation_ids(f)})
    for e in by("entry_gate"):
        if not e.get("passed"):
            violations.update(set(find_violation_ids(e.get("reason", ""))))

    return {
        "run_status": finished.get("status"),
        "run_success": finished.get("status") == "COMPLETED",
        "node_success_rate": success_rate,
        "end_to_end_latency_s": finished.get("latency_s"),
        "failed_attempts": len(attempt_failed),
        "retries": len([e for e in attempt_failed if e.get("will_retry")]),
        "fallbacks": len(by("fallback_engaged")),
        "rollbacks": len(by("rollback")),
        "replans": len(by("replan")),
        "changes_rejected": len(by("change_rejected")),
        "approvals_requested": len(by("approval")),
        "approvals_denied": len([e for e in by("approval") if not e["approved"]]),
        "policy_gate_failures": len([e for e in by("exit_gates") if e["failures"]]),
        "policy_violations": dict(sorted(violations.items())),
        "mttr_s": round(sum(recoveries) / len(recoveries), 4) if recoveries else None,
        "halt_reason": finished.get("halt_reason"),
    }

"""Planning policies: a plan must be coherent, consistent with the executable workflow, and traceable.

Subject: Mapping[str, str] of artifacts; the plan is JSON under "plan", requirements under "requirements".

  plan = {"tasks": [{"id": "T1", "title": "...", "node": "implementation", "depends_on": []}, ...],
          "traceability": [{"criterion": "AC1", "tasks": ["T1"],
                            "verified_by": {"tests": ["test_x"]}   # or {"gate": "ui"}
                           }, ...]}

Acceptance criteria are identified by position in requirements["acceptance"]: AC1, AC2, ...
FAIL CLOSED: a missing or unparseable plan is a violation, never a pass.
"""
from __future__ import annotations

import json
from typing import Mapping

from .. import codeintel as ci
from .base import Policy, PolicySet


class GraphRef:
    """Late-bound reference to the workflow graph: gates are built before the Graph object exists."""

    def __init__(self) -> None:
        self.graph = None


def _plan(subject: Mapping[str, str]):
    """Return (plan_dict, error_messages)."""
    if not (subject.get("plan") or "").strip():
        return None, ["missing or empty artifact: plan"]
    try:
        plan = json.loads(subject["plan"])
    except ValueError:
        return None, ["plan is not valid JSON"]
    if not isinstance(plan, dict) or not isinstance(plan.get("tasks"), list) or not plan["tasks"]:
        return None, ["plan needs a non-empty 'tasks' list"]
    if not all(isinstance(t, dict) and t.get("id") and t.get("node") for t in plan["tasks"]):
        return None, ["every task needs an 'id' and a workflow 'node'"]
    return plan, []


def _find_cycle(tasks: list[dict]) -> list[str]:
    deps = {t["id"]: [d for d in t.get("depends_on", []) if isinstance(d, str)] for t in tasks}
    color: dict[str, int] = {}

    def visit(n: str, path: list[str]):
        color[n] = 1
        for d in deps.get(n, []):
            if color.get(d) == 1:
                return path + [n, d]
            if d in deps and color.get(d) is None:
                found = visit(d, path + [n])
                if found:
                    return found
        color[n] = 2
        return None

    for n in deps:
        if color.get(n) is None:
            found = visit(n, [])
            if found:
                return found
    return []


def plan_well_formed() -> Policy:
    def check(subject):
        plan, errors = _plan(subject)
        if errors:
            return errors
        tasks, out = plan["tasks"], []
        ids = [t["id"] for t in tasks]
        out += [f"duplicate task id: {i}" for i in sorted({i for i in ids if ids.count(i) > 1})]
        for t in tasks:
            out += [f"task {t['id']} depends on unknown task '{d}'" for d in t.get("depends_on", []) if d not in ids]
        cycle = _find_cycle(tasks)
        return out + ([f"task dependencies form a cycle: {' -> '.join(cycle)}"] if cycle else [])
    return Policy("plan-well-formed", "compliance", "Unique ids, valid dependencies, no cycles", check)


def plan_maps_to_workflow(ref: GraphRef, required_nodes: tuple[str, ...]) -> Policy:
    def check(subject):
        plan, errors = _plan(subject)
        if errors:
            return []                                      # plan-well-formed reports structural problems
        nodes = set(ref.graph.nodes)
        used = {t["node"] for t in plan["tasks"]}
        out = [f"task {t['id']} targets unknown workflow node '{t['node']}'" for t in plan["tasks"] if t["node"] not in nodes]
        return out + [f"no task is planned for workflow stage '{n}'" for n in required_nodes if n not in used]
    return Policy("plan-maps-to-workflow", "compliance", "Every task maps to a real stage; every key stage is planned", check)


def plan_sequencing_matches_graph(ref: GraphRef) -> Policy:
    """If task B depends on task A, then A's stage must really run before B's stage in the executable graph."""
    def check(subject):
        plan, errors = _plan(subject)
        if errors:
            return []
        by_id = {t["id"]: t for t in plan["tasks"]}
        graph, out = ref.graph, []
        for t in plan["tasks"]:
            for d in t.get("depends_on", []):
                dep = by_id.get(d)
                if not dep or t["node"] not in graph.nodes or dep["node"] not in graph.nodes or dep["node"] == t["node"]:
                    continue
                if dep["node"] not in graph.ancestors(t["node"]):
                    out.append(f"plan says {t['id']} ({t['node']}) follows {d} ({dep['node']}), but the workflow does not run "
                               f"'{dep['node']}' before '{t['node']}'")
        return out
    return Policy("plan-sequencing-matches-graph", "compliance", "The plan's ordering must be executable as written", check)


def acceptance_criteria_traced() -> Policy:
    def check(subject):
        plan, errors = _plan(subject)
        if errors:
            return []
        try:
            criteria = json.loads(subject.get("requirements") or "")["acceptance"]
        except (ValueError, KeyError, TypeError):
            return ["requirements with an 'acceptance' list are needed to check traceability"]
        task_ids = {t["id"] for t in plan["tasks"]}
        traced = {e.get("criterion"): e for e in plan.get("traceability", []) if isinstance(e, dict)}
        out = []
        for i, text in enumerate(criteria, 1):
            entry = traced.get(f"AC{i}")
            if not entry:
                out.append(f"acceptance criterion AC{i} is not traced: {text[:60]}")
                continue
            if not entry.get("tasks") or any(t not in task_ids for t in entry["tasks"]):
                out.append(f"AC{i} must map to existing tasks")
            ev = entry.get("verified_by") or {}
            if not ev.get("tests") and not ev.get("gate"):
                out.append(f"AC{i} names no verification (tests or gate)")
        return out + [f"traceability mentions unknown criterion {c}" for c in traced
                      if c not in {f"AC{i}" for i in range(1, len(criteria) + 1)}]
    return Policy("acceptance-criteria-traced", "compliance", "Every acceptance criterion maps to tasks and verification", check)


def traceability_evidence_exists(ref: GraphRef) -> Policy:
    """Subject also carries the NEW tests: named tests must exist, named gates must exist and be non-empty."""
    def check(subject):
        plan, errors = _plan(subject)
        if errors:
            return []
        if not (subject.get("tests") or "").strip():
            return ["missing or empty artifact: tests"]
        try:
            have = ci.test_names(subject["tests"])
        except SyntaxError:
            return ["tests are not parseable"]
        out = []
        for e in plan.get("traceability", []):
            ev = e.get("verified_by") or {}
            out += [f"{e.get('criterion')}: promised test '{n}' does not exist" for n in ev.get("tests", []) if n not in have]
            gate = ev.get("gate")
            if gate and (gate not in ref.graph.nodes or not ref.graph.nodes[gate].exit_gates):
                out.append(f"{e.get('criterion')}: workflow node '{gate}' has no exit gates to verify it")
        return out
    return Policy("traceability-evidence-exists", "compliance", "Promised tests and gates must really exist", check)


def plan_set(ref: GraphRef, required_nodes: tuple[str, ...]) -> PolicySet:
    return PolicySet(plan_well_formed(), plan_maps_to_workflow(ref, required_nodes),
                     plan_sequencing_matches_graph(ref), acceptance_criteria_traced())


def traceability_set(ref: GraphRef) -> PolicySet:
    return PolicySet(traceability_evidence_exists(ref))

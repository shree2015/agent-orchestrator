"""Planning: decomposition must be coherent, executable as written, and traceable to acceptance criteria."""
import json

import pytest

from orchestrator import cli
from orchestrator.core.graph import Graph, Node
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals, gates as g
from orchestrator.governance.policies import change_control as cc
from orchestrator.governance.policies.planning import GraphRef, plan_set, traceability_set
from orchestrator.runner import run_workflow
from orchestrator.workflows import plan_fixtures as pf, shortener

PLAN = shortener.DEFAULTS["planner"]
REQS = shortener.DEFAULTS["requirements"]
REQUIRED = ("implementation", "tests", "docs", "ui")


def ref():
    graph, _ = shortener.build(shortener.make_llm())
    r = GraphRef()
    r.graph = graph
    return r


def check(plan_text, reqs=REQS, required=REQUIRED):
    return plan_set(ref(), required).evaluate({"plan": plan_text, "requirements": reqs})


def ids(violations):
    return {f"{v.category}/{v.policy}" for v in violations}


def edited(fn):
    plan = json.loads(PLAN)
    fn(plan)
    return json.dumps(plan)


# ---- the reference plan ----------------------------------------------------------
def test_reference_plan_passes_every_planning_policy():
    assert check(PLAN) == []


# ---- well-formed -----------------------------------------------------------------
def test_unparseable_empty_or_malformed_plans_fail_closed():
    assert check("not json")
    assert check("")
    assert check(json.dumps({"tasks": []}))
    assert check(json.dumps({"tasks": [{"id": "T1"}]}))                         # no node


def test_duplicate_ids_and_unknown_dependencies_are_named():
    dup = edited(lambda p: p["tasks"].append({"id": "T1", "title": "x", "node": "docs", "depends_on": []}))
    assert any("duplicate task id: T1" in v.message for v in check(dup))
    unknown = edited(lambda p: p["tasks"][1].update(depends_on=["T99"]))
    assert any("unknown task 'T99'" in v.message for v in check(unknown))


def test_cycles_are_detected_and_the_path_is_reported():
    out = check(pf.plan_cycle(PLAN))
    assert any("cycle" in v.message and "->" in v.message for v in out)
    assert "compliance/plan-well-formed" in ids(out)


# ---- maps to the real workflow ---------------------------------------------------
def test_tasks_must_target_real_stages_and_every_key_stage_must_be_planned():
    ghost = edited(lambda p: p["tasks"][0].update(node="deployment"))
    assert any("unknown workflow node 'deployment'" in v.message for v in check(ghost))
    no_docs = edited(lambda p: p.update(tasks=[t for t in p["tasks"] if t["node"] != "docs"]))
    assert any("no task is planned for workflow stage 'docs'" in v.message for v in check(no_docs))


# ---- sequencing matches the executable graph --------------------------------------
def test_a_plan_that_orders_stages_differently_from_the_workflow_is_rejected():
    out = check(pf.plan_wrong_order(PLAN))
    assert ids(out) == {"compliance/plan-sequencing-matches-graph"}
    assert "'tests' before 'docs'" in out[0].message


def test_dependencies_inside_one_stage_and_along_real_edges_are_fine():
    assert check(PLAN) == []            # T2 -> T1 (same stage), T4 -> T3 (implementation -> ui) are both present


# ---- acceptance-criterion traceability -------------------------------------------
def test_an_untraced_acceptance_criterion_is_named():
    out = check(pf.plan_misses_criterion(PLAN))
    assert ids(out) == {"compliance/acceptance-criteria-traced"} and "AC3" in out[0].message


def test_traceability_needs_real_tasks_real_verification_and_known_criteria():
    bad_task = edited(lambda p: p["traceability"][0].update(tasks=["T42"]))
    assert any("AC1 must map to existing tasks" in v.message for v in check(bad_task))
    no_proof = edited(lambda p: p["traceability"][1].update(verified_by={}))
    assert any("AC2 names no verification" in v.message for v in check(no_proof))
    extra = edited(lambda p: p["traceability"].append({"criterion": "AC9", "tasks": ["T1"], "verified_by": {"gate": "ui"}}))
    assert any("unknown criterion AC9" in v.message for v in check(extra))


def test_traceability_cannot_be_checked_without_requirements():
    assert any("requirements" in v.message for v in check(PLAN, reqs=""))


# ---- evidence exists --------------------------------------------------------------
def _evidence(plan_text, tests_text):
    return traceability_set(ref()).evaluate({"plan": plan_text, "tests": tests_text})


def test_every_promised_test_must_exist():
    tests = shortener.read("tests.py.txt")
    assert _evidence(PLAN, tests) == []
    renamed = tests.replace("def test_clicks_are_counted_by_day_and_referrer", "def test_clicks_are_counted")
    out = _evidence(PLAN, renamed)
    assert len(out) == 1 and "test_clicks_are_counted_by_day_and_referrer" in out[0].message


def test_a_promised_gate_must_exist_and_actually_gate_something():
    tests = shortener.read("tests.py.txt")
    nonexistent = edited(lambda p: p["traceability"][5]["verified_by"].update(gate="deployment"))
    assert any("deployment" in v.message for v in _evidence(nonexistent, tests))
    graph = Graph().add(Node("ui", "x"))                       # a node with NO exit gates verifies nothing
    r = GraphRef()
    r.graph = graph
    assert any("no exit gates" in v.message for v in traceability_set(r).evaluate({"plan": PLAN, "tests": tests}))
    assert _evidence(PLAN, "")                                  # fails closed without tests


# ---- inside the real workflow ------------------------------------------------------
def go(tmp_path, llm):
    graph, agents = shortener.build(llm)
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="pl")


def test_the_greenfield_workflow_has_a_planning_stage_that_design_depends_on(tmp_path):
    graph, _ = shortener.build(shortener.make_llm())
    assert graph.nodes["plan"].depends_on == ("requirements",) and "plan" in graph.nodes["design"].depends_on
    assert cc.lint_graph(graph) == []
    r = go(tmp_path, shortener.make_llm())
    assert r["status"] is RunStatus.COMPLETED and (r["run_dir"] / "artifacts" / "plan.json").exists()


@pytest.mark.parametrize("violation,expected", [
    ("plan-cycle", {"compliance/plan-well-formed", "compliance/plan-sequencing-matches-graph"}),
    ("plan-misses-criterion", {"compliance/acceptance-criteria-traced"}),
    ("plan-wrong-order", {"compliance/plan-sequencing-matches-graph"}),
])
def test_a_flawed_plan_is_blocked_by_the_named_policy_then_the_planner_recovers(tmp_path, violation, expected):
    role, bad, _ = shortener.VIOLATIONS[violation]
    r = go(tmp_path, shortener.make_llm(scripted={role: [bad, shortener.DEFAULTS[role]]}))
    assert r["status"] is RunStatus.COMPLETED and set(r["metrics"]["policy_violations"]) == expected
    assert r["metrics"]["retries"] == 1


def test_a_plan_that_never_improves_stops_the_run_before_any_code_is_written(tmp_path):
    r = go(tmp_path, shortener.make_llm(scripted={"planner": [pf.plan_cycle(PLAN)]}))
    assert r["status"] is RunStatus.HALTED
    assert "implementation" not in r["engine"].context and r["engine"].state["design"] is NodeState.SKIPPED


def test_a_promised_test_that_disappears_blocks_the_tests_stage(tmp_path):
    tests = shortener.DEFAULTS.get("tester") or shortener.read("tests.py.txt")
    dropped = tests.replace("def test_concurrent_shortening_is_safe_and_codes_are_unique", "def test_concurrent_shortening")
    r = go(tmp_path, shortener.make_llm(scripted={"tester": [dropped, tests]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"compliance/traceability-evidence-exists": 1}


def test_release_needs_the_plan_as_evidence():
    gate = g.release_compliance_for(("requirements", "plan"))
    ok, why = gate({"requirements": {"content": "x"}})
    assert not ok and "artifact: plan" in why


def test_cli_plan_violations(tmp_path):
    base = ["run", "shortener", "--approve", "auto", "--run-root", str(tmp_path)]
    for name in ("plan-cycle", "plan-misses-criterion", "plan-wrong-order"):
        assert cli.main(base + ["--violate", name]) == 0

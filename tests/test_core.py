import threading
import time

import pytest

from orchestrator.core.engine import Engine
from orchestrator.core.graph import Graph, GraphError, Node
from orchestrator.core.state import NodeState, RunStatus


def diamond() -> Graph:
    g = Graph()
    g.add(Node("req", "a"))
    g.add(Node("impl", "a", depends_on=("req",)))
    g.add(Node("tests", "a", depends_on=("impl",)))
    g.add(Node("docs", "a", depends_on=("impl",)))
    g.add(Node("release", "a", depends_on=("tests", "docs"), requires_approval=True))
    return g


def run(graph, agent, approver=lambda n, o: True, **kw):
    events = []
    eng = Engine(graph, {"a": agent}, approver=approver, audit=events.append, **kw)
    return eng, eng.run(), events


# ---- graph ----------------------------------------------------------------
def test_cycle_detected():
    g = Graph().add(Node("a", "x", depends_on=("b",))).add(Node("b", "x", depends_on=("a",)))
    with pytest.raises(GraphError, match="cycle"):
        g.validate()


def test_unknown_dependency_rejected():
    with pytest.raises(GraphError, match="unknown"):
        Graph().add(Node("a", "x", depends_on=("ghost",))).validate()


def test_parallel_layers():
    assert diamond().parallel_layers() == [["req"], ["impl"], ["docs", "tests"], ["release"]]


# ---- engine ---------------------------------------------------------------
def test_happy_path_completes():
    eng, status, _ = run(diamond(), lambda ctx: "ok")
    assert status is RunStatus.COMPLETED
    assert set(eng.context) == set(diamond().nodes)


def test_parallel_nodes_overlap_and_join_waits():
    active, peak = 0, 0
    lock = threading.Lock()

    def agent(ctx):
        nonlocal active, peak
        with lock:
            active += 1
            peak = max(peak, active)
        time.sleep(0.05)
        with lock:
            active -= 1
        return "ok"

    eng, status, _ = run(diamond(), agent)
    assert status is RunStatus.COMPLETED
    assert peak == 2  # tests and docs ran concurrently


def test_retry_then_success():
    calls = {"n": 0}

    def flaky(ctx):
        calls["n"] += 1
        if calls["n"] < 3:
            raise RuntimeError("boom")
        return "ok"

    g = Graph().add(Node("x", "a", max_retries=2))
    eng, status, _ = run(g, flaky)
    assert status is RunStatus.COMPLETED and eng.attempts["x"] == 3


def test_retries_are_bounded_and_trigger_safe_stop():
    def always_fail(ctx):
        raise RuntimeError("nope")

    eng, status, _ = run(diamond(), always_fail)
    assert status is RunStatus.HALTED
    assert eng.state["req"] is NodeState.FAILED
    assert eng.state["impl"] is NodeState.SKIPPED
    assert eng.attempts["req"] == 3  # 1 + 2 retries, no more


def test_exit_gate_failure_blocks_commit():
    g = Graph().add(Node("x", "a", max_retries=0, exit_gates=(lambda ctx, out: (False, "bad output"),)))
    eng, status, _ = run(g, lambda ctx: "ok")
    assert status is RunStatus.HALTED and "x" not in eng.context


def test_entry_gate_blocks_agent():
    called = []
    g = Graph().add(Node("x", "a", entry_gates=(lambda ctx: (False, "missing input"),)))
    eng, status, _ = run(g, lambda ctx: called.append(1))
    assert status is RunStatus.HALTED and not called


def test_human_rejection_halts_and_skips_downstream():
    eng, status, _ = run(diamond(), lambda ctx: "ok", approver=lambda n, o: False)
    assert status is RunStatus.HALTED
    assert eng.state["release"] is NodeState.REJECTED


def test_default_approver_denies():
    eng = Engine(Graph().add(Node("x", "a", requires_approval=True)), {"a": lambda c: 1})
    assert eng.run() is RunStatus.HALTED


def test_attempt_budget_safe_stop():
    g = Graph().add(Node("x", "a", max_retries=100))
    eng, status, _ = run(g, lambda ctx: 1 / 0, max_total_attempts=5)
    assert status is RunStatus.HALTED and "budget" in eng.halt_reason


def test_replan_reruns_only_affected_nodes():
    runs = []
    eng, _, _ = run(diamond(), lambda ctx: runs.append(1) or "ok")
    before = len(runs)
    eng.state["req"] = NodeState.DONE
    affected = eng.invalidate("impl", "design changed")
    assert affected == {"impl", "tests", "docs", "release"}
    assert eng.run() is RunStatus.COMPLETED
    assert len(runs) - before == 4  # req not re-run


def test_audit_log_records_key_events():
    _, _, events = run(diamond(), lambda ctx: "ok")
    kinds = {e["event"] for e in events}
    assert {"run_started", "wave_started", "state_change", "approval", "run_finished"} <= kinds

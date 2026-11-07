"""Fallback, backoff, checkpoint/rollback and re-planning behaviour of the engine."""
from orchestrator.core.engine import Engine
from orchestrator.core.graph import Graph, Node
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.observability import metrics


def chain() -> Graph:
    return (Graph().add(Node("req", "a"))
            .add(Node("impl", "a", depends_on=("req",), max_retries=1, fallback_agent="fb"))
            .add(Node("tests", "a", depends_on=("impl",))))


class Versioned:
    """Agent whose output carries a version, so tests can tell old from new."""
    def __init__(self, name):
        self.name, self.calls, self.fail_after = name, 0, None   # fail_after: calls allowed before failing

    def __call__(self, ctx):
        self.calls += 1
        if self.fail_after is not None and self.calls > self.fail_after:
            raise RuntimeError(f"{self.name} broke")
        return f"{self.name}-v{self.calls}"


def make(graph=None, approver=lambda n, o: True, **kw):
    events = []
    a, fb = Versioned("a"), Versioned("fb")
    eng = Engine(graph or chain(), {"a": a, "fb": fb}, approver=approver, audit=events.append, **kw)
    return eng, a, fb, events


# ---- fallback --------------------------------------------------------------
def test_fallback_used_after_retries_exhausted():
    g = Graph().add(Node("x", "a", max_retries=1, fallback_agent="fb"))
    eng, a, fb, events = make(g)
    a.fail_after = 0                                    # primary always fails
    assert eng.run() is RunStatus.COMPLETED
    assert eng.producer["x"] == "fb" and eng.attempts["x"] == 3      # 2 primary + 1 fallback
    assert [e["event"] for e in events if e["event"] == "fallback_engaged"] == ["fallback_engaged"]
    m = metrics.compute(events)
    assert m["fallbacks"] == 1 and m["retries"] == 1 and m["failed_attempts"] == 2


def test_fallback_not_used_when_primary_succeeds():
    eng, a, fb, events = make()
    assert eng.run() is RunStatus.COMPLETED
    assert fb.calls == 0 and eng.producer["impl"] == "a"


def test_fallback_failure_halts_safely():
    g = Graph().add(Node("x", "a", max_retries=0, fallback_agent="fb")).add(Node("y", "a", depends_on=("x",)))
    eng, a, fb, _ = make(g)
    a.fail_after = fb.fail_after = 0
    assert eng.run() is RunStatus.HALTED
    assert eng.state["y"] is NodeState.SKIPPED and "x" not in eng.context
    assert "fallback exhausted" in eng.halt_reason


def test_fallback_output_must_pass_the_same_exit_gates():
    gate = lambda ctx, out: (not out.startswith("fb"), "fallback output rejected by gate")
    g = Graph().add(Node("x", "a", max_retries=0, fallback_agent="fb", exit_gates=(gate,)))
    eng, a, fb, _ = make(g)
    a.fail_after = 0
    assert eng.run() is RunStatus.HALTED and "x" not in eng.context


def test_missing_fallback_agent_is_rejected_at_construction():
    g = Graph().add(Node("x", "a", fallback_agent="nope"))
    try:
        Engine(g, {"a": lambda c: 1})
    except ValueError as e:
        assert "nope" in str(e)
    else:
        raise AssertionError("expected ValueError")


# ---- backoff ---------------------------------------------------------------
def test_exponential_backoff_between_retries_only():
    g = Graph().add(Node("x", "a", max_retries=3))
    delays = []
    eng, a, _, events = make(g, backoff_base_s=0.1, sleep=delays.append)
    a.fail_after = 0
    assert eng.run() is RunStatus.HALTED
    assert delays == [0.1, 0.2, 0.4]                    # 4 attempts -> 3 waits, none after the last


def test_backoff_is_capped():
    g = Graph().add(Node("x", "a", max_retries=4))
    delays = []
    eng, a, _, _ = make(g, backoff_base_s=1.0, backoff_max_s=3.0, sleep=delays.append)
    a.fail_after = 0
    eng.run()
    assert delays == [1.0, 2.0, 3.0, 3.0]


# ---- checkpoint / rollback -------------------------------------------------
def test_failed_replan_rolls_back_to_last_good_outputs():
    eng, a, fb, events = make()
    assert eng.run() is RunStatus.COMPLETED
    good = dict(eng.context)
    eng.invalidate("impl", "spec changed")
    a.fail_after = a.calls                              # every further primary call fails
    fb.fail_after = fb.calls
    assert eng.run() is RunStatus.HALTED
    assert eng.context["impl"] == good["impl"] and eng.context["tests"] == good["tests"]
    assert eng.state["impl"] is NodeState.ROLLED_BACK and eng.state["req"] is NodeState.DONE
    assert eng.producer["impl"] == "a"
    m = metrics.compute(events)
    assert m["rollbacks"] == 1 and m["replans"] == 1 and m["run_status"] == "HALTED"


def test_rollback_is_all_or_nothing_across_the_change_set():
    """req re-runs fine (new version) but impl then fails: req must be reverted too."""
    eng, a, fb, _ = make()
    eng.run()
    old_req = eng.context["req"]
    eng.invalidate("req", "requirements changed")
    # req will succeed again (new version), then impl and its fallback fail
    calls_after_req = a.calls + 1
    a.fail_after = calls_after_req
    fb.fail_after = fb.calls
    assert eng.run() is RunStatus.HALTED
    assert eng.context["req"] == old_req                # reverted, not the new req-v..
    assert eng.state["req"] is NodeState.ROLLED_BACK


def test_successful_replan_commits_and_does_not_roll_back():
    eng, a, _, events = make()
    eng.run()
    eng.invalidate("impl", "spec changed")
    assert eng.run() is RunStatus.COMPLETED
    kinds = [e["event"] for e in events]
    assert "change_committed" in kinds and "rollback" not in kinds
    assert eng._checkpoint is None


def test_human_rejection_of_revised_output_rolls_back():
    g = (Graph().add(Node("req", "a"))
         .add(Node("design", "a", depends_on=("req",), requires_approval=True))
         .add(Node("impl", "a", depends_on=("design",))))
    answers = iter([True, False])                       # approve first design, reject the revision
    eng, a, _, _ = make(g, approver=lambda n, o: next(answers))
    assert eng.run() is RunStatus.COMPLETED
    old_design = eng.context["design"]
    eng.invalidate("design", "revise")
    assert eng.run() is RunStatus.HALTED
    assert eng.context["design"] == old_design and eng.state["design"] is NodeState.ROLLED_BACK


def test_first_run_failure_has_nothing_to_roll_back_and_commits_nothing():
    g = Graph().add(Node("x", "a", max_retries=0))
    eng, a, _, events = make(g)
    a.fail_after = 0
    assert eng.run() is RunStatus.HALTED
    assert eng.context == {} and metrics.compute(events)["rollbacks"] == 0


def test_mttr_is_never_negative_after_rollback():
    eng, a, fb, events = make()
    eng.run()
    eng.invalidate("impl", "x")
    a.fail_after = a.calls
    fb.fail_after = fb.calls
    eng.run()
    m = metrics.compute(events)
    assert m["mttr_s"] is None or m["mttr_s"] >= 0


def test_metrics_node_success_rate_reflects_final_states():
    eng, a, fb, events = make()
    eng.run()
    assert metrics.compute(events)["node_success_rate"] == 1.0
    eng.invalidate("impl", "x")
    a.fail_after = a.calls
    fb.fail_after = fb.calls
    eng.run()
    assert metrics.compute(events)["node_success_rate"] < 1.0

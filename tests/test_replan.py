"""Automatic re-planning: fingerprints, stale detection, revise(), and its governance."""
from orchestrator.core.engine import Engine, fingerprint_of
from orchestrator.core.graph import Graph, Node
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.observability import metrics


class Counter:
    """Agent that records how often it ran and derives its output from what it read."""
    def __init__(self, name, reads=()):
        self.name, self.reads, self.calls = name, reads, 0

    def __call__(self, ctx):
        self.calls += 1
        seen = "|".join(str(ctx[r]["content"]) for r in self.reads)
        return {"role": self.name, "content": f"{self.name}({seen})", "derived_from": list(self.reads)}


def diamond(**kw):
    """req -> design -> (impl, docs) -> review   plus an unrelated branch: audit"""
    graph = (Graph().add(Node("req", "req"))
             .add(Node("design", "design", depends_on=("req",)))
             .add(Node("impl", "impl", depends_on=("design",)))
             .add(Node("docs", "docs", depends_on=("design",)))
             .add(Node("review", "review", depends_on=("impl", "docs")))
             .add(Node("audit", "audit")))
    agents = {"req": Counter("req"), "design": Counter("design", ("req",)), "impl": Counter("impl", ("design",)),
              "docs": Counter("docs", ("design",)), "review": Counter("review", ("impl", "docs")), "audit": Counter("audit")}
    events = []
    eng = Engine(graph, agents, audit=events.append, **kw)
    assert eng.run() is RunStatus.COMPLETED
    return eng, agents, events


def test_fingerprint_depends_on_content_not_on_metadata():
    assert fingerprint_of({"content": "x", "role": "a"}) == fingerprint_of({"content": "x", "role": "b", "derived_from": ["q"]})
    assert fingerprint_of({"content": "x"}) != fingerprint_of({"content": "y"})
    assert fingerprint_of("plain") == fingerprint_of("plain")


def test_each_node_records_the_fingerprints_of_all_its_ancestors():
    eng, _, _ = diamond()
    assert set(eng._consumed["review"]) == {"req", "design", "impl", "docs"}
    assert eng._consumed["audit"] == {} and eng.stale_nodes() == {}


def test_revising_an_upstream_output_redoes_exactly_the_affected_nodes():
    eng, agents, events = diamond()
    result = eng.revise("design", "NEW DESIGN", "stakeholder changed scope")
    assert result.accepted and result.changed and result.affected == {"impl", "docs", "review"}
    assert eng.run() is RunStatus.COMPLETED
    calls = {n: a.calls for n, a in agents.items()}
    assert calls == {"req": 1, "design": 1, "impl": 2, "docs": 2, "review": 2, "audit": 1}   # req, design (edited) and audit untouched
    assert "NEW DESIGN" in eng.context["impl"]["content"] and "NEW DESIGN" in eng.context["review"]["content"]


def test_an_identical_revision_triggers_nothing():
    eng, agents, events = diamond()
    same = eng.context["design"]["content"]
    result = eng.revise("design", same, "reconfirming")
    assert result.accepted and not result.changed and not result.affected
    assert eng.run() is RunStatus.COMPLETED and sum(a.calls for a in agents.values()) == 6
    m = metrics.compute(events)
    assert m["revision_noops"] == 1 and m["replans"] == 0 and m["revisions"] == 0


def test_the_edited_node_records_who_changed_it_for_decision_lineage():
    eng, _, events = diamond()
    eng.revise("req", "AMENDED", "client call", by="product-owner")
    eng.run()
    assert eng.producer["req"] == "product-owner"
    assert "revision by product-owner" in eng.context["req"]["derived_from"]
    rev = [e for e in events if e["event"] == "revision"][0]
    assert rev["by"] == "product-owner" and rev["old"] != rev["new"]
    replan = [e for e in events if e["event"] == "replan"][0]
    assert replan["auto"] is True and "output of 'req' changed" in replan["why"]


def test_metrics_report_the_rework_a_change_cost():
    eng, _, events = diamond()
    eng.revise("design", "v2", "change")
    eng.run()
    m = metrics.compute(events)
    assert m["revisions"] == 1 and m["auto_replans"] == 1 and m["nodes_replanned"] == 3 and m["run_status"] == "COMPLETED"


def test_a_revision_must_pass_the_nodes_exit_gates():
    gate = lambda ctx, out: ("FORBIDDEN" not in out["content"], "content contains a forbidden word")
    graph = Graph().add(Node("req", "req", exit_gates=(gate,))).add(Node("design", "design", depends_on=("req",)))
    agents = {"req": Counter("req"), "design": Counter("design", ("req",))}
    events = []
    eng = Engine(graph, agents, audit=events.append)
    eng.run()
    before = dict(eng.context)
    result = eng.revise("req", "FORBIDDEN text", "try to sneak it in")
    assert not result.accepted and "forbidden word" in result.why
    assert eng.context == before and eng.state["design"] is NodeState.DONE           # nothing touched
    assert metrics.compute(events)["revisions_rejected"] == 1


def test_a_revision_needs_a_reason_and_respects_the_replan_budget():
    eng, _, events = diamond(max_replans=1)
    assert not eng.revise("design", "x", "  ").accepted
    assert eng.revise("design", "v2", "first").accepted
    eng.run()
    second = eng.revise("design", "v3", "second")
    assert not second.accepted and "budget" in second.why
    assert metrics.compute(events)["changes_rejected"] == 2


def test_only_committed_nodes_can_be_revised_and_unknown_nodes_raise():
    eng, _, _ = diamond()
    eng.invalidate("design", "force")
    assert not eng.revise("impl", "x", "why").accepted        # impl has no committed output right now
    try:
        eng.revise("ghost", "x", "why")
    except KeyError:
        pass
    else:
        raise AssertionError("expected KeyError")


def test_a_failed_re_run_rolls_back_the_revision_itself():
    eng, agents, events = diamond()
    originals = {n: eng.context[n] for n in ("design", "impl", "docs", "review")}
    agents["impl"].calls = 0

    def broken(ctx):
        raise RuntimeError("cannot implement the new design")
    eng.agents["impl"] = broken
    result = eng.revise("design", "IMPOSSIBLE DESIGN", "ambitious change")
    assert result.changed and eng.run() is RunStatus.HALTED
    for n, out in originals.items():                           # the human's change is undone too: all or nothing
        assert eng.context[n] == out and eng.state[n] is NodeState.ROLLED_BACK
    assert eng.producer["design"] == "design" and eng.fingerprint["design"] == fingerprint_of(originals["design"])
    assert metrics.compute(events)["rollbacks"] == 1 and eng.state["req"] is NodeState.DONE


def test_after_a_rollback_the_system_is_consistent_and_can_take_a_new_revision():
    eng, agents, _ = diamond()
    eng.agents["impl"] = lambda ctx: (_ for _ in ()).throw(RuntimeError("boom"))
    eng.revise("design", "BAD", "first try")
    eng.run()                                                   # rolled back
    eng.agents["impl"] = Counter("impl", ("design",))
    assert eng.stale_nodes() == {}
    result = eng.revise("design", "GOOD", "second try")
    assert result.changed and eng.run() is RunStatus.COMPLETED
    assert "GOOD" in eng.context["review"]["content"]


def test_agent_revisions_of_approval_nodes_need_a_human_but_human_edits_do_not():
    asked = []
    graph = Graph().add(Node("req", "req")).add(Node("design", "design", depends_on=("req",), requires_approval=True))
    agents = {"req": Counter("req"), "design": Counter("design", ("req",))}
    eng = Engine(graph, agents, approver=lambda n, o: asked.append(n.id) or False)
    eng.state["design"] = NodeState.PENDING
    eng.approver = lambda n, o: True
    eng.run()
    asked.clear()
    eng.approver = lambda n, o: asked.append(n.id) or False
    assert not eng.revise("design", "agent edit", "automated tweak", by="agent").accepted and asked == ["design"]
    assert eng.revise("design", "human edit", "I decided", by="human").accepted and asked == ["design"]    # no second prompt


def test_manual_invalidate_still_works_and_stale_safety_net_catches_tampering():
    eng, agents, _ = diamond()
    eng.context["design"] = {"role": "x", "content": "TAMPERED", "derived_from": []}
    eng.fingerprint["design"] = fingerprint_of(eng.context["design"])        # someone edited state behind the engine's back
    assert set(eng.stale_nodes()) == {"impl", "docs", "review"}
    assert eng.run() is RunStatus.COMPLETED                                  # run() re-plans on its own
    assert agents["impl"].calls == 2 and "TAMPERED" in eng.context["impl"]["content"]

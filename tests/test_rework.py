"""Review-driven rework: a reviewer's rejection is a verdict on earlier work, so it is sent back, not retried.

Found on the second live run: Sonnet's first draft really did have a defect (non-ASCII URLs crash the redirect) and the reviewer said
so, correctly. The pipeline then halted. A real model's first draft will often have some defect, so a pipeline that stops at the first
rejection can never converge. The findings now go back to the stages that produced the work, within a bound, with a checkpoint.
"""
import pytest

from orchestrator.core.engine import Engine
from orchestrator.core.graph import Graph, GraphError, Node, Rework
from orchestrator.governance import approvals, gates as g
from orchestrator.runner import run_workflow
from orchestrator.workflows import shortener
from tests.test_review import Recorder


def build(reviewer_rule, **engine):
    """design -> impl -> tests -> review (-> sends back to impl). The reviewer rejects until the code says v2."""
    seen = {"impl": [], "tests": [], "design": [], "review": []}

    def agent(name, make):
        def run(ctx):
            seen[name].append(ctx.get("__feedback__"))
            return {"role": name, "content": make(ctx), "derived_from": []}
        return run

    def reviewer(ctx):
        seen["review"].append(ctx.get("__feedback__"))
        return {"role": "review", "content": reviewer_rule(ctx["impl"]["content"]), "derived_from": []}

    state = {"impl_runs": 0}

    def impl(ctx):
        seen["impl"].append(ctx.get("__feedback__"))
        state["impl_runs"] += 1
        return {"role": "impl", "content": "code v2" if ctx.get("__feedback__") else "code v1", "derived_from": []}

    graph = (Graph()
             .add(Node("design", "design"))
             .add(Node("impl", "impl", depends_on=("design",)))
             .add(Node("tests", "tests", depends_on=("impl",)))
             .add(Node("review", "review", depends_on=("tests",), exit_gates=(g.reviewer_approves,), max_retries=0,
                       rework=g.review_rework("impl"))))
    agents = {"design": agent("design", lambda c: "design"), "impl": impl, "tests": agent("tests", lambda c: "tests for " + c["impl"]["content"]),
              "review": reviewer}
    events = []
    return Engine(graph, agents, audit=events.append, **engine), seen, events


def verdict_needs_v2(code):
    return "APPROVE" if "v2" in code else "REJECT - the code must say v2"


def test_a_rejection_sends_the_findings_back_and_the_work_is_redone_until_approved():
    engine, seen, events = build(verdict_needs_v2)
    assert engine.run().value == "COMPLETED"
    rework = [e for e in events if e["event"] == "rework"]
    assert len(rework) == 1 and rework[0]["granted"] and rework[0]["round"] == 1 and rework[0]["of"] == 2
    assert set(rework[0]["redo"]) == {"impl", "tests", "review"} and "the code must say v2" in rework[0]["findings"]
    assert engine.context["impl"]["content"] == "code v2" and engine.context["tests"]["content"] == "tests for code v2"
    assert engine.attempts["impl"] == 2 and engine.attempts["tests"] == 2 and engine.attempts["design"] == 1       # upstream work is NOT redone
    feedback = seen["impl"][1]
    assert "A reviewer rejected the finished work" in feedback["content"] and "the code must say v2" in feedback["content"]
    assert feedback["previous"] == "code v1"                                                                       # the agent gets its own previous output
    assert "the code must say v2" in seen["tests"][1]["content"] and seen["tests"][1]["previous"] == "tests for code v1"
    assert seen["design"] == [None] and engine.rejected == {}                                                      # a committed node leaves no rejection behind
    assert [e["event"] for e in events if e["event"] in ("rework", "replan", "change_committed")] == ["rework", "replan", "change_committed"]


def test_the_rework_is_bounded_then_the_run_halts_and_rolls_back_whole():
    engine, seen, events = build(lambda code: "REJECT - never good enough", max_reworks=2)
    assert engine.run().value == "HALTED"
    granted = [e["granted"] for e in events if e["event"] == "rework"]
    assert granted == [True, True, False]                                                                          # two rounds, then refused
    assert "rework budget exhausted (2)" in [e for e in events if e["event"] == "rework"][-1]["why"]
    assert "never good enough" in engine.halt_reason and "never good enough" in engine.rejected["review"]
    assert engine.context["impl"]["content"] == "code v1"                                                          # rolled back to the first draft, whole
    assert any(e["event"] == "rollback" for e in events)
    assert engine.attempts["impl"] == 3                                                                            # first draft + exactly two reworks


def test_zero_reworks_means_halt_at_once_as_before():
    engine, _, events = build(lambda code: "REJECT - no", max_reworks=0)
    assert engine.run().value == "HALTED"
    assert [(e["granted"], e["why"]) for e in events if e["event"] == "rework"] == [(False, "rework budget exhausted (0)")]
    assert not any(e["event"] == "replan" for e in events) and engine.attempts["impl"] == 1


def test_a_reply_that_is_not_a_verdict_is_retried_not_sent_back():
    engine, _, events = build(lambda code: "hmm, maybe, not sure")
    assert engine.run().value == "HALTED"
    assert not [e for e in events if e["event"] == "rework"] and engine.attempts["impl"] == 1


def test_a_rework_target_must_be_upstream_of_the_reviewing_node():
    graph = Graph().add(Node("a", "a")).add(Node("b", "b", depends_on=("a",), rework=Rework("c"))).add(Node("c", "c", depends_on=("b",)))
    with pytest.raises(GraphError, match="not upstream"):
        graph.validate()
    ok = Graph().add(Node("a", "a")).add(Node("b", "b", depends_on=("a",), rework=Rework("a")))
    ok.validate()


# ---------------------------------------------------------------------------------------------- the workflows
def test_the_greenfield_workflow_recovers_from_a_real_finding(tmp_path):
    finding = "REJECT\n- shortener.py: a non-ASCII URL crashes the redirect"
    rec = Recorder(shortener.make_llm(scripted={"reviewer": [finding, finding, "APPROVE\n- checked"]}))
    graph, agents = shortener.build(rec)
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="loop")
    assert res["status"].value == "COMPLETED" and res["metrics"]["reworks"] == 1 and res["metrics"]["reworks_refused"] == 0
    prompts = {}
    for role, prompt in rec.prompts:
        prompts.setdefault(role, []).append(prompt)
    for role in ("developer", "tester", "doc_writer", "frontend"):
        assert len(prompts[role]) == 2 and "A reviewer rejected the finished work" in prompts[role][1] and "non-ASCII URL crashes" in prompts[role][1], role
        assert "A reviewer rejected" not in prompts[role][0]
    assert len(prompts["architect"]) == 1 and len(prompts["planner"]) == 1 and len(prompts["requirements"]) == 1     # earlier stages untouched
    assert len(prompts["reviewer"]) == 3
    assert not list((tmp_path / "loop" / "artifacts").glob("REJECTED-*")) and (tmp_path / "loop" / "artifacts" / "review.md").exists()
    report = (tmp_path / "loop" / "report.md").read_text(encoding="utf-8")
    assert "| reworks | 1 |" in report and "`rework`" in report


def test_the_activity_feed_describes_a_rework_in_plain_words():
    from orchestrator.admin.session import describe
    granted = describe({"event": "rework", "granted": True, "round": 1, "of": 2, "redo": ["impl", "tests"], "findings": "REJECT - x"})
    assert granted == "Review sent the work back (round 1 of 2): impl, tests will be redone. Findings: REJECT - x"
    refused = describe({"event": "rework", "granted": False, "round": 2, "of": 2, "findings": "REJECT - y", "why": "budget"})
    assert refused == "Review rejected the work and the rework budget is spent (2): REJECT - y"


def test_the_command_line_records_and_applies_the_rework_budget(tmp_path):
    import json
    from orchestrator import cli
    assert cli.main(["run", "shortener", "--approve", "auto", "--max-reworks", "0", "--run-root", str(tmp_path)]) == 0
    run = sorted(p for p in tmp_path.iterdir() if p.is_dir())[-1]
    first = json.loads((run / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["event"] == "run_config" and first["config"]["max_reworks"] == 0

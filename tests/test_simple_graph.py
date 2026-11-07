import pytest

from simple_graph.demo import run_scenario
from simple_graph.sdlc_graph import ENTRY, EDGES, ROUTES, END


def events(final):
    return [(e["node"], e["event"]) for e in final["audit_trail"]]


def test_happy_path_visits_every_stage_once():
    f, llm = run_scenario("happy")
    assert f["status"] == "DONE" and f["retries"] == 0
    assert [n for n, _ in events(f)] == ["analyst", "architect", "human_approval",
                                         "developer", "qa_tester", "release"]


def test_failing_tests_loop_back_to_developer_then_pass():
    f, llm = run_scenario("fix_loop")
    assert f["status"] == "DONE" and f["retries"] == 1 and llm.calls["developer"] == 2
    assert ("qa_tester", "tests FAILED") in events(f)


def test_security_violation_also_loops_back():
    f, llm = run_scenario("security_loop")
    assert f["status"] == "DONE" and llm.calls["developer"] == 2
    assert "SECURITY" in next(e["detail"] for e in f["audit_trail"] if e["event"] == "tests FAILED")


def test_retry_loop_is_bounded():
    f, llm = run_scenario("always_bad")
    assert f["status"] == "HALTED" and llm.calls["developer"] == 4      # 1 try + 3 retries
    assert "tests still failing" in f["halt_reason"]


def test_rejected_design_loops_back_to_architect():
    f, llm = run_scenario("reject_once")
    assert f["status"] == "DONE" and llm.calls["architect"] == 2 and f["design_revisions"] == 1


def test_design_rejection_loop_is_bounded():
    f, llm = run_scenario("reject_always")
    assert f["status"] == "HALTED" and llm.calls["architect"] == 3
    assert "developer" not in [n for n, _ in events(f)]                # code never written


def test_audit_trail_is_append_only_across_steps():
    f, _ = run_scenario("fix_loop")
    assert len(f["audit_trail"]) == 8                                  # nothing overwritten


def test_every_route_label_points_at_a_real_node():
    nodes = set(EDGES) | set(ROUTES) | {ENTRY}
    for _, (_, mapping) in ROUTES.items():
        assert set(mapping.values()) <= nodes
    assert set(EDGES.values()) <= nodes | {END}


def test_langgraph_matches_plain_runner():
    pytest.importorskip("langgraph")
    for name in ("happy", "fix_loop", "reject_once", "always_bad"):
        a, _ = run_scenario(name, "plain")
        b, _ = run_scenario(name, "langgraph")
        assert events(a) == events(b) and a["status"] == b["status"]

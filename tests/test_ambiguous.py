"""Ambiguous scenario: 'Make links expire sensibly.' -> structured questions -> human decisions -> governed change."""
import json

import pytest

from orchestrator import cli
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals, codeintel as ci, sandbox
from orchestrator.governance.policies import PolicySet, change_control as cc, compliance as comp
from orchestrator.governance.policies.planning import GraphRef, plan_set
from orchestrator.runner import run_workflow
from orchestrator.workflows import ambiguous as am

ANSWERS = {"default_ttl_days": "90", "max_ttl_days": "365", "ttl_applies_to_aliases": "no"}
DECIDED = {"default_ttl_days": 90, "max_ttl_days": 365, "ttl_applies_to_aliases": "no"}
DOWNSTREAM = {"design", "implementation", "tests", "docs", "review", "release"}


def go(tmp_path, answers=ANSWERS, llm=None, **kw):
    llm = llm or am.make_llm()
    graph, agents = am.build(llm, asker=approvals.dict_asker(answers) if answers is not None else None)
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="am", **kw), llm


def decisions_of(result):
    return {d["id"]: d for d in json.loads(result["engine"].context["clarifications"]["content"])["decisions"]}


# ---- the scenario's own integrity ------------------------------------------------
def test_the_request_really_is_vague_and_the_questions_are_structured():
    assert len(am.read("request.md").split("> ")[1].split("\n")[0].split()) <= 5
    questions = json.loads(am.DEFAULTS["requirements"])["ambiguities"]
    assert {q["risk"] for q in questions} == {"high", "medium"}
    for q in questions:
        assert q["proposed"] in q["options"] and q["why"] and q["question"].endswith("?")


def test_the_impact_analysis_is_grounded_and_the_plan_is_executable_and_traceable():
    assert comp.impact_analysis_grounded(am.BASELINE_CODE).evaluate({"impact_analysis": am.DEFAULTS["analyst"]}) == []
    graph, _ = am.build(am.make_llm())
    ref = GraphRef()
    ref.graph = graph
    assert plan_set(ref, ("implementation", "tests", "docs")).evaluate(
        {"plan": am.DEFAULTS["planner"], "requirements": am.DEFAULTS["requirements"]}) == []
    assert cc.lint_graph(graph) == []


@pytest.mark.parametrize("answers", [
    {"default_ttl_days": 30, "max_ttl_days": 90, "ttl_applies_to_aliases": "yes"},
    {"default_ttl_days": 90, "max_ttl_days": 365, "ttl_applies_to_aliases": "no"},
    {"default_ttl_days": 180, "max_ttl_days": 365, "ttl_applies_to_aliases": "yes"},
    {"default_ttl_days": 90, "max_ttl_days": 90, "ttl_applies_to_aliases": "no"},
])
def test_for_any_valid_decisions_the_rendered_change_is_correct_and_the_tests_prove_the_new_behaviour(answers):
    code, tests = am.render_code(answers), am.render_tests(answers)
    ok = sandbox.run_tests(code, tests, timeout=60)
    assert not ok["failed"]                                                       # new code passes old + new tests
    on_baseline = sandbox.run_tests(am.BASELINE_CODE, tests, timeout=60)
    new_tests = ci.test_names(tests) - ci.test_names(am.BASELINE_TESTS)
    assert len(new_tests) == 7 and set(on_baseline["failed"]) >= new_tests - {"test_service_without_a_default_ttl_still_creates_permanent_links"}


def test_the_change_touches_exactly_the_declared_symbols_and_keeps_the_public_api():
    code = am.render_code(DECIDED)
    assert set(ci.changed_symbols(am.BASELINE_CODE, code)) == {"<module>", "ShortenerService.__init__", "ShortenerService.shorten", "serve"}
    assert cc.ChangeSet({"shortener.py": am.BASELINE_CODE}, {"shortener.py": code}).lines_changed <= am.MAX_LINES_CHANGED
    cs = cc.ChangeSet({"shortener.py": am.BASELINE_CODE}, {"shortener.py": code})
    assert PolicySet(cc.public_api_stable()).evaluate(cs) == []


# ---- who may answer ---------------------------------------------------------------
def test_with_nobody_to_answer_the_high_risk_question_stops_the_run_and_names_it(tmp_path):
    r, _ = go(tmp_path, answers=None)
    assert r["status"] is RunStatus.HALTED
    assert "default_ttl_days" in r["metrics"]["halt_reason"] and "needs a human decision" in r["metrics"]["halt_reason"]
    assert r["metrics"]["policy_violations"] == {"compliance/high-risk-needs-human": 1}
    assert r["engine"].attempts["clarifications"] == 1                       # a human is not re-asked by retry loops
    assert "design" not in r["engine"].context and r["engine"].state["design"] is NodeState.SKIPPED


def test_nothing_from_a_later_wave_starts_once_the_question_has_blocked(tmp_path):
    r, _ = go(tmp_path, answers=None)
    eng = r["engine"]
    assert eng.state["plan"] is NodeState.SKIPPED and eng.state["design"] is NodeState.SKIPPED
    assert not {"plan", "design", "implementation", "tests", "docs", "release"} & set(eng.context)
    # (the sibling analysis branch shares the blocked wave: whether it had already started is a thread race, so it is not asserted)


def test_low_and_medium_risk_questions_are_assumed_with_a_recorded_reason(tmp_path):
    r, _ = go(tmp_path, answers={"default_ttl_days": "90"})
    assert r["status"] is RunStatus.COMPLETED
    d = decisions_of(r)
    assert d["default_ttl_days"]["decided_by"] == "human" and d["max_ttl_days"]["decided_by"] == "assumed"
    assert d["max_ttl_days"]["answer"] == 365 and d["max_ttl_days"]["rationale"].startswith("proposed default accepted")
    log = (r["run_dir"] / "artifacts" / "docs.md").read_text(encoding="utf-8")
    assert "`default_ttl_days` = 90 (human)" in log and "`max_ttl_days` = 365 (assumed)" in log          # the changelog says who decided


def test_contradictory_answers_are_stopped_at_the_decision_stage(tmp_path):
    r, _ = go(tmp_path, answers={"default_ttl_days": "180", "max_ttl_days": "90"})
    assert r["status"] is RunStatus.HALTED and "compliance/decisions-consistent" in r["metrics"]["policy_violations"]


def test_an_answer_outside_the_options_is_rejected(tmp_path):
    r, _ = go(tmp_path, answers={"default_ttl_days": "45"})
    assert r["status"] is RunStatus.HALTED and "compliance/answers-within-options" in r["metrics"]["policy_violations"]


# ---- answers drive the artifacts ---------------------------------------------------
def test_a_clean_run_builds_everything_from_the_decisions(tmp_path):
    r, _ = go(tmp_path)
    assert r["status"] is RunStatus.COMPLETED and r["metrics"]["policy_violations"] == {}
    art = r["run_dir"] / "artifacts"
    assert "DEFAULT_TTL_S = 90 * 24 * 3600" in (art / "implementation.py").read_text(encoding="utf-8")
    assert "90 days" in (art / "design.md").read_text(encoding="utf-8")
    log = (art / "docs.md").read_text(encoding="utf-8")
    assert "FEAT-207" in log and "default_ttl_days" in log and "(human)" in log and "(assumed)" not in log
    assert (art / "clarifications.json").exists() and (art / "plan.json").exists()


def test_different_answers_produce_different_artifacts(tmp_path):
    a, _ = go(tmp_path / "a", answers={**ANSWERS, "default_ttl_days": "30"})
    b, _ = go(tmp_path / "b", answers={**ANSWERS, "default_ttl_days": "180", "ttl_applies_to_aliases": "yes"})
    code_a = a["engine"].context["implementation"]["content"]
    code_b = b["engine"].context["implementation"]["content"]
    assert "DEFAULT_TTL_S = 30 *" in code_a and "DEFAULT_TTL_S = 180 *" in code_b
    assert "APPLIES_TO_ALIASES = False" in code_a and "APPLIES_TO_ALIASES = True" in code_b


def test_questions_and_analysis_run_in_the_same_wave(tmp_path):
    r, _ = go(tmp_path)
    waves = [e["nodes"] for e in r["audit"].events if e["event"] == "wave_started"]
    assert waves[0] == ["requirements"] and set(waves[1]) == {"clarifications", "impact_analysis"}


# ---- changing an answer later: automatic re-planning -----------------------------
def edit(value, decision="default_ttl_days"):
    return [("clarifications", am.answer_edit(decision, value), f"human changed {decision} to {value}")]


def test_changing_an_answer_redoes_only_the_work_that_depended_on_it(tmp_path):
    r, _ = go(tmp_path, edits=edit("180"))
    assert r["status"] is RunStatus.COMPLETED
    rev = r["revisions"][0]
    assert rev.accepted and rev.changed and set(rev.affected) == DOWNSTREAM
    attempts = r["engine"].attempts
    assert all(attempts[n] == 1 for n in ("requirements", "clarifications", "impact_analysis", "plan"))   # untouched
    assert all(attempts[n] == 2 for n in DOWNSTREAM)                                                      # redone once
    assert "DEFAULT_TTL_S = 180 *" in (r["run_dir"] / "artifacts" / "implementation.py").read_text(encoding="utf-8")
    assert r["engine"].producer["clarifications"] == "human"
    m = r["metrics"]
    assert (m["revisions"], m["auto_replans"], m["nodes_replanned"], m["rollbacks"]) == (1, 1, 6, 0)


def test_the_revision_is_recorded_for_decision_lineage(tmp_path):
    r, _ = go(tmp_path, edits=edit("180"))
    events = r["audit"].events
    rev = next(e for e in events if e["event"] == "revision")
    replan = next(e for e in events if e["event"] == "replan" and e.get("auto"))
    assert rev["node"] == "clarifications" and rev["by"] == "human" and "default_ttl_days to 180" in rev["reason"]
    assert replan["trigger"] == "clarifications" and set(replan["invalidated"]) == DOWNSTREAM and "changed" in replan["why"]
    assert "revision by human" in r["engine"].context["clarifications"]["derived_from"]


def test_restating_the_same_answer_does_nothing(tmp_path):
    r, _ = go(tmp_path, edits=edit("90"))
    assert not r["revisions"][0].changed and r["metrics"]["replans"] == 0 and r["metrics"]["revision_noops"] == 1
    assert all(n == 1 for n in r["engine"].attempts.values())


def test_a_revision_that_contradicts_another_answer_is_rejected_and_nothing_changes(tmp_path):
    r, _ = go(tmp_path, answers={**ANSWERS, "default_ttl_days": "30", "max_ttl_days": "90"}, edits=edit("180"))
    rev = r["revisions"][0]
    assert not rev.accepted and "decisions-consistent" in rev.why
    assert r["status"] is RunStatus.COMPLETED and decisions_of(r)["default_ttl_days"]["answer"] == 30
    assert "DEFAULT_TTL_S = 30 *" in r["engine"].context["implementation"]["content"]


def test_an_agent_cannot_overwrite_a_human_decision_with_an_assumption(tmp_path):
    graph, agents = am.build(am.make_llm(), asker=approvals.dict_asker(ANSWERS))
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="ag")
    body = json.loads(r["engine"].context["clarifications"]["content"])
    for d in body["decisions"]:
        if d["id"] == "default_ttl_days":
            d.update(answer=30, decided_by="assumed", rationale="agent thinks so")
    result = r["engine"].revise("clarifications", json.dumps(body, indent=2), "agent optimisation", by="agent")
    assert not result.accepted and "high-risk" in result.why
    assert decisions_of(r)["default_ttl_days"]["answer"] == 90


def test_a_revision_the_system_cannot_realise_is_rolled_back_including_the_answer(tmp_path):
    llm = am.make_llm()

    def break_developers():
        llm.inject_failures("developer", 99)
        llm.inject_failures("developer_fallback", 99)

    r, _ = go(tmp_path, llm=llm, edits=edit("180"), on_replan=break_developers)
    assert r["status"] is RunStatus.HALTED and r["metrics"]["rollbacks"] == 1
    assert decisions_of(r)["default_ttl_days"]["answer"] == 90                       # the human's old answer is back
    art = r["run_dir"] / "artifacts"
    assert "DEFAULT_TTL_S = 90 *" in (art / "implementation.py").read_text(encoding="utf-8")
    assert r["engine"].state["requirements"] is NodeState.DONE and r["engine"].state["implementation"] is NodeState.ROLLED_BACK


def test_a_second_change_after_a_rollback_works(tmp_path):
    llm = am.make_llm()
    graph, agents = am.build(llm, asker=approvals.dict_asker(ANSWERS))
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="rb2",
                     edits=edit("180"), on_replan=lambda: (llm.inject_failures("developer", 99), llm.inject_failures("developer_fallback", 99)))
    assert r["status"] is RunStatus.HALTED
    llm._pending_failures.clear()
    eng = r["engine"]
    result = eng.revise("clarifications", am.answer_edit("default_ttl_days", "30")(eng), "try 30 instead")
    assert result.changed and eng.run() is RunStatus.COMPLETED
    assert "DEFAULT_TTL_S = 30 *" in eng.context["implementation"]["content"]


# ---- agents that misbehave ----------------------------------------------------------
@pytest.mark.parametrize("violation,expected", [
    ("ignores-decision", {"compliance/implementation-honours-decisions": 1}),
    ("scope-creep", {"change_control/change-within-declared-impact": 1}),
    ("untraced-tests", {"compliance/traceability-evidence-exists": 1}),
    ("changelog-no-ticket", {"compliance/changelog-references-ticket": 1}),
    ("plan-misses-criterion", {"compliance/acceptance-criteria-traced": 1}),
    ("plan-wrong-order", {"compliance/plan-sequencing-matches-graph": 1}),
])
def test_each_violation_is_blocked_by_exactly_the_named_policy_then_recovers(tmp_path, violation, expected):
    role, bad, _ = am.VIOLATIONS[violation]
    llm = am.make_llm(scripted={role: [bad, am.DEFAULTS[role]]})
    r, _ = go(tmp_path, llm=llm)
    assert r["status"] is RunStatus.COMPLETED and r["metrics"]["policy_violations"] == expected


def test_a_developer_that_keeps_ignoring_the_decision_is_replaced_by_the_fallback(tmp_path):
    llm = am.make_llm(scripted={"developer": [am.VIOLATIONS["ignores-decision"][1]]})
    r, _ = go(tmp_path, llm=llm)
    assert r["status"] is RunStatus.COMPLETED and r["engine"].producer["implementation"] == "developer_fallback"


# ---- command line -------------------------------------------------------------------
def test_cli_covers_every_branch_of_the_scenario(tmp_path):
    base = ["run", "ambiguous", "--approve", "auto", "--run-root", str(tmp_path)]
    assert cli.main(base) == 1                                                                           # nobody answered
    assert cli.main(base + ["--answer", "default_ttl_days=90"]) == 0
    assert cli.main(base + ["--answer", "default_ttl_days=90", "--revise-answer", "default_ttl_days=180"]) == 0
    assert cli.main(base + ["--answer", "default_ttl_days=90", "--revise-answer", "default_ttl_days=90"]) == 0
    assert cli.main(base + ["--answer", "default_ttl_days=30", "--answer", "max_ttl_days=90",
                            "--revise-answer", "default_ttl_days=180"]) == 3                             # change refused
    assert cli.main(base + ["--answer", "default_ttl_days=90", "--revise-answer", "default_ttl_days=180", "--break-on-revise"]) == 1
    for name in am.VIOLATIONS:
        assert cli.main(base + ["--answer", "default_ttl_days=90", "--violate", name]) == 0


def test_cli_rejects_answers_for_workflows_that_ask_nothing_and_malformed_pairs(tmp_path):
    with pytest.raises(SystemExit):
        cli.main(["run", "shortener", "--approve", "auto", "--answer", "x=1", "--run-root", str(tmp_path)])
    with pytest.raises(SystemExit):
        cli.main(["run", "ambiguous", "--approve", "auto", "--answer", "no-equals-sign", "--run-root", str(tmp_path)])


# ---- the output folder is a complete, runnable deliverable ---------------------------
def _free_port():
    import socket
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def _latest_run(root):
    return sorted(p for p in root.iterdir() if p.is_dir())[-1]


def test_a_cli_run_carries_the_existing_web_ui_next_to_the_changed_code(tmp_path):
    from orchestrator.workflows import shortener
    assert cli.main(["run", "ambiguous", "--approve", "auto", "--answer", "default_ttl_days=90", "--run-root", str(tmp_path)]) == 0
    art = _latest_run(tmp_path) / "artifacts"
    assert (art / "ui.html").read_text(encoding="utf-8") == shortener.GOOD_UI
    assert "ui.html" in (_latest_run(tmp_path) / "report.md").read_text(encoding="utf-8")


@pytest.mark.parametrize("default_days,aliases,alias_days", [(90, "no", None), (30, "yes", 30)])
def test_the_generated_service_serves_its_ui_and_applies_the_decisions_over_real_http(tmp_path, default_days, aliases, alias_days):
    import subprocess, sys, time, urllib.request
    from datetime import datetime
    assert cli.main(["run", "ambiguous", "--approve", "auto", "--answer", f"default_ttl_days={default_days}",
                     "--answer", f"ttl_applies_to_aliases={aliases}", "--run-root", str(tmp_path / "runs")]) == 0
    art = _latest_run(tmp_path / "runs") / "artifacts"
    work = tmp_path / "work"
    work.mkdir()
    port = _free_port()
    proc = subprocess.Popen([sys.executable, str(art / "implementation.py"), str(port)], cwd=work,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)

    def call(path, body=None):
        req = urllib.request.Request(f"http://127.0.0.1:{port}{path}", json.dumps(body).encode() if body else None)
        with urllib.request.urlopen(req, timeout=3) as resp:
            return resp.status, resp.read().decode("utf-8")

    def seconds(link):
        parse = lambda iso: datetime.strptime(iso, "%Y-%m-%dT%H:%M:%SZ")
        return None if link["expires_at"] is None else (parse(link["expires_at"]) - parse(link["created_at"])).total_seconds()

    try:
        for _ in range(50):
            try:
                status, page = call("/")
                break
            except OSError:
                time.sleep(0.1)
        else:
            raise AssertionError("server did not start")
        assert status == 200 and "<title>Shortener" in page
        plain = json.loads(call("/api/shorten", {"url": "https://example.com/default-expiry"})[1])
        assert seconds(plain) == default_days * 86400                                       # the human's decision, live
        alias = json.loads(call("/api/shorten", {"url": "https://example.com/promo", "alias": "promo-1"})[1])
        assert seconds(alias) == (alias_days * 86400 if alias_days else None)              # the alias decision, live
        explicit = json.loads(call("/api/shorten", {"url": "https://example.com/short", "ttl_seconds": 3600})[1])
        assert seconds(explicit) == 3600                                                    # an explicit ttl still wins
    finally:
        proc.terminate()
        proc.wait(timeout=10)

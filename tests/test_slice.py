import json

from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals, policy_engine
from orchestrator.llm.mock_client import BAD_CODE_SECRET, GOOD_CODE, MockLLM
from orchestrator.observability.audit_log import AuditLog
from orchestrator.runner import run_workflow
from orchestrator.workflows import slugify


def go(tmp_path, llm=None, approver=approvals.auto_approver):
    graph, agents = slugify.build(llm or MockLLM())
    return run_workflow(graph, agents, approver, run_root=tmp_path, run_id="t")


def test_happy_path_and_artifacts(tmp_path):
    r = go(tmp_path)
    assert r["status"] is RunStatus.COMPLETED
    assert (r["run_dir"] / "artifacts" / "implementation.py").exists()
    assert r["metrics"]["retries"] == 0 and r["metrics"]["node_success_rate"] == 1.0


def test_llm_failures_retry_then_recover(tmp_path):
    r = go(tmp_path, MockLLM(fail_times={"developer": 2}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["retries"] == 2 and r["metrics"]["mttr_s"] is not None


def test_primary_exhausted_then_fallback_recovers(tmp_path):
    r = go(tmp_path, MockLLM(fail_times={"developer": 5}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["engine"].producer["implementation"] == "developer_fallback"
    assert r["metrics"]["fallbacks"] == 1 and r["metrics"]["retries"] == 2


def test_primary_and_fallback_both_fail_safe_stop(tmp_path):
    r = go(tmp_path, MockLLM(fail_times={"developer": 5, "developer_fallback": 5}))
    assert r["status"] is RunStatus.HALTED
    assert r["engine"].state["tests"] is NodeState.SKIPPED
    assert r["engine"].attempts["implementation"] == 4      # 3 primary + 1 fallback, no more
    assert "fallback exhausted" in r["metrics"]["halt_reason"]


def test_secret_blocked_by_policy_then_clean_retry(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"developer": [BAD_CODE_SECRET, GOOD_CODE]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_gate_failures"] == 1
    assert "API_KEY" not in (r["run_dir"] / "artifacts" / "implementation.py").read_text()


def test_persistent_policy_violation_halts_without_committing(tmp_path):
    # BOTH the primary and the fallback leak a secret: the policy gate must hold.
    r = go(tmp_path, MockLLM(scripted={"developer": [BAD_CODE_SECRET], "developer_fallback": [BAD_CODE_SECRET]}))
    assert r["status"] is RunStatus.HALTED
    assert "implementation" not in r["engine"].context


def test_fallback_output_is_still_policy_checked_and_can_save_the_run(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"developer": [BAD_CODE_SECRET]}))   # fallback is clean
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_gate_failures"] == 3 and r["metrics"]["fallbacks"] == 1
    assert "API_KEY" not in (r["run_dir"] / "artifacts" / "implementation.py").read_text()


def test_revised_design_replans_and_reruns_downstream(tmp_path):
    graph, agents = slugify.build(MockLLM())
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="rv",
                     revisions=[("design", "design revised", None)])
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["replans"] == 1 and r["metrics"]["rollbacks"] == 0
    assert r["engine"].attempts["requirements"] == 1          # upstream NOT re-run
    assert r["engine"].attempts["implementation"] == 2


def test_failed_revision_is_rolled_back_and_artifacts_restored(tmp_path):
    llm = MockLLM()
    graph, agents = slugify.build(llm)

    def break_developers():
        llm.inject_failures("developer", 99)
        llm.inject_failures("developer_fallback", 99)

    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="rb",
                     revisions=[("design", "design revised", break_developers)])
    assert r["status"] is RunStatus.HALTED
    assert r["metrics"]["rollbacks"] == 1
    eng = r["engine"]
    assert eng.state["implementation"] is NodeState.ROLLED_BACK
    assert eng.state["requirements"] is NodeState.DONE         # untouched upstream
    assert (r["run_dir"] / "artifacts" / "implementation.py").read_text() == GOOD_CODE
    assert AuditLog.verify(r["run_dir"] / "audit.jsonl")


def test_bad_generated_tests_are_caught_by_sandbox_gate(tmp_path):
    wrong = "def test_wrong():\n    assert slugify('A B') == 'a_b'\n"
    r = go(tmp_path, MockLLM(scripted={"tester": [wrong]}))
    assert r["status"] is RunStatus.HALTED and "tests failed" in r["metrics"]["halt_reason"]


def test_human_rejection_at_design(tmp_path):
    r = go(tmp_path, approver=approvals.deny_approver("design"))
    assert r["status"] is RunStatus.HALTED
    assert r["engine"].state["design"] is NodeState.REJECTED
    assert r["engine"].state["implementation"] is NodeState.SKIPPED


def test_release_requires_human_approval(tmp_path):
    r = go(tmp_path, approver=approvals.deny_approver("release"))
    assert r["engine"].state["release"] is NodeState.REJECTED
    assert r["metrics"]["approvals_denied"] == 1


def test_audit_chain_verifies_and_detects_tampering(tmp_path):
    r = go(tmp_path)
    path = r["run_dir"] / "audit.jsonl"
    assert AuditLog.verify(path)
    lines = path.read_text().splitlines()
    rec = json.loads(lines[3]); rec["event"] = "tampered"; lines[3] = json.dumps(rec)
    path.write_text("\n".join(lines) + "\n")
    assert not AuditLog.verify(path)


def test_lineage_recorded(tmp_path):
    r = go(tmp_path)
    assert r["engine"].context["implementation"]["derived_from"] == ["requirements", "design"]


def test_policy_engine_rules():
    assert policy_engine.check_security(GOOD_CODE) == []
    assert policy_engine.check_security("eval('1')")
    assert policy_engine.check_security("import os\nos.system('ls')")
    assert policy_engine.check_security(BAD_CODE_SECRET)


def test_cli_fallback_and_rollback_demos(tmp_path):
    from orchestrator import cli
    root = str(tmp_path)
    assert cli.main(["run", "slugify", "--approve", "auto", "--fail", "developer:5", "--run-root", root]) == 0
    assert cli.main(["run", "slugify", "--approve", "auto", "--revise-design", "ok", "--run-root", root]) == 0
    assert cli.main(["run", "slugify", "--approve", "auto", "--revise-design", "fail", "--run-root", root]) == 1
    assert cli.main(["run", "slugify", "--approve", "auto", "--revise-design", "reject", "--run-root", root]) == 1

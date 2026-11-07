"""Security, compliance and change-control policies, the workflow linter, and re-plan limits."""
from orchestrator.core.engine import Engine
from orchestrator.core.graph import Graph, Node
from orchestrator.core.state import RunStatus
from orchestrator.governance import approvals, gates as g, policy_engine
from orchestrator.governance.policies import (GovernanceError, Policy, PolicySet, categories_of,
                                              find_violation_ids)
from orchestrator.governance.policies import change_control as cc, compliance as comp, security as sec
from orchestrator.llm.mock_client import (BAD_CODE_IMPORT, BAD_CODE_SECRET, DOCS, GOOD_CODE, TESTS,
                                          THIN_TESTS, UNDOCUMENTED_CODE, MockLLM)
from orchestrator.observability import metrics
from orchestrator.runner import run_workflow
from orchestrator.workflows import slugify

import pytest


def ids(violations):
    return {f"{v.category}/{v.policy}" for v in violations}


# ---- framework -------------------------------------------------------------
def test_violation_renders_with_category_and_policy_id():
    v = sec.default_set().evaluate(BAD_CODE_SECRET)[0]
    assert str(v).startswith("[security/no-hardcoded-secrets]")
    assert find_violation_ids(str(v)) == ["security/no-hardcoded-secrets"]


def test_policy_set_rejects_duplicate_ids_and_unknown_category():
    p = Policy("x", "security", "d", lambda s: [])
    with pytest.raises(ValueError, match="duplicate"):
        PolicySet(p, p)
    with pytest.raises(ValueError, match="category"):
        Policy("y", "vibes", "d", lambda s: [])


# ---- security --------------------------------------------------------------
def test_clean_code_passes_all_security_policies():
    assert sec.default_set().evaluate(GOOD_CODE) == []


def test_unapproved_third_party_import_is_blocked_but_stdlib_is_fine():
    assert ids(sec.default_set().evaluate(BAD_CODE_IMPORT)) == {"security/dependency-allowlist"}
    assert sec.default_set().evaluate("import json, re, sqlite3\nfrom collections import deque\n") == []


def test_allowlist_permits_approved_dependency():
    assert sec.default_set(frozenset({"requests"})).evaluate(BAD_CODE_IMPORT) == []
    assert sec.dependency_allowlist(frozenset({"fastapi"})).evaluate("from fastapi import FastAPI\n") == []


def test_relative_imports_are_not_treated_as_third_party():
    assert sec.dependency_allowlist().evaluate("from . import sibling\n") == []


def test_unsafe_imports_and_dangerous_calls_blocked():
    assert "security/no-unsafe-imports" in ids(sec.default_set().evaluate("import pickle\n"))
    assert "security/no-dangerous-calls" in ids(sec.default_set().evaluate("eval('1')\n"))
    assert "security/no-dangerous-calls" in ids(sec.default_set().evaluate("import os\nos.system('ls')\n"))


def test_unparseable_code_does_not_crash_policies_and_legacy_api_reports_syntax_error():
    assert sec.default_set().evaluate("def broken(:\n") == []        # valid_python gate owns syntax errors
    assert any("syntax error" in m for m in policy_engine.check_security("def broken(:\n"))


# ---- compliance ------------------------------------------------------------
def test_compliant_artifacts_pass():
    arts = {"implementation": GOOD_CODE, "tests": TESTS, "docs": DOCS}
    assert comp.code_set().evaluate(arts) == [] and comp.tests_set().evaluate(arts) == []
    assert comp.docs_set().evaluate(arts) == []


def test_undocumented_public_function_is_flagged_private_is_not():
    assert ids(comp.code_set().evaluate({"implementation": UNDOCUMENTED_CODE})) == {"compliance/public-api-documented"}
    assert comp.code_set().evaluate({"implementation": "def _helper():\n    return 1\n"}) == []


def test_minimum_tests_counts_only_test_functions():
    assert ids(comp.tests_set().evaluate({"tests": THIN_TESTS})) == {"compliance/minimum-tests"}
    helpers_only = "def helper():\n    pass\n" * 5
    assert comp.tests_set().evaluate({"tests": helpers_only})


def test_docs_must_mention_every_public_function():
    arts = {"implementation": GOOD_CODE, "docs": "# nothing relevant here"}
    assert ids(comp.docs_set().evaluate(arts)) == {"compliance/docs-cover-public-api"}


def test_compliance_fails_closed_when_artifacts_are_missing():
    assert comp.code_set().evaluate({})
    assert comp.tests_set().evaluate({})
    assert comp.docs_set().evaluate({"implementation": GOOD_CODE})            # docs missing
    assert comp.docs_set().evaluate({"implementation": GOOD_CODE, "docs": "   "})   # docs blank


def test_release_evidence_requires_everything_and_an_approving_review():
    full = {k: "x" for k in comp.RELEASE_EVIDENCE} | {"review": "APPROVE: fine"}
    assert comp.release_set().evaluate(full) == []
    assert comp.release_set().evaluate({**full, "review": "REJECT: no"})
    assert comp.release_set().evaluate({k: v for k, v in full.items() if k != "tests"})


def test_release_entry_gate_blocks_without_evidence():
    ok, why = g.release_compliance({})
    assert not ok and "release-evidence" in why


# ---- change control: change sets ------------------------------------------
def test_change_set_classification_and_line_count():
    cs = cc.ChangeSet({"a.py": "1\n2\n3", "b.py": "x", "gone.py": "y\nz"}, {"a.py": "1\n2\nCHANGED", "b.py": "x", "new.py": "n1\nn2"})
    assert cs.added == {"new.py"} and cs.deleted == {"gone.py"} and cs.modified == {"a.py"}
    assert cs.lines_changed == 2 + 2 + 2     # a.py: -3 +CHANGED ; new.py: +2 ; gone.py: -2


def test_file_count_and_line_limits():
    many = cc.ChangeSet({}, {f"f{i}.py": "x" for i in range(5)})
    assert ids(PolicySet(cc.max_files_changed(3)).evaluate(many)) == {"change_control/max-files-changed"}
    big = cc.ChangeSet({}, {"a.py": "\n".join("l" for _ in range(300))})
    assert ids(PolicySet(cc.max_lines_changed(200)).evaluate(big)) == {"change_control/max-lines-changed"}


def test_protected_paths_including_nested_and_deletions():
    pset = PolicySet(cc.protected_paths())
    assert pset.evaluate(cc.ChangeSet({}, {".env": "K=1"}))
    assert pset.evaluate(cc.ChangeSet({}, {"deploy/server.pem": "x"}))
    assert pset.evaluate(cc.ChangeSet({".github/ci.yml": "x"}, {}))          # deleting a protected file
    assert pset.evaluate(cc.ChangeSet({}, {"src/app.py": "x"})) == []


def test_deletions_need_a_human():
    assert ids(PolicySet(cc.no_file_deletion()).evaluate(cc.ChangeSet({"a.py": "x"}, {}))) == {"change_control/no-file-deletion"}


def test_change_gate_blocks_writes_to_protected_path_and_allows_normal_file():
    ok, _ = cc.change_gate("slugify.py")({}, {"content": GOOD_CODE})
    assert ok
    ok, why = cc.change_gate(".env")({}, {"content": "KEY=1"})
    assert not ok and "protected-paths" in why


# ---- change control: workflow linter ---------------------------------------
def test_slugify_workflow_passes_lint():
    graph, _ = slugify.build(MockLLM())
    assert cc.lint_graph(graph) == []


def test_lint_flags_high_impact_node_without_approval():
    graph = Graph().add(Node("deploy", "a", tags=("high_impact",)))
    assert ids(cc.lint_graph(graph)) == {"change_control/high-impact-requires-approval"}


def test_lint_flags_code_node_without_security_gate_and_accepts_one_with():
    assert ids(cc.lint_graph(Graph().add(Node("impl", "a", tags=("code",))))) == {"change_control/code-requires-security-gate"}
    assert cc.lint_graph(Graph().add(Node("impl", "a", tags=("code",), exit_gates=(g.security_policy,)))) == []


def test_lint_flags_release_without_compliance_gate():
    graph = Graph().add(Node("rel", "a", tags=("release",), requires_approval=True))
    assert ids(cc.lint_graph(graph)) == {"change_control/release-requires-compliance-gate"}


def test_a_non_policy_gate_does_not_satisfy_the_security_requirement():
    assert categories_of(g.valid_python) == frozenset()
    graph = Graph().add(Node("impl", "a", tags=("code",), exit_gates=(g.valid_python,)))
    assert cc.lint_graph(graph)


def test_runner_refuses_to_run_a_workflow_that_drops_a_mandatory_control(tmp_path):
    graph, agents = slugify.build(MockLLM())
    stripped = Graph()
    for n in graph.nodes.values():
        if n.id == "implementation":      # silently remove all gates from the code node
            n = Node(n.id, n.agent, n.depends_on, (), (), n.requires_approval, n.max_retries, tags=n.tags)
        stripped.add(n)
    with pytest.raises(GovernanceError, match="code-requires-security-gate"):
        run_workflow(stripped, agents, approvals.auto_approver, run_root=tmp_path, run_id="lint")
    assert not (tmp_path / "lint" / "artifacts").exists()     # nothing ran
    assert "policy_lint" in (tmp_path / "lint" / "audit.jsonl").read_text()   # but the refusal is audited


# ---- change control: re-plan limits ----------------------------------------
def _engine(**kw):
    events = []
    g_ = Graph().add(Node("a", "x")).add(Node("b", "x", depends_on=("a",)))
    eng = Engine(g_, {"x": lambda c: "ok"}, audit=events.append, **kw)
    eng.run()
    return eng, events


def test_replan_requires_a_reason():
    eng, events = _engine()
    assert eng.invalidate("a", "   ") == set()
    assert eng.context and eng.run() is RunStatus.COMPLETED          # untouched, still the last good state
    assert metrics.compute(events)["changes_rejected"] == 1


def test_replan_budget_is_enforced():
    eng, events = _engine(max_replans=1)
    assert eng.invalidate("a", "first change")
    assert eng.run() is RunStatus.COMPLETED
    assert eng.invalidate("a", "second change") == set()
    m = metrics.compute(events)
    assert m["replans"] == 1 and m["changes_rejected"] == 1


def test_replan_of_unknown_node_is_an_error():
    eng, _ = _engine()
    with pytest.raises(KeyError):
        eng.invalidate("ghost", "why")


# ---- end to end: each policy fires inside the real workflow ----------------
def go(tmp_path, llm):
    graph, agents = slugify.build(llm)
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="p")


def test_unapproved_dependency_is_blocked_then_clean_retry_succeeds(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"developer": [BAD_CODE_IMPORT, GOOD_CODE]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"security/dependency-allowlist": 1}


def test_undocumented_code_is_blocked_by_compliance_then_retry_succeeds(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"developer": [UNDOCUMENTED_CODE, GOOD_CODE]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"compliance/public-api-documented": 1}


def test_too_few_tests_halts_the_run_and_blocks_release(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"tester": [THIN_TESTS]}))
    assert r["status"] is RunStatus.HALTED
    assert r["engine"].state["release"].value == "SKIPPED"
    assert "compliance/minimum-tests" in r["metrics"]["policy_violations"]


def test_empty_docs_halts_the_run(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"doc_writer": [""]}))
    assert r["status"] is RunStatus.HALTED
    assert "compliance/docs-cover-public-api" in r["metrics"]["policy_violations"]


def test_metrics_break_violations_down_by_policy(tmp_path):
    r = go(tmp_path, MockLLM(scripted={"developer": [BAD_CODE_SECRET, BAD_CODE_IMPORT, GOOD_CODE]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"security/dependency-allowlist": 1, "security/no-hardcoded-secrets": 1}


def test_cli_policy_demos(tmp_path):
    from orchestrator import cli
    root = str(tmp_path)
    base = ["run", "slugify", "--approve", "auto", "--run-root", root]
    assert cli.main(base + ["--violate", "import"]) == 0
    assert cli.main(base + ["--violate", "undocumented", "--violate", "secret"]) == 0
    assert cli.main(base + ["--violate", "thin-tests"]) == 1
    assert cli.main(base + ["--violate", "empty-docs"]) == 1
    assert cli.main(base + ["--revise-design", "ok", "--max-replans", "0"]) == 0

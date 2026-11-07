"""Brownfield scenario: fixing BUG-142 in the existing shortener under governance."""
import json

import pytest

from orchestrator import cli
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals, codeintel as ci, sandbox
from orchestrator.governance.policies import PolicySet, change_control as cc, compliance as comp
from orchestrator.runner import run_workflow
from orchestrator.workflows import brownfield as bf


def ids(violations):
    return {f"{v.category}/{v.policy}" for v in violations}


def go(tmp_path, llm=None, **kw):
    graph, agents = bf.build(llm or bf.make_llm())
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="bf", **kw)


# ---- the scenario itself must be internally consistent --------------------------
def test_baseline_passes_its_own_tests_so_the_bug_is_invisible_to_the_existing_suite():
    r = sandbox.run_tests(bf.BASELINE_CODE, bf.BASELINE_TESTS, timeout=60)
    assert not r["failed"] and len(r["passed"]) >= 25


def test_the_regression_tests_fail_on_the_baseline_and_pass_on_the_fix():
    new_names = ci.test_names(bf.FIXED_TESTS) - ci.test_names(bf.BASELINE_TESTS)
    assert len(new_names) == 3
    on_baseline = sandbox.run_tests(bf.BASELINE_CODE, bf.FIXED_TESTS, timeout=60)
    assert set(on_baseline["failed"]) == new_names                      # exactly the new tests expose the bug
    on_fix = sandbox.run_tests(bf.FIXED_CODE, bf.FIXED_TESTS, timeout=60)
    assert not on_fix["failed"]


def test_the_fix_touches_exactly_the_declared_symbols_and_is_small():
    assert ci.changed_symbols(bf.BASELINE_CODE, bf.FIXED_CODE) == {
        "LinkRepository.__init__": "modified", "LinkRepository.delete": "modified", "LinkRepository._purge_orphans": "added"}
    assert cc.ChangeSet({"shortener.py": bf.BASELINE_CODE}, {"shortener.py": bf.FIXED_CODE}).lines_changed <= bf.MAX_LINES_CHANGED


# ---- impact analysis is grounded ------------------------------------------------
def _grounded(impact, artifacts_extra=None):
    return comp.impact_analysis_grounded(bf.BASELINE_CODE).evaluate({"impact_analysis": json.dumps(impact), **(artifacts_extra or {})})


GOOD_IMPACT = json.loads(bf.IMPACT)


def test_reference_impact_analysis_is_grounded():
    assert _grounded(GOOD_IMPACT) == []


def test_a_symbol_that_does_not_exist_is_rejected():
    bad = {**GOOD_IMPACT, "impacted": GOOD_IMPACT["impacted"] + [{"symbol": "LinkRepository.vacuum", "reason": "x"}]}
    assert any("vacuum" in v.message for v in _grounded(bad))


def test_a_new_symbol_that_already_exists_is_rejected():
    assert any("already exists" in v.message for v in _grounded({**GOOD_IMPACT, "new_symbols": ["LinkRepository.delete"]}))


def test_impact_analysis_needs_a_root_cause_impacted_symbols_and_risks_and_valid_json():
    assert any("root_cause" in v.message for v in _grounded({**GOOD_IMPACT, "root_cause": ""}))
    assert any("impacted" in v.message for v in _grounded({**GOOD_IMPACT, "impacted": []}))
    assert comp.impact_analysis_grounded(bf.BASELINE_CODE).evaluate({"impact_analysis": "not json"})
    assert comp.impact_analysis_grounded(bf.BASELINE_CODE).evaluate({})                # fails closed


# ---- the patch must stay inside the declared blast radius -----------------------
def _within(code, impact=bf.IMPACT):
    return cc.change_within_declared_impact(bf.BASELINE_CODE).evaluate({"implementation": code, "impact_analysis": impact})


def test_reference_fix_is_within_the_declared_impact():
    assert _within(bf.FIXED_CODE) == []


def test_touching_an_undeclared_symbol_is_flagged_by_name():
    out = _within(bf.FIXTURES["scope_creep"])
    assert len(out) == 1 and "RateLimiter.check" in out[0].message and "modified" in out[0].message


def test_removing_an_undeclared_symbol_is_flagged():
    removed = bf.FIXED_CODE.replace("def _iso(", "def _iso_renamed(")
    assert any("removed" in v.message or "added" in v.message for v in _within(removed))


def test_within_impact_fails_closed_on_missing_or_broken_inputs():
    assert cc.change_within_declared_impact(bf.BASELINE_CODE).evaluate({"implementation": bf.FIXED_CODE})
    assert _within(bf.FIXED_CODE, "not json")
    assert _within("def broken(:\n")


# ---- backward compatibility -----------------------------------------------------
def _api(new_code):
    return PolicySet(cc.public_api_stable()).evaluate(cc.ChangeSet({"shortener.py": bf.BASELINE_CODE}, {"shortener.py": new_code}))


def test_reference_fix_keeps_the_public_api():
    assert _api(bf.FIXED_CODE) == []


def test_new_required_parameter_and_removed_function_break_the_api():
    assert any("get_link" in v.message for v in _api(bf.FIXTURES["api_break"]))
    assert any("removed" in v.message for v in _api(bf.FIXED_CODE.replace("def validate_alias(", "def _validate_alias(")))


def test_adding_optional_parameter_or_new_public_function_is_allowed():
    assert _api(bf.FIXED_CODE.replace("def get_link(self, code: str) -> dict:", "def get_link(self, code: str, verbose: bool = False) -> dict:")) == []
    assert _api(bf.FIXED_CODE + "\ndef brand_new():\n    \"\"\"doc\"\"\"\n") == []


def test_non_python_files_and_new_files_are_ignored_by_the_api_policy():
    cs = cc.ChangeSet({"README.md": "a"}, {"README.md": "b", "extra.py": "def f(): pass"})
    assert PolicySet(cc.public_api_stable()).evaluate(cs) == []


# ---- tests may be added, never removed; they must reproduce the bug -------------
def test_dropping_an_existing_test_is_flagged():
    out = comp.no_tests_removed(bf.BASELINE_TESTS).evaluate({"tests": bf.FIXTURES["dropped_test"]})
    assert len(out) == 1 and "test_validate_alias_rules" in out[0].message
    assert comp.no_tests_removed(bf.BASELINE_TESTS).evaluate({"tests": bf.FIXED_TESTS}) == []
    assert comp.no_tests_removed(bf.BASELINE_TESTS).evaluate({})                      # fails closed


def test_a_fix_without_a_new_test_is_rejected():
    out = comp.regression_test_reproduces_bug(bf.BASELINE_CODE, bf.BASELINE_TESTS).evaluate({"tests": bf.BASELINE_TESTS})
    assert any("no new test" in v.message for v in out)


def test_a_new_test_that_also_passes_on_the_buggy_code_is_rejected():
    useless = bf.BASELINE_TESTS + "\n\ndef test_trivially_true():\n    assert 1 + 1 == 2\n"
    out = comp.regression_test_reproduces_bug(bf.BASELINE_CODE, bf.BASELINE_TESTS).evaluate({"tests": useless})
    assert any("do not reproduce the bug" in v.message for v in out)


def test_the_real_regression_tests_are_accepted():
    assert comp.regression_test_reproduces_bug(bf.BASELINE_CODE, bf.BASELINE_TESTS).evaluate({"tests": bf.FIXED_TESTS}) == []


def test_changelog_must_reference_the_ticket():
    assert comp.changelog_references_ticket("BUG-142").evaluate({"docs": bf.CHANGELOG}) == []
    assert comp.changelog_references_ticket("BUG-142").evaluate({"docs": bf.FIXTURES["changelog_no_ticket"]})
    assert comp.changelog_references_ticket("BUG-142").evaluate({})


# ---- the orchestrated scenario --------------------------------------------------
def test_workflow_passes_governance_lint():
    graph, _ = bf.build(bf.make_llm())
    assert cc.lint_graph(graph) == []


def test_clean_run_fixes_the_bug_end_to_end(tmp_path):
    r = go(tmp_path)
    assert r["status"] is RunStatus.COMPLETED and r["metrics"]["policy_violations"] == {}
    art = r["run_dir"] / "artifacts"
    assert (art / "impact_analysis.json").exists() and (art / "implementation.py").read_text(encoding="utf-8") == bf.FIXED_CODE
    assert [n for n in r["engine"].graph.nodes][:3] == ["requirements", "impact_analysis", "design"]
    assert r["engine"].graph.nodes["impact_analysis"].depends_on == ("requirements",)


@pytest.mark.parametrize("violation,expected", [
    ("scope-creep", {"change_control/change-within-declared-impact": 1}),
    ("api-break", {"change_control/change-within-declared-impact": 1, "change_control/public-api-stable": 1}),
    ("ungrounded-impact", {"compliance/impact-analysis-grounded": 1}),
    ("no-regression-test", {"compliance/regression-test-reproduces-bug": 1}),
    ("drop-tests", {"compliance/no-tests-removed": 1}),
    ("changelog-no-ticket", {"compliance/changelog-references-ticket": 1}),
])
def test_each_violation_is_blocked_by_exactly_the_named_policies_then_recovers(tmp_path, violation, expected):
    role, bad, _ = bf.VIOLATIONS[violation]
    llm = bf.make_llm(scripted={role: [bad, bf.DEFAULTS[role]]})
    r = go(tmp_path, llm)
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == expected and r["metrics"]["retries"] == 1


def test_an_ungrounded_analysis_that_never_improves_stops_the_run_before_any_code_is_touched(tmp_path):
    r = go(tmp_path, bf.make_llm(scripted={"analyst": [bf.FIXTURES["ungrounded_impact"]]}))
    assert r["status"] is RunStatus.HALTED
    assert "implementation" not in r["engine"].context and r["engine"].state["design"] is NodeState.SKIPPED


def test_an_oversized_patch_is_blocked_by_the_tight_brownfield_budget(tmp_path):
    padded = bf.FIXED_CODE + "".join(f"# padding line {i}\n" for i in range(60))
    r = go(tmp_path, bf.make_llm(scripted={"developer": [padded, bf.FIXED_CODE]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"change_control/max-lines-changed": 1}


def test_a_developer_that_keeps_breaking_the_api_falls_back_to_the_conservative_agent(tmp_path):
    llm = bf.make_llm(scripted={"developer": [bf.FIXTURES["api_break"]]})       # persistent for the primary
    r = go(tmp_path, llm)
    assert r["status"] is RunStatus.COMPLETED and r["engine"].producer["implementation"] == "developer_fallback"
    assert r["metrics"]["fallbacks"] == 1


def test_a_failed_design_revision_rolls_back_to_the_verified_fix(tmp_path):
    llm = bf.make_llm()

    def break_developers():
        llm.inject_failures("developer", 99)
        llm.inject_failures("developer_fallback", 99)

    r = go(tmp_path, llm, revisions=[("impact_analysis", "analysis reopened", break_developers)])
    assert r["status"] is RunStatus.HALTED and r["metrics"]["rollbacks"] == 1
    assert (r["run_dir"] / "artifacts" / "implementation.py").read_text(encoding="utf-8") == bf.FIXED_CODE


def test_cli_runs_brownfield_and_rejects_violations_from_other_workflows(tmp_path):
    base = ["run", "brownfield", "--approve", "auto", "--run-root", str(tmp_path)]
    assert cli.main(base) == 0
    for name in bf.VIOLATIONS:
        assert cli.main(base + ["--violate", name]) == 0
    assert cli.main(base + ["--revise-design", "fail"]) == 1
    with pytest.raises(SystemExit):
        cli.main(base + ["--violate", "ui-cdn"])                                  # a shortener-only violation

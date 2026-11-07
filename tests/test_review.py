"""The review stage with a real model.

Found on the second live run (Sonnet): the reviewer rejected three times and the run halted at `review` with only "reviewer did not
approve". Three faults: the reviewer judged code, tests and docs WITHOUT the request or the UI, and without being told what had already
been verified; its reasons were thrown away; and a retry told it that its verdict "was rejected by automated checks" so it simply
repeated itself.
"""
import json

from orchestrator.agents.llm_agent import REVIEW_RETRY, llm_agent, review_instruction
from orchestrator.governance import approvals, gates as g
from orchestrator.runner import run_workflow
from orchestrator.workflows import ambiguous, brownfield, shortener, slugify
from tests.test_admin_api import Admin, with_clean_env


# ---------------------------------------------------------------------------------------------- the gate
def test_the_gate_reports_the_reviewers_reasons_instead_of_discarding_them():
    ok, why = g.reviewer_approves({}, {"content": "REJECT\n- tests/alias: the tests never cover alias collisions\n- docs are wrong"})
    assert ok is False and why == "reviewer did not approve: REJECT - tests/alias: the tests never cover alias collisions - docs are wrong"
    long = g.reviewer_approves({}, {"content": "REJECT " + "x" * 1000})[1]
    assert len(long) < 360 and long.endswith("...")
    assert g.reviewer_approves({}, {"content": "   "}) == (False, "reviewer did not approve: (empty reply)")
    assert g.reviewer_approves({}, {"content": "approve - looks right"}) == (True, "ok")
    assert g.reviewer_approves({}, {"content": "APPROVE\nSuggestions: none"}) == (True, "ok")


# ---------------------------------------------------------------------------------------- the reviewer's inputs
class Recorder:
    def __init__(self, inner):
        self.inner, self.prompts = inner, []

    def complete(self, role, prompt):
        self.prompts.append((role, prompt))
        return self.inner.complete(role, prompt)


CTX = {"requirements": {"content": "REQUIREMENTS-TEXT"}, "implementation": {"content": "CODE-TEXT"}, "tests": {"content": "TESTS-TEXT"},
       "docs": {"content": "DOCS-TEXT"}, "ui": {"content": "UI-TEXT"}, "impact_analysis": {"content": "IMPACT-TEXT"},
       "clarifications": {"content": "DECISIONS-TEXT"}}


def review_prompt(module, **kw):
    rec = Recorder(module.make_llm())
    _, agents = module.build(rec, **kw)
    agents["reviewer"](CTX)
    return [p for role, p in rec.prompts if role == "reviewer"][0]


def test_the_greenfield_reviewer_sees_the_request_the_ui_and_what_was_already_verified():
    prompt = review_prompt(shortener, requirement="Build a shortener with PIN-CODE-42.")
    for needle in ("PIN-CODE-42", "REQUIREMENTS-TEXT", "CODE-TEXT", "TESTS-TEXT", "DOCS-TEXT", "### ui\nUI-TEXT", "ALREADY PASSED",
                   "BLOCKING defect", "NOT blocking", "at most three", "web UI satisfies the security and accessibility policies"):
        assert needle in prompt, needle


def test_a_workflow_without_a_ui_does_not_mention_one():
    prompt = review_prompt(slugify)
    assert "### ui" not in prompt and "web UI" not in prompt and "REQUIREMENTS-TEXT" in prompt and "ALREADY PASSED" in prompt


def test_the_change_workflows_give_the_reviewer_the_analysis_or_decisions_they_are_judged_by():
    bf = review_prompt(brownfield)
    assert "### impact_analysis\nIMPACT-TEXT" in bf and "every existing test" in bf and "REQUIREMENTS-TEXT" in bf
    amb = review_prompt(ambiguous)
    assert "### clarifications\nDECISIONS-TEXT" in amb and "every existing test" in amb


def test_the_request_text_in_the_review_prompt_is_the_one_the_operator_wrote():
    assert "ORIGINAL REQUEST" in review_instruction() and "ORIGINAL REQUEST" in review_instruction(ui=True, existing_code=True)
    assert "binding" in review_prompt(brownfield, requirement="Fix BUG-999 only.") and "Fix BUG-999 only." in review_prompt(brownfield, requirement="Fix BUG-999 only.")


# ------------------------------------------------------------------------------------------ the retry
def test_a_reviewer_retry_asks_it_to_recheck_not_to_fix_an_artifact():
    rec = Recorder(shortener.make_llm())
    _, agents = shortener.build(rec)
    feedback = {"__feedback__": {"content": "reviewer did not approve: REJECT - style nits", "previous": "REJECT\n- style nits"}}
    agents["reviewer"]({**CTX, **feedback})
    prompt = [p for role, p in rec.prompts if role == "reviewer"][0]
    assert "Re-check your previous reply" in prompt and REVIEW_RETRY in prompt and "REJECT\n- style nits" in prompt
    assert "Drop every reason that is not blocking" in prompt and "reply APPROVE" in prompt     # the wording itself, not only the constant
    assert "REJECTED by automated checks" not in prompt and "fix the problems above" not in prompt


def test_other_agents_still_get_the_fix_it_feedback():
    seen = []

    class Echo:
        def complete(self, role, prompt):
            seen.append(prompt)
            return "ok"

    llm_agent(Echo(), "developer", "Write code.", ("requirements",))({"requirements": {"content": "r"}, "__feedback__": {"content": "bad import", "previous": "old code"}})
    assert "REJECTED by automated checks" in seen[0] and "bad import" in seen[0] and "fix the problems above" in seen[0] and "old code" in seen[0]


# ----------------------------------------------------------------------------- a halted run keeps what was refused
def test_a_run_halted_by_the_reviewer_shows_why_and_keeps_the_verdict(tmp_path):
    verdict = "REJECT\n- tests/test_alias.py: the tests never cover alias collisions"
    graph, agents = shortener.build(shortener.make_llm(scripted={"reviewer": [verdict] * 6}))
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="rejected")
    assert res["status"].value == "HALTED"
    failed = [e for e in res["audit"].events if e["event"] == "attempt_failed" and e["node"] == "review"]
    assert len(failed) == 6 and all("the tests never cover alias collisions" in e["error"] for e in failed)       # reasons in the log
    reworks = [e for e in res["audit"].events if e["event"] == "rework"]                                          # 2 rounds sent back, then the budget is spent
    assert [e["granted"] for e in reworks] == [True, True, False] and reworks[-1]["why"].startswith("rework budget exhausted")
    assert "the tests never cover alias collisions" in res["engine"].halt_reason
    saved = tmp_path / "rejected" / "artifacts" / "REJECTED-review.md"
    assert saved.read_text(encoding="utf-8") == verdict                                                          # and kept in full
    report = (tmp_path / "rejected" / "report.md").read_text(encoding="utf-8")
    assert "## Rejected outputs" in report and "REJECTED-review.md" in report
    assert not (tmp_path / "rejected" / "artifacts" / "review.md").exists()                                      # never committed


def test_a_run_that_completes_has_no_rejected_outputs(tmp_path):
    graph, agents = shortener.build(shortener.make_llm())
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="clean")
    assert res["engine"].rejected == {} and not list((tmp_path / "clean" / "artifacts").glob("REJECTED-*"))
    assert "Rejected outputs" not in (tmp_path / "clean" / "report.md").read_text(encoding="utf-8")


def test_a_human_rejection_keeps_the_rejected_design(tmp_path):
    graph, agents = shortener.build(shortener.make_llm())
    res = run_workflow(graph, agents, lambda node, out: node.id != "design", run_root=tmp_path, run_id="human")
    assert res["status"].value == "HALTED" and "design rejected by human" in res["engine"].halt_reason
    assert (tmp_path / "human" / "artifacts" / "REJECTED-design.md").read_text(encoding="utf-8") == res["engine"].rejected["design"]["content"]


def test_a_stage_that_fails_a_gate_once_and_then_passes_leaves_nothing_behind(tmp_path):
    graph, agents = shortener.build(shortener.make_llm(scripted=__import__("orchestrator.cli", fromlist=["x"]).scripted_answers(shortener, ["secret"])))
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="recovered")
    assert res["status"].value == "COMPLETED" and res["metrics"]["retries"] == 1 and res["engine"].rejected == {}


# ------------------------------------------------------------------------------------------------ the page
@with_clean_env
def test_the_admin_page_lists_the_rejected_output_and_says_where_to_look(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run(scenario="shortener", requirement="Build a shortener.", limits={"max_lines": 80})
    s = a.finish()
    assert s["run"]["status"] == "halted" and s["run"]["runnable"] is False
    assert "REJECTED-implementation.py" in s["artifacts"] and "implementation.py" not in s["artifacts"]
    assert any("REJECTED-<stage>" in n for n in s["notes"])
    shown = a.call("GET", f"/api/runs/{run_id}/artifacts/REJECTED-implementation.py")[2]
    # (Windows writes the saved file with CRLF and the page serves its bytes as they are, so compare ignoring line endings)
    assert shown["content"].replace("\r\n", "\n") == shortener.GOOD_CODE                # the 507 lines the 80-line limit refused
    assert a.call("POST", "/api/service", {"run_id": run_id})[0] == 400              # a rejected file is never offered as a service

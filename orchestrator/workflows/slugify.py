"""Toy workflow: requirement -> design -> code -> (tests || docs) -> review -> release.

Used as the test bed for the orchestrator before it is pointed at the URL shortener.
"""
from __future__ import annotations

from ..agents.llm_agent import llm_agent
from ..core.graph import Graph, Node
from ..governance import gates as g
from ..llm import mock_client as mc

REQUIREMENT = "Write a Python function slugify(text) that turns arbitrary text into a URL-safe slug, with tests and docs."


def release_agent(ctx):
    """Deterministic (non-LLM) agent: assembles a release-readiness checklist from evidence."""
    checklist = {
        "requirements_captured": "requirements" in ctx,
        "design_approved": "design" in ctx,
        "security_policy_passed": "implementation" in ctx,
        "tests_passed_in_sandbox": "tests" in ctx,
        "docs_present": "docs" in ctx,
        "review_approved": ctx["review"]["content"].upper().startswith("APPROVE"),
    }
    ready = all(checklist.values())
    return {"role": "release_manager", "content": f"READY={ready}\n{checklist}",
            "derived_from": ["requirements", "design", "implementation", "tests", "docs", "review"]}


def build(llm, requirement: str = REQUIREMENT, code_path: str = "slugify.py", max_lines: int = 200):
    graph = (
        Graph()
        .add(Node("requirements", "requirements",
                  exit_gates=(g.json_has_keys("goal", "functional", "ambiguities", "acceptance"),),
                  description="Normalize intent, surface ambiguities"))
        .add(Node("design", "architect", depends_on=("requirements",),
                  entry_gates=(g.require_inputs("requirements"),),
                  requires_approval=True, tags=("high_impact",), description="Design (human approval)"))
        .add(Node("implementation", "developer", depends_on=("design",),
                  max_retries=2,
                  fallback_agent="developer_fallback", tags=("code",),
                  exit_gates=(g.valid_python, g.security_policy, g.code_compliance,
                              g.change_control_gate(code_path, max_lines=max_lines)),
                  description="Generate code; syntax + security + compliance + change-control gates"))
        .add(Node("tests", "tester", depends_on=("implementation",),
                  exit_gates=(g.valid_python, g.tests_pass_against_implementation, g.tests_compliance),
                  description="Generate tests; must pass against the implementation in a sandbox"))
        .add(Node("docs", "doc_writer", depends_on=("implementation",),
                  exit_gates=(g.docs_compliance,),
                  description="Generate documentation (parallel with tests)"))
        .add(Node("review", "reviewer", depends_on=("tests", "docs"),
                  exit_gates=(g.reviewer_approves,),
                  description="Sync point: critic reviews all artifacts"))
        .add(Node("release", "release_manager", depends_on=("review",),
                  entry_gates=(g.release_compliance,),
                  requires_approval=True, tags=("high_impact", "release"),
                  description="Release readiness: compliance evidence re-verified, then human approval"))
    )
    agents = {
        "requirements": llm_agent(llm, "requirements", "Extract requirements as JSON with keys goal, functional, ambiguities, acceptance.", seed=requirement),
        "architect": llm_agent(llm, "architect", "Produce a concise design.", ("requirements",)),
        "developer": llm_agent(llm, "developer", "Write the Python implementation only.", ("requirements", "design")),
        "developer_fallback": llm_agent(llm, "developer_fallback", "Write the SIMPLEST correct Python implementation only. No extras.", ("requirements", "design")),
        "tester": llm_agent(llm, "tester", "Write plain test_ functions (no imports of the module).", ("requirements", "implementation")),
        "doc_writer": llm_agent(llm, "doc_writer", "Write markdown docs.", ("design", "implementation")),
        "reviewer": llm_agent(llm, "reviewer", "Reply APPROVE or REJECT with reasons.", ("implementation", "tests", "docs")),
        "release_manager": release_agent,
    }
    return graph, agents


# ---- what the CLI needs to run and to demonstrate violations for this workflow ----
FIXTURES = {
    "bad_secret": mc.BAD_CODE_SECRET, "bad_import": mc.BAD_CODE_IMPORT, "undocumented": mc.UNDOCUMENTED_CODE,
    "good_code": mc.GOOD_CODE, "thin_tests": mc.THIN_TESTS,
}


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted)

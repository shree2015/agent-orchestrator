"""Toy workflow: requirement -> design -> code -> (tests || docs) -> review -> release.

Used as the test bed for the orchestrator before it is pointed at the URL shortener.
"""
from __future__ import annotations

from ..agents.llm_agent import llm_agent
from ..core.graph import Graph, Node
from ..governance import gates as g
from ..governance.policies.compliance import RELEASE_EVIDENCE
from ..governance.policies.planning import GraphRef
from ..llm import mock_client as mc

REQUIREMENT = "Write a Python function slugify(text) that turns arbitrary text into a URL-safe slug, with tests and docs."


def _release_checklist_extra(ctx):
    return {"ui_present": "ui" in ctx} if "ui" in ctx else {}


def release_agent(ctx):
    """Deterministic (non-LLM) agent: assembles a release-readiness checklist from evidence."""
    checklist = {
        "requirements_captured": "requirements" in ctx,
        "design_approved": "design" in ctx,
        "security_policy_passed": "implementation" in ctx,
        "tests_passed_in_sandbox": "tests" in ctx,
        "docs_present": "docs" in ctx,
        "review_approved": ctx["review"]["content"].upper().startswith("APPROVE"),
        **_release_checklist_extra(ctx),
    }
    ready = all(checklist.values())
    return {"role": "release_manager", "content": f"READY={ready}\n{checklist}",
            "derived_from": ["requirements", "design", "implementation", "tests", "docs", "review"]
                            + (["ui"] if "ui" in ctx else [])}


def build(llm, requirement: str = REQUIREMENT, code_path: str = "slugify.py", max_lines: int = 200,
          ui: bool = False, plan: bool = False):
    ref = GraphRef()                   # lets plan/traceability gates look at the finished graph
    graph = (
        Graph()
        .add(Node("requirements", "requirements",
                  exit_gates=(g.json_has_keys("goal", "functional", "ambiguities", "acceptance"),),
                  description="Normalize intent, surface ambiguities"))
        .add(Node("design", "architect", depends_on=("requirements", "plan") if plan else ("requirements",),
                  entry_gates=(g.require_inputs("requirements"),),
                  requires_approval=True, tags=("high_impact",), description="Design (human approval)"))
        .add(Node("implementation", "developer", depends_on=("design",),
                  max_retries=2,
                  fallback_agent="developer_fallback", tags=("code",),
                  exit_gates=(g.valid_python, g.security_policy, g.code_compliance,
                              g.change_control_gate(code_path, max_lines=max_lines)),
                  description="Generate code; syntax + security + compliance + change-control gates"))
        .add(Node("tests", "tester", depends_on=("implementation",),
                  exit_gates=(g.valid_python, g.tests_pass_against_implementation, g.tests_compliance)
                  + ((g.traceability_gate(ref),) if plan else ()),
                  description="Generate tests; must pass against the implementation in a sandbox"))
        .add(Node("docs", "doc_writer", depends_on=("implementation",),
                  exit_gates=(g.docs_compliance,),
                  description="Generate documentation (parallel with tests)"))
        .add(Node("review", "reviewer", depends_on=("tests", "docs") + (("ui",) if ui else ()),
                  exit_gates=(g.reviewer_approves,),
                  description="Sync point: critic reviews all artifacts"))
        .add(Node("release", "release_manager", depends_on=("review",),
                  entry_gates=(g.release_compliance_for(RELEASE_EVIDENCE + (("ui",) if ui else ()) + (("plan",) if plan else ())),),
                  requires_approval=True, tags=("high_impact", "release"),
                  description="Release readiness: compliance evidence re-verified, then human approval"))
    )
    if plan:  # decomposition: tasks with dependencies, mapped to workflow stages and traced to acceptance criteria
        graph.add(Node("plan", "planner", depends_on=("requirements",), max_retries=2,
                       exit_gates=(g.plan_gate(ref, ("implementation", "tests", "docs") + (("ui",) if ui else ())),),
                       description="Decompose the requirement into tasks; plan must be coherent, executable and traceable"))
    if ui:  # a web UI is one more parallel branch: it only needs the implementation's API contract
        graph.add(Node("ui", "frontend", depends_on=("implementation",), max_retries=2, tags=("code",),
                       exit_gates=(g.ui_security, g.ui_compliance, g.change_control_gate("ui.html", max_lines=500)),
                       description="Generate the web UI; HTML/JS security + API-wiring + accessibility gates"))
    original = "### original request (exact names, routes, numbers and limits here are binding)\n" + requirement
    agents = {
        "requirements": llm_agent(llm, "requirements", "Extract requirements as JSON with keys goal, functional, constraints, ambiguities, acceptance. Preserve every exact name, route and number from the request.", seed=requirement),
        "architect": llm_agent(llm, "architect", "Produce a concise design for this request.", ("requirements",) + (("plan",) if plan else ()), seed=original),
        "developer": llm_agent(llm, "developer", "Write the complete Python implementation for this request.", ("requirements", "design"), seed=original),
        "developer_fallback": llm_agent(llm, "developer_fallback", "Write the SIMPLEST correct Python implementation only. No extras.", ("requirements", "design"), seed=original),
        "tester": llm_agent(llm, "tester", "Write plain test_ functions (do not import the module under test)." + (" Create every test name the plan promises." if plan else ""), ("requirements", "implementation") + (("plan",) if plan else ())),
        "doc_writer": llm_agent(llm, "doc_writer", "Write markdown docs.", ("design", "implementation")),
        "reviewer": llm_agent(llm, "reviewer", "Reply APPROVE or REJECT with reasons.", ("implementation", "tests", "docs")),
        "release_manager": release_agent,
    }
    if plan:
        agents["planner"] = llm_agent(llm, "planner", "Decompose the requirement into JSON tasks (id, title, node, depends_on) plus acceptance-criterion traceability.", ("requirements",))
    if ui:
        agents["frontend"] = llm_agent(llm, "frontend", "Write ONE self-contained index.html (inline CSS and JS, no external resources) that calls exactly the API routes the request and the implementation define.", ("requirements", "design", "implementation"), seed=original)
    ref.graph = graph
    return graph, agents


# ---- what the CLI needs to run and to demonstrate violations for this workflow ----
FIXTURES = {
    "bad_secret": mc.BAD_CODE_SECRET, "bad_import": mc.BAD_CODE_IMPORT, "undocumented": mc.UNDOCUMENTED_CODE,
    "good_code": mc.GOOD_CODE, "thin_tests": mc.THIN_TESTS,
}


# name -> (agent role, bad output, persistent?)  persistent = the agent keeps producing it, so the policy must halt the run
VIOLATIONS = {
    "secret": ("developer", FIXTURES["bad_secret"], False),
    "import": ("developer", FIXTURES["bad_import"], False),
    "undocumented": ("developer", FIXTURES["undocumented"], False),
    "thin-tests": ("tester", FIXTURES["thin_tests"], True),
    "empty-docs": ("doc_writer", "", True),
}


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted)

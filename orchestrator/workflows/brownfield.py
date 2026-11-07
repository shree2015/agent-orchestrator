"""Brownfield scenario: fix BUG-142 in the EXISTING URL shortener under governance.

requirements -> impact_analysis -> design* -> implementation -> (tests || docs) -> review -> release*

What is different from the greenfield graph, and why it matters:
  * `impact_analysis` runs first and is GROUNDED: every symbol it names must exist in the real baseline
    code, so an agent cannot hand-wave about code it has not read.
  * The patch is judged against the baseline, not in isolation: tiny size limit, backward-compatible public
    API, and it may only touch the symbols the analysis declared (declare the blast radius, then be held to it).
  * The tests must include a regression test that FAILS on the old code (proof it reproduces the bug),
    must not drop any existing test, and must all pass on the new code.
  * The changelog must reference the ticket.
As with the other workflows, the agents are an offline mock returning reviewed reference artifacts.
"""
from __future__ import annotations

import ast
from pathlib import Path

from ..agents.llm_agent import llm_agent
from ..core.graph import Graph, Node
from ..governance import gates as g
from ..governance.policies.compliance import RELEASE_EVIDENCE
from ..llm import mock_client as mc
from . import shortener
from .slugify import release_agent

_REF = Path(__file__).parent / "reference" / "brownfield"
read = lambda name: (_REF / name).read_text(encoding="utf-8")

TICKET = "BUG-142"
CODE_PATH = "shortener.py"
MAX_LINES_CHANGED = 40          # a bug fix in an existing codebase gets a much smaller budget than new code

ASSETS = {"ui.html": shortener.GOOD_UI}        # part of the existing service; the fix does not touch it
BUG_REPORT = read("bug_report.md")
REQUIREMENT = BUG_REPORT
BASELINE_CODE = read("baseline_shortener.py.txt")
BASELINE_TESTS = read("baseline_tests.py.txt")
FIXED_CODE = read("fixed_shortener.py.txt")
FIXED_TESTS = read("fixed_tests.py.txt")
IMPACT = read("impact_analysis.json")
CHANGELOG = read("changelog.md")


def _replace(text: str, old: str, new: str) -> str:
    assert old in text, f"fixture fragment not found: {old[:40]!r}"
    return text.replace(old, new, 1)


def _without_test(text: str, name: str) -> str:
    fn = next(n for n in ast.parse(text).body if isinstance(n, ast.FunctionDef) and n.name == name)
    lines = text.splitlines(keepends=True)
    return "".join(lines[:fn.lineno - 1] + lines[fn.end_lineno:])


DEFAULTS = {
    "requirements": read("requirements.json"),
    "analyst": IMPACT,
    "architect": read("design.md.txt"),
    "developer": FIXED_CODE,
    "developer_fallback": FIXED_CODE,
    "tester": FIXED_TESTS,
    "doc_writer": CHANGELOG,
}

FIXTURES = {
    "scope_creep": _replace(FIXED_CODE, "if len(self._hits) > 10_000:", "if len(self._hits) > 5_000:"),
    "api_break": _replace(FIXED_CODE, "def get_link(self, code: str) -> dict:", "def get_link(self, code: str, include_expired: bool) -> dict:"),
    "ungrounded_impact": _replace(IMPACT, '"LinkRepository.delete"', '"LinkRepository.remove_link"'),
    "no_regression_test": BASELINE_TESTS,
    "dropped_test": _without_test(FIXED_TESTS, "test_validate_alias_rules"),
    "changelog_no_ticket": CHANGELOG.replace(TICKET, "BUG-0"),   # every mention, not just the first
}

# name -> (agent role, bad first answer, persistent?)
VIOLATIONS = {
    "scope-creep": ("developer", FIXTURES["scope_creep"], False),
    "api-break": ("developer", FIXTURES["api_break"], False),
    "ungrounded-impact": ("analyst", FIXTURES["ungrounded_impact"], False),
    "no-regression-test": ("tester", FIXTURES["no_regression_test"], False),
    "drop-tests": ("tester", FIXTURES["dropped_test"], False),
    "changelog-no-ticket": ("doc_writer", FIXTURES["changelog_no_ticket"], False),
}


def build(llm, requirement: str = REQUIREMENT):
    graph = (
        Graph()
        .add(Node("requirements", "requirements",
                  exit_gates=(g.json_has_keys("goal", "functional", "ambiguities", "acceptance"),),
                  description="Normalise the bug report into an engineering problem"))
        .add(Node("impact_analysis", "analyst", depends_on=("requirements",), max_retries=2,
                  exit_gates=(g.json_has_keys("impacted", "root_cause", "risks"), g.impact_grounded(BASELINE_CODE)),
                  description="Codebase reasoning: impacted symbols, data flows, APIs, risks (checked against the real code)"))
        .add(Node("design", "architect", depends_on=("requirements", "impact_analysis"),
                  entry_gates=(g.require_inputs("requirements", "impact_analysis"),),
                  requires_approval=True, tags=("high_impact",), description="Fix plan (human approval)"))
        .add(Node("implementation", "developer", depends_on=("design",), max_retries=2,
                  fallback_agent="developer_fallback", tags=("code",),
                  exit_gates=(g.valid_python, g.security_policy, g.code_compliance,
                              g.brownfield_change_gate(CODE_PATH, BASELINE_CODE, MAX_LINES_CHANGED),
                              g.within_declared_impact(BASELINE_CODE)),
                  description="Patch the existing code: small, backward compatible, inside the declared blast radius"))
        .add(Node("tests", "tester", depends_on=("implementation",), max_retries=2,
                  exit_gates=(g.valid_python, g.tests_pass_against_implementation, g.tests_compliance,
                              g.tests_not_removed(BASELINE_TESTS), g.regression_reproduced(BASELINE_CODE, BASELINE_TESTS)),
                  description="Regression test must FAIL on the old code and PASS on the fix; no tests may be dropped"))
        .add(Node("docs", "doc_writer", depends_on=("implementation",), max_retries=2,
                  exit_gates=(g.changelog_ticket(TICKET),),
                  description="Changelog entry referencing the ticket (parallel with tests)"))
        .add(Node("review", "reviewer", depends_on=("tests", "docs"), exit_gates=(g.reviewer_approves,),
                  description="Sync point: critic reviews patch, tests and changelog"))
        .add(Node("release", "release_manager", depends_on=("review",),
                  entry_gates=(g.release_compliance_for(RELEASE_EVIDENCE + ("impact_analysis",)),),
                  requires_approval=True, tags=("high_impact", "release"),
                  description="Release readiness: evidence re-verified, then human approval"))
    )
    agents = {
        "requirements": llm_agent(llm, "requirements", "Normalise this bug report into JSON with keys ticket, goal, functional, ambiguities, acceptance.", seed=requirement),
        "analyst": llm_agent(llm, "analyst", "Analyse the baseline code and return impact analysis JSON (root_cause, impacted symbols that EXIST, new_symbols, apis_affected, data_flows, risks).", ("requirements",), seed="### baseline code\n" + BASELINE_CODE),
        "architect": llm_agent(llm, "architect", "Write a minimal fix plan with rollout and rollback.", ("requirements", "impact_analysis")),
        "developer": llm_agent(llm, "developer", "Return the full patched file. Touch only the declared symbols; keep public signatures compatible.", ("requirements", "impact_analysis", "design"), seed="### baseline code\n" + BASELINE_CODE),
        "developer_fallback": llm_agent(llm, "developer_fallback", "Return the SMALLEST possible patched file fixing the bug.", ("requirements", "impact_analysis", "design"), seed="### baseline code\n" + BASELINE_CODE),
        "tester": llm_agent(llm, "tester", "Return the full test file: keep every existing test and add regression tests that fail on the baseline.", ("requirements", "implementation"), seed="### existing tests\n" + BASELINE_TESTS),
        "doc_writer": llm_agent(llm, "doc_writer", f"Write a changelog entry that references {TICKET}.", ("implementation", "impact_analysis")),
        "reviewer": llm_agent(llm, "reviewer", "Reply APPROVE or REJECT with reasons.", ("implementation", "tests", "docs")),
        "release_manager": release_agent,
    }
    return graph, agents


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted, defaults=DEFAULTS)

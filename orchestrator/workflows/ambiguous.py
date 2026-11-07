r"""Ambiguous scenario: "Make links expire sensibly." (FEAT-207) on the existing URL shortener.

requirements --> clarifications (HUMAN decisions) -------------------------\
      \--> impact_analysis --> plan ----------------------------------------+--> design* --> implementation
                                                                                  --> tests ->\
                                                                                  --> docs  ->+-> review --> release*

What this scenario demonstrates
  * An agent turns a vague request into STRUCTURED questions (options, risk, proposed default).
  * Policy decides who may answer: high-risk questions can only be answered by a human; an agent may
    assume low/medium-risk ones, but must say why. No human available -> the run stops safely.
  * Answers are validated (allowed options, mutual consistency) and must be VISIBLE in the code.
  * Changing an answer later (`engine.revise`) automatically re-plans: only the work that depended on
    that answer is redone (design onward); requirements, impact analysis and the plan are untouched.
Agents are an offline mock whose output is a function of what they read (the recorded decisions).
"""
from __future__ import annotations

import json
from pathlib import Path

from ..agents.llm_agent import llm_agent
from ..core.graph import Graph, Node
from ..governance import gates as g
from ..governance.policies.compliance import RELEASE_EVIDENCE
from ..governance.policies.planning import GraphRef
from ..llm import mock_client as mc
from . import plan_fixtures as pf
from . import shortener
from .slugify import release_agent

_REF = Path(__file__).parent / "reference" / "ambiguous"
read = lambda name: (_REF / name).read_text(encoding="utf-8")

TICKET = "FEAT-207"
CODE_PATH = "shortener.py"
MAX_LINES_CHANGED = 60
REQUIREMENT = read("request.md")
BASELINE_CODE = shortener.GOOD_CODE
BASELINE_TESTS = shortener.read("tests.py.txt")
ASSETS = {"ui.html": shortener.GOOD_UI}        # part of the existing service; this change does not touch it
HUMAN_RATIONALE = "answered by a human"
DAY = 24 * 3600

# decision id -> (module constant the code must set, how to compute its expected value)
HONOURED = {
    "default_ttl_days": ("DEFAULT_TTL_S", lambda days: days * DAY),
    "max_ttl_days": ("MAX_TTL_S", lambda days: days * DAY),
    "ttl_applies_to_aliases": ("DEFAULT_TTL_APPLIES_TO_ALIASES", lambda answer: answer == "yes"),
}
RULES = [(("default_ttl_days", "max_ttl_days"), lambda a: a["default_ttl_days"] <= a["max_ttl_days"],
          "default expiry ({default_ttl_days} days) is longer than the maximum a client may request ({max_ttl_days} days)")]


# ----------------------------------------------------------------------------------------- helpers
def _replace(text: str, old: str, new: str) -> str:
    assert old in text, f"fragment not found: {old[:50]!r}"
    return text.replace(old, new, 1)


def decisions_from_prompt(prompt: str) -> dict:
    """What an agent 'reads': the recorded decisions that llm_agent embedded in its prompt."""
    marker = "### clarifications\n"
    rest = prompt[prompt.index(marker) + len(marker):]
    end = rest.find("\n\n### ")
    data = json.loads(rest if end == -1 else rest[:end])
    return {d["id"]: d["answer"] for d in data["decisions"]}


def render_design(d: dict) -> str:
    alias = "also get" if d["ttl_applies_to_aliases"] == "yes" else "do not get"
    return (f"Design for {TICKET} (smallest change that honours the recorded decisions):\n"
            f"1. Module configuration: DEFAULT_TTL_S = {d['default_ttl_days']} days, MAX_TTL_S = {d['max_ttl_days']} days, "
            f"DEFAULT_TTL_APPLIES_TO_ALIASES = {d['ttl_applies_to_aliases'] == 'yes'}.\n"
            "2. ShortenerService gains an OPTIONAL default_ttl_s (None keeps today's behaviour, so the public API is unchanged).\n"
            f"3. shorten(): when no ttl_seconds is given, apply the default; custom-alias links {alias} the default.\n"
            "4. serve(): wire MAX_TTL_S and DEFAULT_TTL_S into the running service.\n"
            "Constraints kept: existing links are untouched, expired links still return 410, no route or payload changes.\n"
            "Rollout: deploy; only links created afterwards are affected. Rollback: revert; links created meanwhile keep their expiry.")


def render_code(d: dict) -> str:
    code = BASELINE_CODE
    code = _replace(code, "MAX_BODY_BYTES = 4096\n",
                    "MAX_BODY_BYTES = 4096\n"
                    f"DEFAULT_TTL_S = {d['default_ttl_days']} * 24 * 3600  # expiry for links created without ttl_seconds\n"
                    f"MAX_TTL_S = {d['max_ttl_days']} * 24 * 3600  # longest expiry a client may request\n"
                    f"DEFAULT_TTL_APPLIES_TO_ALIASES = {d['ttl_applies_to_aliases'] == 'yes'}\n")
    code = _replace(code, "max_ttl_s: int = 365 * 24 * 3600) -> None:",
                    "max_ttl_s: int = 365 * 24 * 3600, default_ttl_s: int | None = None) -> None:")
    code = _replace(code, "        self.max_ttl_s = max_ttl_s\n", "        self.max_ttl_s = max_ttl_s\n        self.default_ttl_s = default_ttl_s\n")
    code = _replace(code, "            expires_at = now + ttl_seconds\n",
                    "            expires_at = now + ttl_seconds\n"
                    "        elif self.default_ttl_s is not None and (alias is None or DEFAULT_TTL_APPLIES_TO_ALIASES):\n"
                    "            expires_at = now + self.default_ttl_s\n")
    return _replace(code, "ShortenerService(LinkRepository(db_path))",
                    "ShortenerService(LinkRepository(db_path), max_ttl_s=MAX_TTL_S, default_ttl_s=DEFAULT_TTL_S)")


def render_tests(d: dict) -> str:
    return BASELINE_TESTS.rstrip("\n") + f'''


# ------------------------------------------------------------- {TICKET}: default and maximum expiry
def test_decisions_are_reflected_in_the_code_constants():
    assert DEFAULT_TTL_S == {d['default_ttl_days']} * 24 * 3600
    assert MAX_TTL_S == {d['max_ttl_days']} * 24 * 3600
    assert DEFAULT_TTL_APPLIES_TO_ALIASES is {d['ttl_applies_to_aliases'] == 'yes'}


def test_links_without_ttl_get_the_default_expiry():
    svc, clock = make_service(default_ttl_s=DEFAULT_TTL_S)
    link, _ = svc.shorten("https://example.com/d1")
    assert link["expires_at"] == clock.now + DEFAULT_TTL_S


def test_default_ttl_links_expire():
    svc, clock = make_service(default_ttl_s=DEFAULT_TTL_S)
    code = svc.shorten("https://example.com/d2")[0]["code"]
    clock.advance(DEFAULT_TTL_S - 1)
    assert svc.resolve(code) == "https://example.com/d2"
    clock.advance(1)
    raises(Expired, svc.resolve, code)


def test_explicit_ttl_overrides_the_default():
    svc, clock = make_service(default_ttl_s=DEFAULT_TTL_S)
    link, _ = svc.shorten("https://example.com/d3", ttl_seconds=60)
    assert link["expires_at"] == clock.now + 60


def test_ttl_above_the_configured_maximum_is_rejected():
    svc, _ = make_service(max_ttl_s=MAX_TTL_S)
    raises(ShortenerError, svc.shorten, "https://example.com/d4", ttl_seconds=MAX_TTL_S + 1)
    assert svc.shorten("https://example.com/d5", ttl_seconds=MAX_TTL_S)[0]["expires_at"] is not None


def test_default_ttl_applies_to_aliases_as_decided():
    svc, _ = make_service(default_ttl_s=DEFAULT_TTL_S)
    link, _ = svc.shorten("https://example.com/d6", alias="campaign-1")
    assert (link["expires_at"] is not None) is DEFAULT_TTL_APPLIES_TO_ALIASES


def test_service_without_a_default_ttl_still_creates_permanent_links():
    svc, _ = make_service()
    assert svc.shorten("https://example.com/d7")[0]["expires_at"] is None
'''


def render_changelog(d: dict, decided_by: dict | None = None) -> str:
    who = decided_by or {}
    alias = "also receive" if d["ttl_applies_to_aliases"] == "yes" else "do not receive"
    lines = "\n".join(f"- `{k}` = {v} ({who.get(k, 'recorded')})" for k, v in d.items())
    return (f"# Changelog\n\n## Unreleased\n\n### Added\n"
            f"- {TICKET}: links created without `ttl_seconds` now expire after {d['default_ttl_days']} days (previously: never).\n"
            f"- {TICKET}: the longest expiry a client may request is {d['max_ttl_days']} days.\n"
            f"- {TICKET}: links with a custom alias {alias} the default expiry.\n\n"
            f"### Decisions (recorded and enforced in code and tests)\n{lines}\n\n"
            "### Notes\n- Existing links are not changed or migrated; expired links still return 410; no API changes.\n"
            "- The web UI's \"Never\" option now means \"server default\"; a UI wording follow-up is needed (out of scope here).\n")


def decisions_with_who(prompt: str) -> tuple[dict, dict]:
    marker = "### clarifications\n"
    rest = prompt[prompt.index(marker) + len(marker):]
    end = rest.find("\n\n### ")
    data = json.loads(rest if end == -1 else rest[:end])
    return ({d["id"]: d["answer"] for d in data["decisions"]}, {d["id"]: d["decided_by"] for d in data["decisions"]})


# ----------------------------------------------------------------------------- the decision-capture agent
def clarify_agent(asker):
    """Deterministic (non-LLM) agent: collects human answers and applies the assumption rules."""
    def run(ctx):
        questions = json.loads(ctx["requirements"]["content"])["ambiguities"]
        decisions = []
        for q in questions:
            raw = asker(q) if asker else None
            if raw is not None:
                answer = next((o for o in q["options"] if str(o) == str(raw)), raw)     # keep the option's own type
                decisions.append({"id": q["id"], "answer": answer, "decided_by": "human", "rationale": HUMAN_RATIONALE})
            elif q.get("risk") == "high":
                decisions.append({"id": q["id"], "answer": None, "decided_by": "unresolved"})
            else:
                decisions.append({"id": q["id"], "answer": q["proposed"], "decided_by": "assumed",
                                  "rationale": f"proposed default accepted: {q.get('why', '')}"})
        body = {"ticket": TICKET, "decisions": decisions}
        return {"role": "clarifier", "content": json.dumps(body, indent=2), "derived_from": ["requirements"]}
    return run


def answer_edit(decision_id: str, value):
    """Build a revise() payload that changes ONE decision to a human answer (all other decisions untouched)."""
    def make(engine) -> str:
        body = json.loads(engine.context["clarifications"]["content"])
        options = next((q["options"] for q in json.loads(engine.context["requirements"]["content"])["ambiguities"]
                        if q["id"] == decision_id), [])
        answer = next((o for o in options if str(o) == str(value)), value)          # keep the option's own type
        for d in body["decisions"]:
            if d["id"] == decision_id:
                d.update(answer=answer, decided_by="human", rationale=HUMAN_RATIONALE)
        return json.dumps(body, indent=2)
    return make


# ------------------------------------------------------------------------------------------- the graph
def build(llm, requirement: str = REQUIREMENT, asker=None):
    ref = GraphRef()
    graph = (
        Graph()
        .add(Node("requirements", "requirements",
                  exit_gates=(g.json_has_keys("goal", "functional", "ambiguities", "acceptance"),),
                  description="Turn the vague request into an engineering problem with STRUCTURED open questions"))
        .add(Node("clarifications", "clarifier", depends_on=("requirements",), max_retries=0,
                  exit_gates=(g.clarification_gate(RULES),),
                  description="Human decisions: high-risk questions need a person; assumptions must be justified"))
        .add(Node("impact_analysis", "analyst", depends_on=("requirements",), max_retries=2,
                  exit_gates=(g.json_has_keys("impacted", "root_cause", "risks"), g.impact_grounded(BASELINE_CODE)),
                  description="Codebase reasoning, checked against the real code (parallel with the questions)"))
        .add(Node("plan", "planner", depends_on=("impact_analysis",), max_retries=2,
                  exit_gates=(g.plan_gate(ref, ("implementation", "tests", "docs")),),
                  description="Tasks, sequencing and acceptance-criterion traceability"))
        .add(Node("design", "architect", depends_on=("plan", "clarifications"),
                  entry_gates=(g.require_inputs("requirements", "impact_analysis", "clarifications"),),
                  requires_approval=True, tags=("high_impact",), description="Design from the decisions (human approval)"))
        .add(Node("implementation", "developer", depends_on=("design",), max_retries=2,
                  fallback_agent="developer_fallback", tags=("code",),
                  exit_gates=(g.valid_python, g.security_policy, g.code_compliance,
                              g.brownfield_change_gate(CODE_PATH, BASELINE_CODE, MAX_LINES_CHANGED),
                              g.within_declared_impact(BASELINE_CODE), g.decisions_honoured(HONOURED)),
                  description="Patch the service; decisions must be visible in the code; stay inside the declared impact"))
        .add(Node("tests", "tester", depends_on=("implementation",), max_retries=2,
                  exit_gates=(g.valid_python, g.tests_pass_against_implementation, g.tests_compliance,
                              g.tests_not_removed(BASELINE_TESTS), g.regression_reproduced(BASELINE_CODE, BASELINE_TESTS),
                              g.traceability_gate(ref)),
                  description="New tests must fail on the old code, keep every old test, and match the plan's promises"))
        .add(Node("docs", "doc_writer", depends_on=("implementation",), max_retries=2,
                  exit_gates=(g.changelog_ticket(TICKET),), description="Changelog recording every decision (parallel with tests)"))
        .add(Node("review", "reviewer", depends_on=("tests", "docs"), exit_gates=(g.reviewer_approves,),
                  description="Sync point: critic reviews code, tests and changelog"))
        .add(Node("release", "release_manager", depends_on=("review",),
                  entry_gates=(g.release_compliance_for(RELEASE_EVIDENCE + ("clarifications", "impact_analysis", "plan")),),
                  requires_approval=True, tags=("high_impact", "release"),
                  description="Release readiness: all evidence re-verified, then human approval"))
    )
    ref.graph = graph
    reads = lambda role, text, *inputs, seed="": llm_agent(llm, role, text, inputs, seed=seed)
    agents = {
        "requirements": reads("requirements", "Turn the request into JSON: ticket, goal, functional, constraints, ambiguities (id, question, options, risk, proposed, why), acceptance.", seed=requirement),
        "clarifier": clarify_agent(asker),
        "analyst": reads("analyst", "Analyse the baseline code and return impact analysis JSON.", "requirements", seed="### baseline code\n" + BASELINE_CODE),
        "planner": reads("planner", "Decompose into tasks mapped to workflow stages and trace every acceptance criterion.", "requirements", "impact_analysis"),
        "architect": reads("architect", "Write a minimal design that honours the recorded decisions.", "requirements", "plan", "clarifications"),
        "developer": reads("developer", "Return the full patched file honouring the decisions.", "requirements", "impact_analysis", "design", "clarifications", seed="### baseline code\n" + BASELINE_CODE),
        "developer_fallback": reads("developer_fallback", "Return the SMALLEST patched file honouring the decisions.", "requirements", "impact_analysis", "design", "clarifications", seed="### baseline code\n" + BASELINE_CODE),
        "tester": reads("tester", "Return the full test file: keep all existing tests, add tests for the decisions.", "requirements", "implementation", "clarifications", seed="### existing tests\n" + BASELINE_TESTS),
        "doc_writer": reads("doc_writer", f"Write a changelog entry for {TICKET} recording every decision.", "implementation", "clarifications"),
        "reviewer": reads("reviewer", "Reply APPROVE or REJECT with reasons.", "implementation", "tests", "docs"),
        "release_manager": release_agent,
    }
    return graph, agents


# ----------------------------------------------------------------- mock behaviour and demo violations
DEFAULTS = {
    "requirements": read("requirements.json"),
    "analyst": read("impact_analysis.json"),
    "planner": read("plan.json"),
    "architect": lambda prompt: render_design(decisions_from_prompt(prompt)),
    "developer": lambda prompt: render_code(decisions_from_prompt(prompt)),
    "developer_fallback": lambda prompt: render_code(decisions_from_prompt(prompt)),
    "tester": lambda prompt: render_tests(decisions_from_prompt(prompt)),
    "doc_writer": lambda prompt: render_changelog(*decisions_with_who(prompt)),
}


def _bad_code_ignores_decision(prompt):
    code = render_code(decisions_from_prompt(prompt))
    return _replace(code, "DEFAULT_TTL_S = ", "DEFAULT_TTL_S = 7 * 24 * 3600  # (ignores the decision)\nDEFAULT_TTL_S_IGNORED = ")


def _bad_code_scope_creep(prompt):
    return _replace(render_code(decisions_from_prompt(prompt)), "if len(self._hits) > 10_000:", "if len(self._hits) > 5_000:")


def _bad_tests_untraced(prompt):
    text = render_tests(decisions_from_prompt(prompt))
    start = text.index("def test_default_ttl_links_expire")
    end = text.index("def test_explicit_ttl_overrides_the_default")
    return text[:start] + text[end:]


def _bad_changelog_no_ticket(prompt):
    return render_changelog(*decisions_with_who(prompt)).replace(TICKET, "FEAT-0")


VIOLATIONS = {
    "ignores-decision": ("developer", _bad_code_ignores_decision, False),
    "scope-creep": ("developer", _bad_code_scope_creep, False),
    "untraced-tests": ("tester", _bad_tests_untraced, False),
    "changelog-no-ticket": ("doc_writer", _bad_changelog_no_ticket, False),
    "plan-cycle": ("planner", pf.plan_cycle(DEFAULTS["planner"]), False),
    "plan-misses-criterion": ("planner", pf.plan_misses_criterion(DEFAULTS["planner"]), False),
    "plan-wrong-order": ("planner", pf.plan_wrong_order(DEFAULTS["planner"]), False),
}


def make_llm(fail_times=None, scripted=None):
    return mc.MockLLM(fail_times=fail_times, scripted=scripted, defaults=DEFAULTS)

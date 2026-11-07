"""A SIMPLE stateful SDLC graph, in the style of a LangGraph app.

Read this file top to bottom. It has five parts:

  1. STATE    - one shared dictionary ("blackboard") every worker reads/writes
  2. NODES    - the workers; each takes the state and returns what it CHANGED
  3. ROUTERS  - tiny functions that look at the state and choose the next step
  4. GRAPH    - plain tables: which node follows which (this is the "map")
  5. RUNNERS  - run_plain() (15 lines, shows what a graph engine does) and
                build_langgraph() (same map, executed by LangGraph)

Workers reuse the pieces already built: MockLLM, security policy, test sandbox.
"""
from __future__ import annotations

import operator
from typing import Annotated, Callable, TypedDict

from orchestrator.governance import policy_engine, sandbox

END = "__end__"
MAX_RETRIES = 3            # developer retries after test failure (1 try + 3 retries)
MAX_DESIGN_REVISIONS = 2   # times a human may send the design back


# ----------------------------------------------------------------------------
# 1. STATE: the shared blackboard
# ----------------------------------------------------------------------------
class SDLCState(TypedDict, total=False):
    raw_requirement: str
    requirements: str
    design: str
    approved: bool
    design_revisions: int
    code: str
    tests: str
    test_log: str            # failure details, fed back to the developer
    tests_passed: bool
    retries: int
    docs: str
    status: str              # RUNNING | DONE | HALTED
    halt_reason: str
    # Annotated[..., operator.add] means: "when a node returns audit_trail,
    # APPEND it to the existing list instead of replacing it" (append-only log).
    audit_trail: Annotated[list, operator.add]


def initial_state(raw_requirement: str) -> SDLCState:
    return {"raw_requirement": raw_requirement, "design_revisions": 0, "retries": 0,
            "status": "RUNNING", "audit_trail": []}


def _log(node: str, event: str, **detail) -> list[dict]:
    return [{"node": node, "event": event, **detail}]


# ----------------------------------------------------------------------------
# 2. NODES: the workers. Each returns ONLY the keys it wants to change.
# ----------------------------------------------------------------------------
def make_nodes(llm, approver: Callable[[str], bool]) -> dict[str, Callable[[SDLCState], dict]]:
    def analyst(s):
        out = llm.complete("requirements", s["raw_requirement"])
        return {"requirements": out, "audit_trail": _log("analyst", "requirements normalized")}

    def architect(s):
        out = llm.complete("architect", s["requirements"])
        return {"design": out, "audit_trail": _log("architect", "design produced",
                                                   revision=s["design_revisions"])}

    def human_approval(s):
        ok = approver(s["design"])                        # a HUMAN decides here
        update = {"approved": ok, "audit_trail": _log("human_approval", "approved" if ok else "rejected")}
        if not ok:
            update["design_revisions"] = s["design_revisions"] + 1
        return update

    def developer(s):
        feedback = f"\nPrevious test failures:\n{s['test_log']}" if s.get("test_log") else ""
        code = llm.complete("developer", s["design"] + feedback)
        return {"code": code, "audit_trail": _log("developer", "code written", attempt=s["retries"] + 1)}

    def qa_tester(s):
        tests = llm.complete("tester", s["requirements"])
        violations = policy_engine.check_security(s["code"])
        if violations:
            passed, log = False, "SECURITY: " + "; ".join(violations)
        else:
            r = sandbox.run_tests(s["code"], tests)
            passed = bool(r["passed"]) and not r["failed"]
            log = "\n".join(f"{name}: {tb}" for name, tb in r["failed"].items())
        return {"tests": tests, "tests_passed": passed, "test_log": log,
                "retries": s["retries"] + (0 if passed else 1),
                "audit_trail": _log("qa_tester", "tests passed" if passed else "tests FAILED",
                                    detail=log[:200])}

    def release(s):
        docs = llm.complete("doc_writer", s["design"] + s["code"])
        return {"docs": docs, "status": "DONE", "audit_trail": _log("release", "docs generated, release ready")}

    def halt(s):
        why = ("design rejected too many times" if not s.get("approved", True)
               else f"tests still failing after {s['retries']} failures")
        return {"status": "HALTED", "halt_reason": why, "audit_trail": _log("halt", "safe stop", reason=why)}

    return {"analyst": analyst, "architect": architect, "human_approval": human_approval,
            "developer": developer, "qa_tester": qa_tester, "release": release, "halt": halt}


# ----------------------------------------------------------------------------
# 3. ROUTERS: look at the state, return a LABEL (not a node name)
# ----------------------------------------------------------------------------
def route_after_approval(s: SDLCState) -> str:
    if s["approved"]:
        return "approved"
    return "rejected" if s["design_revisions"] <= MAX_DESIGN_REVISIONS else "halt"


def route_after_qa(s: SDLCState) -> str:
    if s["tests_passed"]:
        return "pass"
    return "retry" if s["retries"] <= MAX_RETRIES else "halt"      # <- the bounded cycle


# ----------------------------------------------------------------------------
# 4. GRAPH: the map, as plain data
# ----------------------------------------------------------------------------
ENTRY = "analyst"
EDGES = {                       # always go from A to B
    "analyst": "architect",
    "architect": "human_approval",
    "developer": "qa_tester",
    "release": END,
    "halt": END,
}
ROUTES = {                      # go wherever the router's label says
    "human_approval": (route_after_approval,
                       {"approved": "developer", "rejected": "architect", "halt": "halt"}),
    "qa_tester": (route_after_qa,
                  {"pass": "release", "retry": "developer", "halt": "halt"}),
}


# ----------------------------------------------------------------------------
# 5. RUNNERS
# ----------------------------------------------------------------------------
def run_plain(nodes, state: SDLCState, max_steps: int = 60) -> SDLCState:
    """What a graph engine does, in a few lines: run node -> merge -> pick next."""
    state = dict(state)
    current = ENTRY
    for _ in range(max_steps):                       # hard cap = safe-stop against infinite loops
        update = nodes[current](state)
        for key, value in update.items():
            state[key] = state[key] + value if key == "audit_trail" else value
        if current in ROUTES:
            router, mapping = ROUTES[current]
            current = mapping[router(state)]
        else:
            current = EDGES[current]
        if current == END:
            return state
    raise RuntimeError("max_steps exceeded (possible infinite loop)")


def build_langgraph(nodes):
    """Same map, executed by LangGraph. Requires `pip install langgraph`."""
    from langgraph.graph import END as LG_END, START, StateGraph

    fix = lambda target: LG_END if target == END else target
    g = StateGraph(SDLCState)
    for name, fn in nodes.items():
        g.add_node(name, fn)
    g.add_edge(START, ENTRY)
    for src, dst in EDGES.items():
        g.add_edge(src, fix(dst))
    for src, (router, mapping) in ROUTES.items():
        g.add_conditional_edges(src, router, {label: fix(t) for label, t in mapping.items()})
    return g.compile()


def run_langgraph(nodes, state: SDLCState) -> SDLCState:
    return build_langgraph(nodes).invoke(state, {"recursion_limit": 60})

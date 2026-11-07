"""python -m simple_graph.demo <scenario> [--engine plain|langgraph]

Scenarios: happy | fix_loop | security_loop | always_bad | reject_once | reject_always
"""
from __future__ import annotations

import argparse

from orchestrator.llm.mock_client import BAD_CODE_SECRET, GOOD_CODE, MockLLM

from .sdlc_graph import initial_state, make_nodes, run_langgraph, run_plain

BUGGY = '''import re


def slugify(text, max_length=80):
    if not isinstance(text, str):
        raise TypeError("text must be a str")
    if max_length < 1:
        raise ValueError("max_length must be >= 1")
    return re.sub(r"[^a-z0-9]+", "-", text.lower())[:max_length]   # BUG: no accents, no strip
'''

SCENARIOS = {  # name: (scripted developer answers, approvals in order; last repeats)
    "happy":         ([GOOD_CODE], [True]),
    "fix_loop":      ([BUGGY, GOOD_CODE], [True]),
    "security_loop": ([BAD_CODE_SECRET, GOOD_CODE], [True]),
    "always_bad":    ([BUGGY], [True]),
    "reject_once":   ([GOOD_CODE], [False, True]),
    "reject_always": ([GOOD_CODE], [False]),
}
REQUIREMENT = "Write a Python function slugify(text) that turns text into a URL-safe slug."


def make_approver(decisions, ask: bool = False):
    calls = iter(range(10**6))
    def approver(design: str) -> bool:
        i = next(calls)
        if ask:
            print("\nDESIGN TO APPROVE:\n" + design)
            return input("Approve? [y/N] ").strip().lower() in {"y", "yes"}
        return decisions[min(i, len(decisions) - 1)]
    return approver


def run_scenario(name: str, engine: str = "plain", ask: bool = False):
    dev_answers, approvals = SCENARIOS[name]
    llm = MockLLM(scripted={"developer": dev_answers})
    nodes = make_nodes(llm, make_approver(approvals, ask))
    runner = run_plain if engine == "plain" else run_langgraph
    return runner(nodes, initial_state(REQUIREMENT)), llm


def main(argv=None):
    p = argparse.ArgumentParser()
    p.add_argument("scenario", choices=SCENARIOS)
    p.add_argument("--engine", choices=["plain", "langgraph"], default="plain")
    p.add_argument("--ask", action="store_true", help="ask YOU to approve the design")
    a = p.parse_args(argv)
    final, _ = run_scenario(a.scenario, a.engine, a.ask)
    print(f"\n{'step':<4} {'node':<15} {'event':<26} detail")
    for i, e in enumerate(final["audit_trail"], 1):
        extra = {k: v for k, v in e.items() if k not in ("node", "event")}
        print(f"{i:<4} {e['node']:<15} {e['event']:<26} {extra or ''}")
    print(f"\nFINAL STATUS: {final['status']}  retries={final['retries']}  "
          f"design_revisions={final['design_revisions']}  {final.get('halt_reason', '')}")


if __name__ == "__main__":
    main()

"""Deliberately flawed plans, shared by the workflows that have a planning stage (used by `--violate`)."""
from __future__ import annotations

import json


def _first(tasks, node):
    return next(t for t in tasks if t["node"] == node)


def plan_cycle(plan_json: str) -> str:
    """The first task depends on the last one, which (transitively) depends on it: a cycle."""
    plan = json.loads(plan_json)
    tasks = plan["tasks"]
    tasks[0]["depends_on"] = [tasks[-1]["id"]]
    tasks[-1]["depends_on"] = list(dict.fromkeys(tasks[-1]["depends_on"] + [tasks[0]["id"]]))
    return json.dumps(plan, indent=2)


def plan_misses_criterion(plan_json: str) -> str:
    """Forgets to trace the third acceptance criterion."""
    plan = json.loads(plan_json)
    plan["traceability"] = [e for e in plan["traceability"] if e["criterion"] != "AC3"]
    return json.dumps(plan, indent=2)


def plan_wrong_order(plan_json: str) -> str:
    """Says the docs follow the tests, which the executable workflow does not guarantee."""
    plan = json.loads(plan_json)
    docs, tests = _first(plan["tasks"], "docs"), _first(plan["tasks"], "tests")
    docs["depends_on"] = list(dict.fromkeys(docs["depends_on"] + [tests["id"]]))
    return json.dumps(plan, indent=2)

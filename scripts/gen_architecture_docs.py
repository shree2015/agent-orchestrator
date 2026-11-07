"""Generate the parts of the architecture documentation that must never drift from the code.

    python scripts/gen_architecture_docs.py            # rewrite docs/architecture-reference.md and the generated blocks
    python scripts/gen_architecture_docs.py --check    # exit 1 if any generated text is out of date (a test runs this)

Everything produced here is READ FROM THE REAL CODE: the workflow graphs are built by the actual workflow modules, the controls
are the actual gates attached to each stage, the policy catalogue is discovered by calling every policy factory, and the event
catalogue is read from the engine's own audit calls. If someone adds a policy or an event without it appearing here, or an event
without a description in EVENT_MEANING below, generation fails loudly.
"""
from __future__ import annotations

import ast
import inspect
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from orchestrator.governance import gates  # noqa: E402
from orchestrator.governance.policies import (change_control, clarification, compliance, planning, security)  # noqa: E402
from orchestrator.governance.policies.base import Policy  # noqa: E402
from orchestrator.governance.policies.planning import GraphRef  # noqa: E402
from orchestrator.workflows import ambiguous, brownfield, shortener, slugify  # noqa: E402

DOCS = ROOT / "docs"
REFERENCE = DOCS / "architecture-reference.md"
ARCHITECTURE = DOCS / "architecture.md"
TRACEABILITY = DOCS / "requirements-traceability.md"

WORKFLOWS = [  # (id, module, title)
    ("shortener", shortener, "Greenfield: the URL shortener (with plan and web UI)"),
    ("brownfield", brownfield, "Brownfield: fixing BUG-142 in the existing service"),
    ("ambiguous", ambiguous, "Ambiguous: \"make links expire sensibly\""),
    ("slugify", slugify, "Toy: a small function (the cheapest real-model test)"),
]

EVENT_MEANING = {
    "run_started": "a run() call begins; lists the nodes still pending",
    "wave_started": "the nodes that are ready run in parallel; the wave ends at a barrier",
    "entry_gate": "an entry gate was evaluated before a node's agent ran",
    "exit_gates": "a node's exit gates were evaluated for one attempt (failures listed)",
    "attempt_failed": "one attempt was rejected or crashed; says whether a retry follows and any backoff",
    "fallback_engaged": "retries were exhausted and the node's fallback agent takes over",
    "approval": "a human approved or rejected a node's output",
    "node_committed": "an output passed every gate and was committed; records which agent produced it",
    "node_failed": "a node failed for good (this triggers a safe-stop)",
    "safe_stop": "the run stops starting new work; the first reason wins",
    "state_change": "a node changed state",
    "checkpoint": "the last good outputs of the affected nodes were saved before a change",
    "replan": "nodes were invalidated and will be redone; says whether the engine derived it (auto) or it was forced",
    "revision": "an upstream output was replaced (e.g. a human changed an answer); records old and new fingerprints",
    "revision_noop": "an identical revision: nothing to redo",
    "revision_rejected": "a revision failed a gate or approval and was refused",
    "change_rejected": "a change was refused by change control (no reason, or re-plan budget exceeded)",
    "rollback": "a failed change was reverted to its checkpoint",
    "change_committed": "a re-plan completed and its checkpoint was released",
    "run_finished": "a run() call ended; carries status, halt reason, latency and final states",
    "policy_lint": "the workflow definition itself was checked for mandatory controls before anything ran",
    "llm_call": "one real-model call: role, model, token counts, latency, prompt hash (never the prompt) and request id",
}

LINT_RULES = {
    "high-impact-requires-approval": "a node tagged high_impact must require human approval",
    "code-requires-security-gate": "a node tagged code must carry a security exit gate",
    "release-requires-compliance-gate": "a node tagged release must carry a compliance entry gate",
}

HEADER = ("<!-- GENERATED FILE: do not edit by hand. Regenerate with:  python scripts/gen_architecture_docs.py\n"
          "     A test fails if this file is out of date. Everything here is read from the real code. -->\n\n")


# ------------------------------------------------------------------------------------------------ workflow graphs
def build_graph(module):
    graph, _ = module.build(module.make_llm())
    return graph


def reduced_edges(graph) -> list[tuple[str, str]]:
    """Dependency edges without the redundant ones (A -> C is dropped when A -> B -> C exists)."""
    edges = []
    for node in graph.nodes.values():
        for dep in node.depends_on:
            if not any(dep in graph.ancestors(other) for other in node.depends_on if other != dep):
                edges.append((dep, node.id))
    return edges


def mermaid_graph(graph, direction: str = "TD") -> str:
    lines = [f"flowchart {direction}"]
    approval, code = [], []
    for n in graph.nodes.values():
        label = f"{n.id}<br/>{n.agent}" + (" + fallback" if n.fallback_agent else "")
        lines.append(f'  {n.id}{{{{"{label}"}}}}' if n.requires_approval else f'  {n.id}["{label}"]')
        (approval if n.requires_approval else code if "code" in n.tags else []).append(n.id)
    lines += [f"  {a} --> {b}" for a, b in reduced_edges(graph)]
    lines.append("  classDef approval fill:#fff3cd,stroke:#9a6700,color:#000")
    lines.append("  classDef code fill:#e7f0ff,stroke:#2557d6,color:#000")
    if approval:
        lines.append(f"  class {','.join(approval)} approval")
    if code:
        lines.append(f"  class {','.join(code)} code")
    return "\n".join(lines)


LEGEND = "Hexagons need a human approval; blue boxes produce code and carry a security gate; the arrows are dependencies (redundant ones omitted)."


def controls_table(graph) -> str:
    rows = ["| Stage | Agent | Entry controls | Exit controls (every one must pass) | Approval |", "|---|---|---|---|---|"]
    for n in graph.nodes.values():
        entry = "<br/>".join(c for g in n.entry_gates for c in gates.describe_gate(g)) or "-"
        exit_ = "<br/>".join(c for g in n.exit_gates for c in gates.describe_gate(g)) or "-"
        rows.append(f"| `{n.id}` | {n.agent}{' (+ ' + n.fallback_agent + ')' if n.fallback_agent else ''} | {entry} | {exit_} | "
                    f"{'human' if n.requires_approval else '-'} |")
    return "\n".join(rows)


# -------------------------------------------------------------------------------------------- policy catalogue
_DUMMY = {"baseline_code": "", "baseline_tests": "", "ticket": "TICKET", "ref": GraphRef(), "required_nodes": (), "rules": [],
          "mapping": {}, "required": ()}


def policy_registry() -> list[Policy]:
    """Call every public policy factory (anything annotated '-> Policy') with placeholder arguments."""
    found = []
    for mod in (security, compliance, change_control, planning, clarification):
        for name, fn in inspect.getmembers(mod, inspect.isfunction):
            if fn.__module__ != mod.__name__ or name.startswith("_"):
                continue
            sig = inspect.signature(fn)
            if sig.return_annotation not in (Policy, "Policy"):
                continue
            args = {p.name: _DUMMY[p.name] for p in sig.parameters.values() if p.default is inspect.Parameter.empty}
            found.append(fn(**args))
    ids = [(p.category, p.id) for p in found]
    assert len(ids) == len(set(ids)), "duplicate policy ids in the registry"
    return sorted(found, key=lambda p: (p.category, p.id))


def lint_rule_ids() -> set[str]:
    src = inspect.getsource(change_control.lint_graph)
    return set(re.findall(r'v\("([a-z-]+)"', src))


def policy_table() -> str:
    assert lint_rule_ids() == set(LINT_RULES), f"lint rules changed: update LINT_RULES ({lint_rule_ids() ^ set(LINT_RULES)})"
    rows = ["| Category | Policy id | What it enforces |", "|---|---|---|"]
    rows += [f"| {p.category} | `{p.id}` | {p.description} |" for p in policy_registry()]
    rows += [f"| change_control (workflow lint) | `{i}` | {d} |" for i, d in sorted(LINT_RULES.items())]
    return "\n".join(rows)


# ---------------------------------------------------------------------------------------------- event catalogue
def _event_calls(path: Path):
    """Yield (event name, field names) for every audit emission found in a source file."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    for node in ast.walk(tree):
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute) and node.func.attr == "_audit" and node.args:
            first = node.args[0]
            names = ([first.value] if isinstance(first, ast.Constant) and isinstance(first.value, str)
                     else [b.value for b in (first.body, first.orelse) if isinstance(b, ast.Constant)] if isinstance(first, ast.IfExp) else [])
            for name in names:                                              # "a" or  "a" if cond else "b"
                yield name, [k.arg for k in node.keywords if k.arg]
        elif isinstance(node, ast.Dict):                                   # audit({"ts": ..., "event": "x", ...})
            keys = {k.value: v for k, v in zip(node.keys, node.values) if isinstance(k, ast.Constant)}
            ev = keys.get("event")
            if isinstance(ev, ast.Constant) and isinstance(ev.value, str) and "ts" in keys:
                yield ev.value, [k for k in keys if k not in ("ts", "event")]


def event_table() -> str:
    sources = {"engine": ROOT / "orchestrator/core/engine.py", "runner": ROOT / "orchestrator/runner.py",
               "real LLM client": ROOT / "orchestrator/llm/anthropic_client.py"}
    events: dict[str, dict] = {}
    for who, path in sources.items():
        for name, fields in _event_calls(path):
            e = events.setdefault(name, {"by": set(), "fields": []})
            e["by"].add(who)
            e["fields"] += [f for f in fields if f not in e["fields"]]
    missing = sorted(set(events) - set(EVENT_MEANING))
    stale = sorted(set(EVENT_MEANING) - set(events))
    assert not missing and not stale, f"EVENT_MEANING is out of step with the code: undescribed {missing}, no longer emitted {stale}"
    rows = ["| Event | Emitted by | Fields | Meaning |", "|---|---|---|---|"]
    rows += [f"| `{n}` | {', '.join(sorted(e['by']))} | {', '.join(e['fields']) or '-'} | {EVENT_MEANING[n]} |" for n, e in sorted(events.items())]
    return "\n".join(rows)


# ------------------------------------------------------------------------------------------------------- metrics
def metrics_section() -> str:
    import orchestrator.observability.metrics as m
    doc = inspect.getdoc(m) or ""
    lines = [ln for ln in doc.splitlines() if ln.startswith("  * ")]
    tree = ast.parse(Path(m.__file__).read_text(encoding="utf-8"))
    keys = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Return) and isinstance(node.value, ast.Dict):
            keys = [k.value for k in node.value.keys if isinstance(k, ast.Constant)]
    out = ["Definitions (from the metrics module itself):", ""] + [f"- {ln[4:]}" for ln in lines]
    out += ["", "Every value returned by `compute()`: " + ", ".join(f"`{k}`" for k in keys) + "."]
    return "\n".join(out)


# ----------------------------------------------------------------------------------------------------- assembling
def graph_block(name: str) -> str:
    module = next(m for i, m, _ in WORKFLOWS if i == name)
    first, rest = mermaid_graph(build_graph(module), "LR").split("\n", 1)
    return f"```mermaid\n{first}\n  %% pdf: width=17cm\n{rest}\n```\n\n*{LEGEND}*"


def controls_compact(graph) -> str:
    """Stage | exit/entry controls, with the category prefix dropped and grouped in one cell (small enough for a printed page)."""
    short = lambda c: c.split("/", 1)[-1]
    rows = ["| Stage | Controls that must pass | Human |", "|---|---|---|"]
    for n in graph.nodes.values():
        ctl = [("entry: " if g in n.entry_gates else "") + short(c) for g in list(n.entry_gates) + list(n.exit_gates) for c in gates.describe_gate(g)]
        rows.append(f"| `{n.id}` | {', '.join(ctl) or '-'} | {'approval' if n.requires_approval else '-'} |")
    return "\n".join(rows)


def trace_block(name: str) -> str:
    """A worked example produced by REALLY running the greenfield workflow with a scripted policy violation."""
    import tempfile
    from orchestrator.admin.session import describe
    from orchestrator.cli import scripted_answers
    from orchestrator.governance import approvals
    from orchestrator.runner import run_workflow
    assert name == "trace-secret", f"unknown trace: {name}"
    llm = shortener.make_llm(scripted=scripted_answers(shortener, ["secret"]))
    graph, agents = shortener.build(llm)
    with tempfile.TemporaryDirectory() as td:
        res = run_workflow(graph, agents, approvals.auto_approver, run_root=td, run_id="trace")
        feed = [describe(e) for e in res["audit"].events]
    lines = [ln for ln in feed if ln and "implementation" in ln]
    m = res["metrics"]
    viol = ", ".join(f"{k}: {v}" for k, v in m["policy_violations"].items())
    return ("```text\n$ python -m orchestrator.cli run shortener --approve auto --violate secret\n" + "\n".join(lines)
            + f"\nrun status: {m['run_status']}   retries: {m['retries']}   policy violations: {viol}\n```")


def facts() -> dict[str, str]:
    """Numbers quoted in prose, so they cannot go stale."""
    return {"policies": str(len(policy_registry()) + len(LINT_RULES)), "policy-factories": str(len(policy_registry())),
            "events": str(len(EVENT_MEANING)), "workflows": str(len(WORKFLOWS))}


def render_reference() -> str:
    out = [HEADER, "# Architecture reference (generated)\n",
           "Companion to [architecture.md](architecture.md). Everything below is produced from the running code by "
           "`scripts/gen_architecture_docs.py`.\n"]
    out.append("## 1. Workflow graphs and the controls on every stage\n")
    for i, (name, module, title) in enumerate(WORKFLOWS, 1):
        graph = build_graph(module)
        out += [f"### 1.{i} {title}\n", f"```mermaid\n{mermaid_graph(graph)}\n```\n", f"*{LEGEND}*\n", controls_table(graph) + "\n"]
    out += ["## 2. Policy catalogue (discovered from the code)\n",
            "Each policy is a named rule; a violation always reads `[category/id] message`. Rationale and examples: "
            "[policies.md](policies.md).\n", policy_table() + "\n"]
    out += ["## 3. Audit event catalogue (read from the engine's own calls)\n",
            "Every event is a record in the hash-chained `audit.jsonl`; all metrics and the live activity feed are derived from them.\n",
            event_table() + "\n"]
    out += ["## 4. Reliability metrics\n", metrics_section() + "\n"]
    return "\n".join(out)


BLOCK = re.compile(r"(<!-- BEGIN GENERATED: (?P<name>[\w-]+) -->\n)(?P<body>.*?)(<!-- END GENERATED: (?P=name) -->)", re.S)


INLINE = re.compile(r"<!--GEN:(?P<name>[\w-]+)-->.*?<!--/GEN-->")


def render_architecture(text: str) -> str:
    known = facts()

    def fact(m):
        assert m.group("name") in known, f"unknown generated fact: {m.group('name')}"
        return f"<!--GEN:{m.group('name')}-->{known[m.group('name')]}<!--/GEN-->"
    text = INLINE.sub(fact, text)

    def swap(m):
        name = m.group("name")
        if name.startswith("trace-"):
            return f"{m.group(1)}{trace_block(name)}\n{m.group(4)}"
        if name.startswith("controls-"):
            module = next(mod for i, mod, _ in WORKFLOWS if i == name.removeprefix("controls-"))
            return f"{m.group(1)}{controls_compact(build_graph(module))}\n{m.group(4)}"
        assert name.startswith("graph-"), f"unknown generated block: {name}"
        return f"{m.group(1)}{graph_block(name.removeprefix('graph-'))}\n{m.group(4)}"
    return BLOCK.sub(swap, text)


def expected_outputs() -> dict[Path, str]:
    out = {REFERENCE: render_reference()}
    for path in (ARCHITECTURE, TRACEABILITY):                 # hand-written documents with generated blocks and inline facts
        if path.exists():
            out[path] = render_architecture(path.read_text(encoding="utf-8"))
    return out


def main(argv=None) -> int:
    check = "--check" in (argv or sys.argv[1:])
    stale = []
    for path, text in expected_outputs().items():
        current = path.read_text(encoding="utf-8") if path.exists() else None
        if current != text:
            stale.append(path.relative_to(ROOT))
            if not check:
                path.write_text(text, encoding="utf-8")
    if check:
        if stale:
            print("OUT OF DATE (run: python scripts/gen_architecture_docs.py):", *stale)
        return 1 if stale else 0
    print("updated:", *(str(p) for p in stale)) if stale else print("already up to date")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

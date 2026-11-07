"""Runs a workflow and persists audit log, metrics, artifacts and a markdown report."""
from __future__ import annotations

import json
import time
import uuid
from pathlib import Path

from .core.engine import Engine
from .core.state import RunStatus
from .governance.policies import GovernanceError
from .governance.policies.change_control import lint_graph
from .observability import metrics as metrics_mod
from .observability.audit_log import AuditLog

EXT = {"implementation": "py", "tests": "py", "requirements": "json", "ui": "html", "impact_analysis": "json",
       "clarifications": "json", "plan": "json"}


# Events worth showing in the report's governance timeline.
TIMELINE = {"attempt_failed", "fallback_engaged", "checkpoint", "replan", "rollback",
            "change_committed", "change_rejected", "approval", "safe_stop",
            "revision", "revision_noop", "revision_rejected", "policy_lint", "rework"}


def run_workflow(graph, agents, approver, run_root: str | Path = "runs", run_id: str | None = None,
                 revisions=(), edits=(), on_replan=None, assets=None, llm=None, on_start=None, run_config=None, **engine_kwargs) -> dict:
    """Run a workflow; optionally apply upstream revisions afterwards (dynamic re-planning).

    assets: {filename: text} of baseline files the change did not touch; written next to the artifacts so the output
    folder is a complete, runnable deliverable (e.g. brownfield changes keep the existing web UI).
    edits: iterable of (node_id, new_content_or_callable(engine), reason[, by]) - each calls engine.revise(): the changed
    output is fingerprinted and ONLY the nodes whose inputs changed are redone (an identical edit changes nothing).
    run_config: the operator's settings for this run (mode, model, size limits...), recorded as the first audit event. It must never
    contain secrets.
    on_start: called as on_start(engine, audit, run_dir) right after the engine exists and before it runs, so a caller
    (the admin page) can watch the live engine.
    revisions: iterable of (node_id, reason, before_rerun) - after the run completes, the engine
    invalidates node_id (checkpointing downstream outputs), calls before_rerun() (e.g. to change
    agent behaviour), and runs again. A failed re-run is rolled back to the checkpoint.
    """
    run_id = run_id or f"{time.strftime('run-%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    run_dir = Path(run_root) / run_id
    audit = AuditLog(run_dir / "audit.jsonl")
    if run_config:
        audit({"ts": time.time(), "event": "run_config", "config": run_config})
    # Governance applies to the workflow itself: a definition that drops a mandatory control never runs.
    lint = lint_graph(graph)
    audit({"ts": time.time(), "event": "policy_lint", "passed": not lint, "violations": [str(v) for v in lint]})
    if lint:
        raise GovernanceError(lint)
    if llm is not None and hasattr(llm, "bind_audit"):
        llm.bind_audit(audit)          # real clients log every call (tokens, latency, prompt hash) into the audit chain
    engine = Engine(graph, agents, approver=approver, audit=audit, **engine_kwargs)
    if on_start:
        on_start(engine, audit, run_dir)
    status = engine.run()
    revision_results = []
    for node_id, reason, before_rerun in revisions:
        if status is not RunStatus.COMPLETED:
            break                      # never re-plan on top of a halted run
        if not engine.invalidate(node_id, reason):
            continue                   # change rejected by change control: system stays in last good state
        if before_rerun:
            before_rerun()
        status = engine.run()
    for edit in edits:                 # automatic re-planning: a changed upstream output redoes only what it affects
        if status is not RunStatus.COMPLETED:
            break
        node_id, content, reason, *rest = edit
        if callable(content):
            content = content(engine)
        result = engine.revise(node_id, content, reason, rest[0] if rest else "human")
        revision_results.append(result)
        if result.changed:
            if on_replan:
                on_replan()            # demos use this to make the re-run misbehave (e.g. to show rollback)
            status = engine.run()

    m = write_outputs(run_dir, run_id, engine, audit, assets)
    return {"run_id": run_id, "run_dir": run_dir, "status": status, "metrics": m, "engine": engine, "audit": audit,
            "revisions": revision_results}


def write_outputs(run_dir: Path, run_id: str, engine, audit, assets=None) -> dict:
    """(Re)write metrics.json, the artifacts folder and report.md from the engine's current state; returns the metrics.

    Safe to call again after a later revision: it simply reflects the latest state.
    """
    m = metrics_mod.compute(audit.events)
    (run_dir / "metrics.json").write_text(json.dumps(m, indent=2), encoding="utf-8")
    art = run_dir / "artifacts"
    art.mkdir(exist_ok=True)
    for node_id, out in engine.context.items():
        (art / f"{node_id}.{EXT.get(node_id, 'md')}").write_text(out["content"], encoding="utf-8")
    for stale in art.glob("REJECTED-*"):               # the last rejected output of each failed stage, so a halted run can be diagnosed
        stale.unlink()
    for node_id, out in engine.rejected.items():
        text = out["content"] if isinstance(out, dict) and "content" in out else str(out)
        (art / f"REJECTED-{node_id}.{EXT.get(node_id, 'md')}").write_text(str(text), encoding="utf-8")
    for name, content in (assets or {}).items():       # unchanged files of the existing codebase (e.g. the web UI)
        if not (art / name).exists():                  # never overwrite something an agent produced
            (art / name).write_text(content, encoding="utf-8")
    (run_dir / "report.md").write_text(render_report(run_id, engine, m, audit) + (
        "\n## Baseline files carried over unchanged\n" + "\n".join(f"- {n}" for n in sorted(assets)) + "\n" if assets else ""), encoding="utf-8")
    return m


def render_report(run_id, engine, m, audit) -> str:
    rows = "\n".join(f"| {n} | {s.value} | {engine.attempts[n]} | {engine.producer.get(n, '-')} |"
                     for n, s in engine.state.items())
    timeline = "\n".join(f"- #{e['seq']} `{e['event']}` " + ", ".join(
        f"{k}={v}" for k, v in e.items() if k not in ("ts", "seq", "prev_hash", "hash", "event"))
        for e in audit.events if e["event"] in TIMELINE)
    lineage = "\n".join(f"- **{n}** <- {', '.join(o['derived_from']) or '(requirement)'}"
                        for n, o in ((n, engine.context[n]) for n in engine.graph.nodes if n in engine.context))
    flat = lambda v: ", ".join(f"{a}: {b}" for a, b in v.items()) or "-" if isinstance(v, dict) else v
    mrows = "\n".join(f"| {k} | {flat(v)} |" for k, v in m.items())
    ok = AuditLog.verify(audit.path)
    return (f"# Run report: {run_id}\n\n**Status:** {m['run_status']}  \n"
            f"**Halt reason:** {m['halt_reason'] or '-'}  \n**Audit chain intact:** {ok}\n\n"
            f"## Nodes\n| Node | State | Attempts | Produced by |\n|---|---|---|---|\n{rows}\n\n"
            f"## Governance timeline\n{timeline or '- (no retries, fallbacks, re-plans or rollbacks)'}\n\n"
            + ("## Rejected outputs\n" + "\n".join(f"- `{n}`: the last rejected output is saved as `artifacts/REJECTED-{n}.{EXT.get(n, 'md')}`"
                                                    for n in engine.rejected) + "\n\n" if engine.rejected else "") +
            f"## Decision lineage\n{lineage}\n\n## Reliability metrics\n| Metric | Value |\n|---|---|\n{mrows}\n")

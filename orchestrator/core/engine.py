"""Stateful DAG execution engine.

Execution model
---------------
* The engine repeatedly finds nodes whose dependencies are all DONE and runs
  that whole "wave" in parallel (thread pool). A node with several parents is
  only released once ALL parents are DONE -> natural synchronization point.
* Per node: entry gates -> agent (bounded retries) -> exit gates -> optional
  human approval -> commit output to shared context.
* Outputs are committed to the context ONLY after every check passes, so a
  failed node never pollutes downstream context (cheap, safe "rollback").
* Any unrecoverable failure, rejection, or exceeded budget triggers SAFE-STOP:
  no new nodes start, unstarted descendants are SKIPPED.
* Automatic re-planning works like an incremental build: every committed output
  has a content fingerprint, and every node remembers the fingerprints of all the
  upstream outputs it consumed. revise() replaces an upstream output (a human
  answer, an amended requirement); the engine compares fingerprints, finds exactly
  the nodes whose inputs changed, checkpoints them and re-runs only those. An
  identical revision changes nothing (no churn). Revisions obey the same governance
  as normal work: exit gates, a stated reason, a re-plan budget, checkpoint/rollback.
* invalidate() is the manual form: force a node and everything downstream to redo.
  Either way the last good outputs are CHECKPOINTED first and the next run() redoes the work.
* Resilience ladder for a failing node:
    retry (bounded, exponential backoff) -> fallback agent (same exit gates)
    -> safe-stop, then ROLLBACK of the whole change set to the checkpoint.
  A re-plan is all-or-nothing: if the revised change cannot complete, every
  node it touched is restored to its last good output (state ROLLED_BACK).
  A failure on the very first run has nothing to roll back to, and nothing was
  committed, so the context stays clean by construction.

TODO (kept out on purpose): persistent state / resume after a crash, an
event-driven scheduler instead of waves.
"""
from __future__ import annotations

import hashlib
import json
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .graph import Graph, Node
from .state import NodeState, RunStatus

Agent = Callable[[Mapping[str, Any]], Any]          # context -> output
Approver = Callable[[Node, Any], bool]              # (node, output) -> approved?
AuditSink = Callable[[dict], None]


def fingerprint_of(output: Any) -> str:
    """Stable content fingerprint of a node output (dict outputs are fingerprinted by their content)."""
    value = output.get("content") if isinstance(output, dict) and "content" in output else output
    return hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class Revision:
    """Outcome of Engine.revise(): accepted?  did anything change?  which nodes must be redone?"""
    accepted: bool
    changed: bool = False
    affected: frozenset = frozenset()
    why: str = ""


class Engine:
    def __init__(
        self,
        graph: Graph,
        agents: dict[str, Agent],
        approver: Approver | None = None,
        audit: AuditSink | None = None,
        max_workers: int = 4,
        max_total_attempts: int = 50,   # safe-stop budget across the whole run
        backoff_base_s: float = 0.0,    # delay after 1st failure; doubles each retry (0 = off)
        backoff_max_s: float = 5.0,
        max_replans: int = 3,           # change control: bound how often upstream changes may churn the run
        max_reworks: int = 2,           # change control: bound how often review findings may send work back
        sleep: Callable[[float], None] = time.sleep,   # injectable so tests need no real waiting
    ) -> None:
        graph.validate()
        needed = {n.agent for n in graph.nodes.values()}
        needed |= {n.fallback_agent for n in graph.nodes.values() if n.fallback_agent}
        missing = needed - set(agents)
        if missing:
            raise ValueError(f"no agent registered for: {sorted(missing)}")
        self.graph = graph
        self.agents = agents
        self.approver = approver or (lambda node, output: False)  # deny by default
        self._audit_sink = audit or (lambda event: None)
        self.max_workers = max_workers
        self.max_total_attempts = max_total_attempts
        self.backoff_base_s = backoff_base_s
        self.backoff_max_s = backoff_max_s
        self._sleep = sleep
        self.max_replans = max_replans
        self._replans = 0
        self.max_reworks = max_reworks
        self._reworks = 0
        self._rework_notes: dict[str, dict] = {}       # node_id -> {"error": the findings, "previous": its last output}, consumed by its next run

        self.state: dict[str, NodeState] = {n: NodeState.PENDING for n in graph.nodes}
        self.context: dict[str, Any] = {}      # node_id -> committed output
        self.rejected: dict[str, Any] = {}      # node_id -> the last output that was REJECTED (kept for diagnosis; cleared when the node commits)
        self.producer: dict[str, str] = {}     # node_id -> agent key that produced the committed output
        self.fingerprint: dict[str, str] = {}  # node_id -> fingerprint of the committed output
        self._consumed: dict[str, dict[str, str]] = {}   # node_id -> {ancestor: fingerprint it saw}
        self._checkpoint: dict[str, tuple] | None = None   # last good (output, producer, fingerprint, consumed)
        self.attempts: dict[str, int] = {n: 0 for n in graph.nodes}
        self._total_attempts = 0
        self._lock = threading.Lock()
        self._halt = threading.Event()
        self.halt_reason: str | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------
    def run(self) -> RunStatus:
        started = time.monotonic()
        stale = self.stale_nodes()
        if stale:                                   # safety net: never run on top of out-of-date results
            self._replan_nodes(set(stale), trigger="stale-detected", reason="inputs changed since these nodes ran", auto=True)
        self._audit("run_started", pending=self._ids(NodeState.PENDING))
        with ThreadPoolExecutor(max_workers=self.max_workers) as pool:
            while not self._halt.is_set():
                ready = self._ready_nodes()
                if not ready:
                    break
                self._audit("wave_started", nodes=[n.id for n in ready])
                futures = [pool.submit(self._execute_node, n) for n in ready]
                for f in futures:      # barrier: wait for the whole wave
                    f.result()

        status = self._final_status()
        if status is RunStatus.HALTED:
            self._rollback()          # restore last good outputs BEFORE marking the rest skipped
            self._skip_unstarted()
        elif status is RunStatus.COMPLETED and self._checkpoint is not None:
            self._audit("change_committed", nodes=sorted(self._checkpoint))
            self._checkpoint = None
        self._audit(
            "run_finished",
            status=status.value,
            halt_reason=self.halt_reason,
            latency_s=round(time.monotonic() - started, 4),
            states={k: v.value for k, v in self.state.items()},
        )
        return status

    def invalidate(self, node_id: str, reason: str) -> set[str]:
        """Dynamic re-plan: an upstream output changed, redo it and its dependents.

        Change control: a re-plan needs a stated reason and is bounded by `max_replans`. A rejected
        change returns an empty set and leaves the system untouched in its last good state.
        """
        if node_id not in self.graph.nodes:
            raise KeyError(f"unknown node: {node_id}")
        why = None
        if not (reason or "").strip():
            why = "a re-plan requires a stated reason"
        elif self._replans >= self.max_replans:
            why = f"re-plan budget exceeded ({self.max_replans})"
        if why:
            self._audit("change_rejected", trigger=node_id, reason=reason, why=why)
            return set()
        self._replans += 1
        affected = {node_id} | self.graph.downstream(node_id)
        self._replan_nodes(affected, trigger=node_id, reason=reason, auto=False)
        return affected

    def stale_nodes(self) -> dict[str, list[str]]:
        """DONE nodes whose recorded upstream fingerprints no longer match the current upstream outputs."""
        stale: dict[str, list[str]] = {}
        for nid, st in self.state.items():
            if st is not NodeState.DONE or nid not in self._consumed:
                continue
            changed = [a for a in sorted(self.graph.ancestors(nid))
                       if a in self.fingerprint and self._consumed[nid].get(a) != self.fingerprint[a]]
            if changed:
                stale[nid] = changed
        return stale

    def revise(self, node_id: str, content: Any, reason: str, by: str = "human") -> Revision:
        """Replace a committed output (e.g. a human answer) and automatically re-plan what it affects.

        Only nodes whose upstream fingerprints changed are redone. Governance still applies: a stated reason,
        the re-plan budget, the node's exit gates, and (for agent-made revisions) human approval. Anything
        rejected leaves the system untouched; anything that fails during the re-run is rolled back.
        """
        if node_id not in self.graph.nodes:
            raise KeyError(f"unknown node: {node_id}")
        node = self.graph.nodes[node_id]
        if node_id not in self.context or self.state[node_id] not in (NodeState.DONE, NodeState.ROLLED_BACK):
            return self._reject_revision(node_id, reason, "only a node with a committed output can be revised", rejected_change=True)
        if not (reason or "").strip():
            return self._reject_revision(node_id, reason, "a revision requires a stated reason", rejected_change=True)
        old = self.context[node_id]
        new = ({**old, "content": content, "role": by, "derived_from": [*old.get("derived_from", []), f"revision by {by}"]}
               if isinstance(old, dict) else content)
        new_fp = fingerprint_of(new)
        old_fp = self.fingerprint.get(node_id) or fingerprint_of(old)
        if new_fp == old_fp:
            self._audit("revision_noop", node=node_id, by=by, reason=reason, fingerprint=old_fp[:12])
            return Revision(True, False, why="identical to the current output; nothing to redo")
        if self._replans >= self.max_replans:
            return self._reject_revision(node_id, reason, f"re-plan budget exceeded ({self.max_replans})", rejected_change=True)
        ctx: Mapping[str, Any] = MappingProxyType(dict(self.context))
        failures = []
        for gate in node.exit_gates:
            try:
                ok, why = gate(ctx, new)
            except Exception as exc:                  # fail closed: a crashing check is a rejection
                ok, why = False, f"gate crashed: {type(exc).__name__}: {exc}"
            if not ok:
                failures.append(why)
        if failures:
            return self._reject_revision(node_id, reason, "; ".join(failures))
        if node.requires_approval and by != "human":      # a human edit is itself the approval
            if not self.approver(node, new):
                self._audit("approval", node=node_id, approved=False)
                return self._reject_revision(node_id, reason, "revision not approved")
        self._replans += 1
        for nid, st in self.state.items():            # a new change starts from the last good state:
            if st is NodeState.ROLLED_BACK and nid in self.context:       # restored outputs count as settled again
                self.state[nid] = NodeState.DONE
        self._snapshot({node_id} | self.graph.downstream(node_id), trigger=node_id)
        with self._lock:
            self.context[node_id] = new
            self.producer[node_id] = by
            self.fingerprint[node_id] = new_fp
            self.state[node_id] = NodeState.DONE
        self._audit("revision", node=node_id, by=by, reason=reason, old=old_fp[:12], new=new_fp[:12])
        stale = self.stale_nodes()
        self._replan_nodes(set(stale), trigger=node_id, reason=reason, auto=True,
                           why=f"output of '{node_id}' changed ({old_fp[:8]} -> {new_fp[:8]})")
        return Revision(True, True, frozenset(stale))

    def _reject_revision(self, node_id: str, reason: str, why: str, rejected_change: bool = False) -> Revision:
        self._audit("change_rejected" if rejected_change else "revision_rejected", trigger=node_id, reason=reason, why=why)
        return Revision(False, why=why)

    def _snapshot(self, nodes: set[str], trigger: str) -> None:
        """Checkpoint the last GOOD outputs so a failed re-plan can be rolled back (oldest checkpoint wins)."""
        snap = {n: (self.context[n], self.producer.get(n, "?"), self.fingerprint.get(n), self._consumed.get(n, {}))
                for n in nodes if n in self.context}
        if self._checkpoint is None:
            self._checkpoint = snap
        else:
            for n, saved in snap.items():
                self._checkpoint.setdefault(n, saved)
        if snap:
            self._audit("checkpoint", trigger=trigger, nodes=sorted(snap))

    def _replan_nodes(self, nodes: set[str], trigger: str, reason: str, auto: bool, why: str = "") -> None:
        self._snapshot(nodes, trigger)
        for nid in nodes:
            self.state[nid] = NodeState.PENDING
            for table in (self.context, self.producer, self.fingerprint, self._consumed):
                table.pop(nid, None)
        self._halt.clear()
        self.halt_reason = None
        self._audit("replan", trigger=trigger, reason=reason, invalidated=sorted(nodes), auto=auto, why=why)

    # ------------------------------------------------------------------
    # Internals
    # ------------------------------------------------------------------
    def _ready_nodes(self) -> list[Node]:
        return [
            n for nid, n in self.graph.nodes.items()
            if self.state[nid] is NodeState.PENDING
            and all(self.state[d] is NodeState.DONE for d in n.depends_on)
        ]

    def _execute_node(self, node: Node) -> None:
        if self._halt.is_set():
            return
        self._set(node.id, NodeState.RUNNING)
        ctx: Mapping[str, Any] = MappingProxyType(dict(self.context))  # read-only snapshot

        # 1. entry gates
        for gate in node.entry_gates:
            ok, why = gate(ctx)
            self._audit("entry_gate", node=node.id, passed=ok, reason=why)
            if not ok:
                return self._fail(node, f"entry gate failed: {why}")

        # 2. primary agent with bounded retries + backoff (exit gates are part of "success")
        used = node.agent
        note = self._rework_notes.pop(node.id, None)        # a reviewer sent this node back: its first attempt is told what was found
        output, last_error, aborted, last_output = self._run_agent(
            node, node.agent, ctx, node.max_retries + 1, "primary", *( (note["error"], note["previous"]) if note else () ))
        if aborted:
            return

        # 2b. fallback agent: an alternate strategy, judged by the SAME exit gates
        if last_error and node.fallback_agent:
            self._audit("fallback_engaged", node=node.id, fallback_agent=node.fallback_agent,
                        primary_error=last_error)
            used = node.fallback_agent
            output, last_error, aborted, last_output = self._run_agent(
                node, node.fallback_agent, ctx, node.fallback_attempts, "fallback", last_error, last_output)
            if aborted:
                return
        if last_error:
            if last_output is not None:
                self.rejected[node.id] = last_output
            if node.rework is not None and last_output is not None and node.rework.when(str(last_output)):
                if self._send_back(node, str(last_output)):
                    return
            kind = "retries and fallback exhausted" if node.fallback_agent else "retries exhausted"
            return self._fail(node, f"{kind}: {last_error}")

        # 3. human approval for high-impact nodes
        if node.requires_approval:
            self._set(node.id, NodeState.AWAITING_APPROVAL)
            approved = self.approver(node, output)
            self._audit("approval", node=node.id, approved=approved)
            if not approved:
                self.rejected[node.id] = output
                self._set(node.id, NodeState.REJECTED)
                return self._safe_stop(f"{node.id} rejected by human")

        # 4. commit (output + which agent produced it: part of decision lineage)
        with self._lock:
            self.context[node.id] = output
            self.rejected.pop(node.id, None)
            self.producer[node.id] = used
            self.fingerprint[node.id] = fingerprint_of(output)
            self._consumed[node.id] = {a: self.fingerprint[a] for a in self.graph.ancestors(node.id) if a in self.fingerprint}
        self._audit("node_committed", node=node.id, agent=used, fallback=used != node.agent)
        self._set(node.id, NodeState.DONE)

    def _send_back(self, node: Node, findings: str) -> bool:
        """A reviewer's rejection is not a malformed answer to retry: it is a verdict on earlier work. Redo that work with the findings.

        Bounded by `max_reworks`; the redo is an ordinary change set (checkpointed, so a rework that fails is rolled back whole).
        """
        target = node.rework.target
        short = " ".join(findings.split())[:400]
        if self._reworks >= self.max_reworks:
            self._audit("rework", node=node.id, target=target, granted=False, round=self._reworks, of=self.max_reworks,
                        why=f"rework budget exhausted ({self.max_reworks})", findings=short)
            return False
        self._reworks += 1
        affected = {target} | self.graph.downstream(target)
        note = ("A reviewer rejected the finished work. Fix exactly the blocking problems in its findings that concern YOUR artifact and "
                "keep everything else as it is. If a finding concerns another artifact, ignore it (that one is being fixed too).\n"
                "Reviewer's findings:\n" + findings)
        with self._lock:
            for nid in affected:
                if nid != node.id and nid in self.context:       # every generating stage that is redone is told, with its own previous output
                    self._rework_notes[nid] = {"error": note, "previous": self.context[nid].get("content") if isinstance(self.context[nid], dict) else self.context[nid]}
        self._audit("rework", node=node.id, target=target, granted=True, round=self._reworks, of=self.max_reworks, findings=short,
                    redo=sorted(affected))
        self._replan_nodes(affected, trigger=node.id, reason=f"reviewer findings (rework {self._reworks} of {self.max_reworks})", auto=True,
                           why="the reviewer rejected the work; findings sent back to the stages that produced it")
        return True

    def _run_agent(self, node: Node, agent_key: str, ctx: Mapping[str, Any], max_attempts: int, stage: str,
                   error: str = "", previous: Any = None) -> tuple[Any, str, bool, Any]:
        """Run one agent up to max_attempts times. Returns (output, error, aborted, previous_output).

        error == "" means success. aborted=True means the global budget safe-stopped the run. `error`/`previous`
        seed the feedback for the first attempt, so a fallback agent learns why the primary failed.
        """
        for i in range(1, max_attempts + 1):
            if not self._consume_budget(node):
                return None, "attempt budget exceeded", True, previous
            self.attempts[node.id] += 1
            n = self.attempts[node.id]
            # On a retry the agent is told WHY it was rejected (a real model would otherwise repeat the mistake).
            # Only the agent sees this; gates always judge against the real, committed context.
            agent_ctx = ctx if not error else MappingProxyType(
                {**ctx, "__feedback__": {"content": error, "previous": previous, "role": "gate", "derived_from": []}})
            try:
                output = self.agents[agent_key](agent_ctx)
                previous = output.get("content") if isinstance(output, dict) else output
                bad = [why for ok, why in (g(ctx, output) for g in node.exit_gates) if not ok]
                self._audit("exit_gates", node=node.id, attempt=n, agent=agent_key, failures=bad)
                if not bad:
                    return output, "", False, previous
                error = "; ".join(bad)
            except Exception as exc:  # agent crashed
                error = f"{type(exc).__name__}: {exc}"
            will_retry = i < max_attempts
            delay = min(self.backoff_max_s, self.backoff_base_s * 2 ** (i - 1)) if will_retry else 0.0
            self._audit("attempt_failed", node=node.id, attempt=n, agent=agent_key, stage=stage,
                        error=error, will_retry=will_retry, backoff_s=delay)
            if delay > 0:
                self._sleep(delay)
        return None, error, False, previous

    def _rollback(self) -> None:
        """Revert the whole failed change set to the checkpoint taken at invalidate()."""
        if not self._checkpoint:
            self._checkpoint = None
            return
        restored = sorted(self._checkpoint)
        failed = sorted(n for n, st in self.state.items() if st in (NodeState.FAILED, NodeState.REJECTED))
        with self._lock:
            for nid, (output, producer, fp, consumed) in self._checkpoint.items():
                self.context[nid] = output
                self.producer[nid] = producer
                if fp is not None:
                    self.fingerprint[nid] = fp
                self._consumed[nid] = consumed
                self.state[nid] = NodeState.ROLLED_BACK
        self._audit("rollback", reason=self.halt_reason, trigger_nodes=failed, restored=restored)
        self._checkpoint = None

    def _consume_budget(self, node: Node) -> bool:
        with self._lock:
            self._total_attempts += 1
            if self._total_attempts > self.max_total_attempts:
                self._safe_stop("attempt budget exceeded")
                self.state[node.id] = NodeState.FAILED
                return False
        return True

    def _fail(self, node: Node, reason: str) -> None:
        self._set(node.id, NodeState.FAILED)
        self._audit("node_failed", node=node.id, reason=reason)
        self._safe_stop(f"{node.id}: {reason}")

    def stop(self, reason: str = "stopped by the operator") -> None:
        """Ask the run to safe-stop: no new node starts; work already in flight finishes. The FIRST reason wins."""
        self._safe_stop(reason)

    def _safe_stop(self, reason: str) -> None:
        if not self._halt.is_set():
            self.halt_reason = reason
            self._halt.set()
            self._audit("safe_stop", reason=reason)

    def _skip_unstarted(self) -> None:
        for nid, st in self.state.items():
            if st is NodeState.PENDING:
                self.state[nid] = NodeState.SKIPPED

    def _final_status(self) -> RunStatus:
        if self._halt.is_set():
            return RunStatus.HALTED
        if all(s is NodeState.DONE for s in self.state.values()):
            return RunStatus.COMPLETED
        return RunStatus.DEADLOCK

    def _set(self, node_id: str, new: NodeState) -> None:
        old = self.state[node_id]
        self.state[node_id] = new
        self._audit("state_change", node=node_id, from_state=old.value, to_state=new.value)

    def _ids(self, st: NodeState) -> list[str]:
        return sorted(n for n, s in self.state.items() if s is st)

    def _audit(self, event: str, **data: Any) -> None:
        self._audit_sink({"ts": time.time(), "event": event, **data})

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
* invalidate() supports dynamic re-planning: it CHECKPOINTS the last good
  outputs of the node and everything downstream, drops them, and the next run()
  redoes them.
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

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from types import MappingProxyType
from typing import Any, Callable, Mapping

from .graph import Graph, Node
from .state import NodeState, RunStatus

Agent = Callable[[Mapping[str, Any]], Any]          # context -> output
Approver = Callable[[Node, Any], bool]              # (node, output) -> approved?
AuditSink = Callable[[dict], None]


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

        self.state: dict[str, NodeState] = {n: NodeState.PENDING for n in graph.nodes}
        self.context: dict[str, Any] = {}      # node_id -> committed output
        self.producer: dict[str, str] = {}     # node_id -> agent key that produced the committed output
        self._checkpoint: dict[str, tuple[Any, str]] | None = None   # last good outputs before a re-plan
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
        # Checkpoint the last GOOD outputs so a failed re-plan can be rolled back.
        # (Keep the oldest checkpoint if a re-plan is invalidated again mid-flight.)
        snapshot = {nid: (self.context[nid], self.producer.get(nid, "?"))
                    for nid in affected if nid in self.context}
        if self._checkpoint is None:
            self._checkpoint = snapshot
        else:
            for nid, saved in snapshot.items():
                self._checkpoint.setdefault(nid, saved)
        if snapshot:
            self._audit("checkpoint", trigger=node_id, nodes=sorted(snapshot))
        for nid in affected:
            self.state[nid] = NodeState.PENDING
            self.context.pop(nid, None)
            self.producer.pop(nid, None)
        self._halt.clear()
        self.halt_reason = None
        self._audit("replan", trigger=node_id, reason=reason, invalidated=sorted(affected))
        return affected

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
        output, last_error, aborted = self._run_agent(node, node.agent, ctx, node.max_retries + 1, "primary")
        if aborted:
            return

        # 2b. fallback agent: an alternate strategy, judged by the SAME exit gates
        if last_error and node.fallback_agent:
            self._audit("fallback_engaged", node=node.id, fallback_agent=node.fallback_agent,
                        primary_error=last_error)
            used = node.fallback_agent
            output, last_error, aborted = self._run_agent(
                node, node.fallback_agent, ctx, node.fallback_attempts, "fallback")
            if aborted:
                return
        if last_error:
            kind = "retries and fallback exhausted" if node.fallback_agent else "retries exhausted"
            return self._fail(node, f"{kind}: {last_error}")

        # 3. human approval for high-impact nodes
        if node.requires_approval:
            self._set(node.id, NodeState.AWAITING_APPROVAL)
            approved = self.approver(node, output)
            self._audit("approval", node=node.id, approved=approved)
            if not approved:
                self._set(node.id, NodeState.REJECTED)
                return self._safe_stop(f"{node.id} rejected by human")

        # 4. commit (output + which agent produced it: part of decision lineage)
        with self._lock:
            self.context[node.id] = output
            self.producer[node.id] = used
        self._audit("node_committed", node=node.id, agent=used, fallback=used != node.agent)
        self._set(node.id, NodeState.DONE)

    def _run_agent(self, node: Node, agent_key: str, ctx: Mapping[str, Any],
                   max_attempts: int, stage: str) -> tuple[Any, str, bool]:
        """Run one agent up to max_attempts times. Returns (output, error, aborted).

        error == "" means success. aborted=True means the global budget safe-stopped the run.
        """
        error = ""
        for i in range(1, max_attempts + 1):
            if not self._consume_budget(node):
                return None, "attempt budget exceeded", True
            self.attempts[node.id] += 1
            n = self.attempts[node.id]
            try:
                output = self.agents[agent_key](ctx)
                bad = [why for ok, why in (g(ctx, output) for g in node.exit_gates) if not ok]
                self._audit("exit_gates", node=node.id, attempt=n, agent=agent_key, failures=bad)
                if not bad:
                    return output, "", False
                error = "; ".join(bad)
            except Exception as exc:  # agent crashed
                error = f"{type(exc).__name__}: {exc}"
            will_retry = i < max_attempts
            delay = min(self.backoff_max_s, self.backoff_base_s * 2 ** (i - 1)) if will_retry else 0.0
            self._audit("attempt_failed", node=node.id, attempt=n, agent=agent_key, stage=stage,
                        error=error, will_retry=will_retry, backoff_s=delay)
            if delay > 0:
                self._sleep(delay)
        return None, error, False

    def _rollback(self) -> None:
        """Revert the whole failed change set to the checkpoint taken at invalidate()."""
        if not self._checkpoint:
            self._checkpoint = None
            return
        restored = sorted(self._checkpoint)
        failed = sorted(n for n, st in self.state.items() if st in (NodeState.FAILED, NodeState.REJECTED))
        with self._lock:
            for nid, (output, producer) in self._checkpoint.items():
                self.context[nid] = output
                self.producer[nid] = producer
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

"""Explicit dependency graph (DAG) for an SDLC workflow.

A Node declares:
  * which agent runs it
  * what it depends on (edges)
  * entry gates  - must pass BEFORE the agent runs (inputs are acceptable)
  * exit gates   - must pass AFTER the agent runs (output is acceptable)
  * whether a human must approve the output (high-impact action)
  * its retry budget
  * governance tags, which the workflow linter turns into mandatory controls
    (e.g. a "code" node MUST have a security exit gate)
  * an optional fallback agent, tried once retries are exhausted (an alternate,
    more conservative strategy); its output must pass the SAME exit gates

Gates are plain callables so policies (security, compliance, change control)
can be plugged in later from orchestrator/governance/.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
from typing import Any, Callable, Mapping

# A gate receives the shared context (node_id -> output) and, for exit gates,
# the node's own output. It returns (passed, reason).
GateResult = tuple[bool, str]
EntryGate = Callable[[Mapping[str, Any]], GateResult]
ExitGate = Callable[[Mapping[str, Any], Any], GateResult]


class GraphError(ValueError):
    """Raised when the workflow definition is invalid."""


@dataclass(frozen=True)
class Node:
    id: str
    agent: str                                   # key into the engine's agent registry
    depends_on: tuple[str, ...] = ()
    entry_gates: tuple[EntryGate, ...] = ()
    exit_gates: tuple[ExitGate, ...] = ()
    requires_approval: bool = False
    max_retries: int = 2                         # retries AFTER the first attempt
    fallback_agent: str | None = None            # agent key tried after retries are exhausted
    fallback_attempts: int = 1                   # attempts granted to the fallback agent
    description: str = ""
    tags: tuple[str, ...] = ()                   # governance tags: "high_impact", "code", "release"


@dataclass
class Graph:
    nodes: dict[str, Node] = field(default_factory=dict)

    # ---- construction -------------------------------------------------
    def add(self, node: Node) -> "Graph":
        if node.id in self.nodes:
            raise GraphError(f"duplicate node id: {node.id}")
        self.nodes[node.id] = node
        return self

    # ---- validation ---------------------------------------------------
    def validate(self) -> None:
        """Fail fast on unknown dependencies and cycles."""
        for n in self.nodes.values():
            for dep in n.depends_on:
                if dep not in self.nodes:
                    raise GraphError(f"{n.id} depends on unknown node '{dep}'")
        self.topological_order()  # raises on cycle

    def topological_order(self) -> list[str]:
        """Kahn's algorithm; raises GraphError if a cycle exists."""
        indegree = {nid: len(n.depends_on) for nid, n in self.nodes.items()}
        children = self._children()
        queue = deque(sorted(nid for nid, d in indegree.items() if d == 0))
        order: list[str] = []
        while queue:
            nid = queue.popleft()
            order.append(nid)
            for child in sorted(children[nid]):
                indegree[child] -= 1
                if indegree[child] == 0:
                    queue.append(child)
        if len(order) != len(self.nodes):
            cyclic = sorted(set(self.nodes) - set(order))
            raise GraphError(f"cycle detected involving: {cyclic}")
        return order

    # ---- queries ------------------------------------------------------
    def _children(self) -> dict[str, set[str]]:
        children: dict[str, set[str]] = {nid: set() for nid in self.nodes}
        for n in self.nodes.values():
            for dep in n.depends_on:
                children[dep].add(n.id)
        return children

    def downstream(self, node_id: str) -> set[str]:
        """All transitive dependents of node_id (used by re-planning)."""
        children = self._children()
        seen: set[str] = set()
        stack = [node_id]
        while stack:
            for child in children[stack.pop()]:
                if child not in seen:
                    seen.add(child)
                    stack.append(child)
        return seen

    def parallel_layers(self) -> list[list[str]]:
        """Static view of what *could* run in parallel (useful for docs/diagrams)."""
        level: dict[str, int] = {}
        for nid in self.topological_order():
            deps = self.nodes[nid].depends_on
            level[nid] = 0 if not deps else 1 + max(level[d] for d in deps)
        layers: dict[int, list[str]] = {}
        for nid, lv in level.items():
            layers.setdefault(lv, []).append(nid)
        return [sorted(layers[k]) for k in sorted(layers)]

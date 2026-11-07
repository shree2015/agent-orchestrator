"""Policy-as-code framework shared by security, compliance and change-control rules.

Vocabulary (worth being able to say in one breath):
  * Policy    - one named rule: id + category + a check function returning violation messages.
  * PolicySet - a group of policies evaluated together against one *subject*.
  * Violation - the outcome of a failed policy; renders as "[category/policy-id] message".
  * Gate      - an adapter that lets the engine run a PolicySet at a node's entry or exit.

Three kinds of subject, one per category:
  security       -> str                 (generated source code)
  compliance     -> Mapping[str, str]   (artifact name -> text, e.g. {"implementation": ..., "docs": ...})
  change_control -> ChangeSet           (baseline vs proposed files)  or the workflow Graph itself

Design rules:
  * FAIL CLOSED: a policy that needs an artifact which is missing reports a violation, never a pass.
  * The "[category/policy-id]" prefix is the one convention the audit log and metrics rely on.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Callable, Iterable, Mapping

CATEGORIES = ("security", "compliance", "change_control")
_VIOLATION_RE = re.compile(r"\[(security|compliance|change_control)/([a-z0-9-]+)\]")


class GovernanceError(RuntimeError):
    """Raised when a workflow definition itself violates governance (fail closed, nothing runs)."""

    def __init__(self, violations: Iterable["Violation"]) -> None:
        self.violations = list(violations)
        super().__init__("; ".join(str(v) for v in self.violations))


@dataclass(frozen=True)
class Violation:
    policy: str
    category: str
    message: str

    def __str__(self) -> str:
        return f"[{self.category}/{self.policy}] {self.message}"


def find_violation_ids(text: str) -> list[str]:
    """Extract 'category/policy-id' tags from audit text (used by metrics)."""
    return [f"{c}/{p}" for c, p in _VIOLATION_RE.findall(text or "")]


@dataclass(frozen=True)
class Policy:
    id: str
    category: str
    description: str
    check: Callable[[Any], list[str]]       # subject -> list of violation messages

    def __post_init__(self) -> None:
        if self.category not in CATEGORIES:
            raise ValueError(f"unknown policy category: {self.category}")

    def evaluate(self, subject: Any) -> list[Violation]:
        return [Violation(self.id, self.category, m) for m in self.check(subject)]


class PolicySet:
    def __init__(self, *policies: Policy) -> None:
        ids = [p.id for p in policies]
        if len(ids) != len(set(ids)):
            raise ValueError("duplicate policy ids in set")
        self.policies = tuple(policies)

    @property
    def categories(self) -> frozenset[str]:
        return frozenset(p.category for p in self.policies)

    def evaluate(self, subject: Any) -> list[Violation]:
        return [v for p in self.policies for v in p.evaluate(subject)]


# --------------------------------------------------------------------------
# Gate adapters: (ctx, out) -> (passed, reason).  `policy_categories` lets the
# workflow linter verify that required kinds of gate are actually attached.
# --------------------------------------------------------------------------
def _result(violations: list[Violation]) -> tuple[bool, str]:
    return (not violations, "; ".join(str(v) for v in violations) if violations else "ok")


def _tag(gate, pset: PolicySet):
    gate.policy_categories = pset.categories
    return gate


def code_gate(pset: PolicySet):
    """Exit gate: evaluate the node's own output as source code."""
    return _tag(lambda ctx, out: _result(pset.evaluate(out["content"])), pset)


def artifact_gate(pset: PolicySet, key: str):
    """Exit gate: evaluate all committed artifacts plus this node's output stored under `key`."""
    def gate(ctx, out):
        artifacts = {k: v["content"] for k, v in ctx.items()}
        artifacts[key] = out["content"]
        return _result(pset.evaluate(artifacts))
    return _tag(gate, pset)


def entry_artifact_gate(pset: PolicySet):
    """Entry gate: evaluate the artifacts committed so far (before the node runs)."""
    def gate(ctx):
        return _result(pset.evaluate({k: v["content"] for k, v in ctx.items()}))
    return _tag(gate, pset)


def categories_of(gate) -> frozenset[str]:
    return getattr(gate, "policy_categories", frozenset())

"""Change-control policies: limit the blast radius of what agents are allowed to change.

Two subjects:
  * ChangeSet (baseline vs proposed files)  -> size limits, protected paths, deletions
  * the workflow Graph itself               -> `lint_graph` (governance cannot be defined away)

Run-time re-plan limits (reason required, max re-plans) live in the engine's `invalidate()`.
"""
from __future__ import annotations

import difflib
import fnmatch
import json
from dataclasses import dataclass, field
from typing import Mapping

from .. import codeintel as ci
from .base import Policy, PolicySet, Violation, categories_of

DEFAULT_PROTECTED = (".env", ".env.*", "*.pem", "*.key", ".github/*", "ci/*", "secrets/*")


@dataclass(frozen=True)
class ChangeSet:
    """path -> content. A path missing from `proposed` means the change deletes it."""
    baseline: Mapping[str, str] = field(default_factory=dict)
    proposed: Mapping[str, str] = field(default_factory=dict)

    @property
    def added(self) -> set[str]:
        return set(self.proposed) - set(self.baseline)

    @property
    def deleted(self) -> set[str]:
        return set(self.baseline) - set(self.proposed)

    @property
    def modified(self) -> set[str]:
        return {p for p in set(self.baseline) & set(self.proposed) if self.baseline[p] != self.proposed[p]}

    @property
    def touched(self) -> set[str]:
        return self.added | self.deleted | self.modified

    @property
    def lines_changed(self) -> int:
        total = 0
        for path in self.touched:
            diff = difflib.unified_diff(self.baseline.get(path, "").splitlines(),
                                        self.proposed.get(path, "").splitlines(), lineterm="", n=0)
            total += sum(1 for ln in diff if ln[:1] in "+-" and not ln.startswith(("+++", "---")))
        return total


def max_files_changed(limit: int = 3) -> Policy:
    def check(cs: ChangeSet) -> list[str]:
        n = len(cs.touched)
        return [f"{n} files changed; limit is {limit}"] if n > limit else []
    return Policy("max-files-changed", "change_control", "Bound how many files one change may touch", check)


def max_lines_changed(limit: int = 200) -> Policy:
    def check(cs: ChangeSet) -> list[str]:
        n = cs.lines_changed
        return [f"{n} lines changed; limit is {limit}"] if n > limit else []
    return Policy("max-lines-changed", "change_control", "Bound the size of one change", check)


def protected_paths(patterns: tuple[str, ...] = DEFAULT_PROTECTED) -> Policy:
    def hit(path: str) -> bool:
        name = path.rsplit("/", 1)[-1]
        return any(fnmatch.fnmatch(path, p) or fnmatch.fnmatch(name, p) for p in patterns)

    def check(cs: ChangeSet) -> list[str]:
        return [f"protected path touched: {p}" for p in sorted(cs.touched) if hit(p)]
    return Policy("protected-paths", "change_control", "Agents may not touch credentials, CI or secrets", check)


def no_file_deletion() -> Policy:
    def check(cs: ChangeSet) -> list[str]:
        return [f"file deletion not allowed: {p}" for p in sorted(cs.deleted)]
    return Policy("no-file-deletion", "change_control", "Deleting files needs a human, not an agent", check)


def default_set(max_files: int = 3, max_lines: int = 200,
                protected: tuple[str, ...] = DEFAULT_PROTECTED) -> PolicySet:
    return PolicySet(max_files_changed(max_files), max_lines_changed(max_lines),
                     protected_paths(protected), no_file_deletion())


def change_gate(path: str, pset: PolicySet | None = None, baseline: Mapping[str, str] | None = None):
    """Exit gate for a node whose output is the single file `path` (baseline = existing files)."""
    pset = pset or default_set()

    def gate(ctx, out):
        violations = pset.evaluate(ChangeSet(baseline or {}, {path: out["content"]}))
        return (not violations, "; ".join(str(v) for v in violations) if violations else "ok")

    gate.policy_categories = pset.categories
    gate.policy_ids = tuple(f"{p.category}/{p.id}" for p in pset.policies)
    return gate


# --------------------------------------------------------------------------
# Workflow linter: governance cannot be configured away by a careless workflow.
# --------------------------------------------------------------------------
def lint_graph(graph) -> list[Violation]:
    """Static rules over a Graph, driven by node tags:
       high_impact -> must require human approval
       code        -> must carry a security exit gate
       release     -> must carry a compliance entry gate
    """
    out: list[Violation] = []
    v = lambda pid, msg: out.append(Violation(pid, "change_control", msg))
    for n in graph.nodes.values():
        if "high_impact" in n.tags and not n.requires_approval:
            v("high-impact-requires-approval", f"node '{n.id}' is high-impact but needs no human approval")
        if "code" in n.tags and not any("security" in categories_of(g) for g in n.exit_gates):
            v("code-requires-security-gate", f"node '{n.id}' produces code but has no security exit gate")
        if "release" in n.tags and not any("compliance" in categories_of(g) for g in n.entry_gates):
            v("release-requires-compliance-gate", f"node '{n.id}' releases but has no compliance entry gate")
    return out


# --------------------------------------------------------------------------
# Brownfield: backward compatibility and declared blast radius
# --------------------------------------------------------------------------
def public_api_stable() -> Policy:
    """Existing public functions/classes must remain, and their signatures must stay backward compatible."""
    def check(cs: ChangeSet) -> list[str]:
        out: list[str] = []
        for path in sorted(set(cs.baseline) & set(cs.proposed)):
            if not path.endswith(".py"):
                continue
            try:
                old, new = ci.public_api(cs.baseline[path]), ci.public_api(cs.proposed[path])
            except SyntaxError:
                continue                                  # the valid_python gate reports syntax errors
            for name, sig in old.items():
                if name not in new:
                    out.append(f"{path}: public API '{name}' was removed")
                else:
                    out += [f"{path}: public API '{name}' is no longer backward compatible: {why}"
                            for why in ci.incompatibilities(sig, new[name])]
        return out
    return Policy("public-api-stable", "change_control", "Do not break existing callers", check)


def change_within_declared_impact(baseline_code: str) -> Policy:
    """Agents declare their blast radius first (impact analysis), then are held to it.

    Subject: artifacts mapping with the proposed "implementation" and the "impact_analysis" JSON.
    """
    def check(subject) -> list[str]:
        for key in ("implementation", "impact_analysis"):
            if not (subject.get(key) or "").strip():
                return [f"missing or empty artifact: {key}"]
        try:
            impact = json.loads(subject["impact_analysis"])
            allowed = {i.get("symbol") for i in impact.get("impacted", []) if isinstance(i, dict)} | set(impact.get("new_symbols", []))
            changes = ci.changed_symbols(baseline_code, subject["implementation"])
        except (ValueError, AttributeError, SyntaxError):
            return ["could not compare the change against the impact analysis"]
        return [f"symbol '{name}' was {how} but is not declared in the impact analysis"
                for name, how in sorted(changes.items()) if name not in allowed]
    return Policy("change-within-declared-impact", "change_control", "A patch may only touch what the analysis declared", check)


def brownfield_set(max_lines: int = 40, max_files: int = 1) -> PolicySet:
    """Tight limits for a bug fix in an existing codebase (compare default_set for new code)."""
    return PolicySet(max_files_changed(max_files), max_lines_changed(max_lines), protected_paths(),
                     no_file_deletion(), public_api_stable())

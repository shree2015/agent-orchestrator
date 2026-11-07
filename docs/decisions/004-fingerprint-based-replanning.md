# ADR 004: Re-plan from content fingerprints, not from a manual "redo everything" switch

**Status:** accepted

## Context
Requirements change mid-flight. The PDF asks the system to re-plan dynamically when upstream outputs change while
keeping governance. A manual `invalidate(node)` redoes everything downstream even when nothing relevant changed,
and relies on the caller to know what is affected.

## Decision
* Every committed output gets a **content fingerprint** (sha256 of its content, not its metadata).
* Every node records the fingerprints of **all its ancestors** at the time it ran.
* `Engine.revise(node, new_content, reason, by)` replaces an output; the engine **derives** the affected set
  (nodes whose recorded ancestor fingerprints no longer match), checkpoints them, and re-runs only those.
* An **identical** revision is a no-op: no checkpoint, no re-plan, no budget spent.
* Governance is unchanged for revisions: a stated reason, the re-plan budget, the node's **exit gates** (a human
  edit must satisfy the same policies as agent output; a crashing gate rejects), approval for agent-made edits to
  approval nodes, and **checkpoint/rollback**: if the re-run fails the whole change, including the edit, is reverted.
* `run()` also checks for stale nodes first, so it never builds on out-of-date results.

## Alternatives considered
* *Early cutoff* (skip dependents when an intermediate re-run yields the same output): fewer re-runs, but an
  agent often reads more than its declared parents. Tracking all ancestors is conservative and always correct.
* *Always invalidate by hand*: simple, kept as `invalidate()` for forced regeneration.

## Consequences
* (+) Minimal, explainable rework: the audit log says which output changed (old and new fingerprint) and why each
  node was redone; metrics report `revisions`, `auto_replans`, `nodes_replanned`.
* (-) Ancestor-level tracking can redo a node whose agent never actually read the changed input.
* (-) State is in memory: a process crash loses fingerprints (no resume yet).

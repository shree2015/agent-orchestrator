# ADR 001: Resilience ladder (retry -> fallback -> safe-stop -> rollback)

**Status:** accepted

## Context
The assignment requires bounded retries, fallback, rollback and safe-stop. These are easy to
blur together, so each needs one precise meaning.

## Decision
| Mechanism | Trigger | Action | Bound |
|---|---|---|---|
| Retry | agent error or exit-gate failure | re-run the same agent, exponential backoff (capped) | `max_retries` per node |
| Fallback | retries exhausted | run an alternate agent (e.g. a more conservative prompt) | `fallback_attempts` (default 1) |
| Safe-stop | fallback exhausted, human rejection, global attempt budget | stop starting new nodes; unstarted nodes -> SKIPPED | run-wide `max_total_attempts` |
| Rollback | safe-stop during a *re-plan* | restore the whole change set to the checkpoint taken at `invalidate()`; nodes -> ROLLED_BACK | one checkpoint per change |

* Fallback output must pass the **same exit gates** as primary output. A fallback is a different
  strategy, never a way around governance.
* Outputs are committed to shared context only after gates and approvals pass, so a failure on the
  *first* run leaves nothing to undo.
* Rollback is **all-or-nothing** across the invalidated nodes: a half-applied change (new design
  with old code) would be inconsistent, so partially successful re-runs are reverted too.
* A re-plan that completes releases the checkpoint (`change_committed`).

## Consequences
* (+) Every step is an audit event, so retries, fallbacks and rollbacks are all measurable.
* (-) Rollback restores *outputs held by the engine*; side effects outside it (files, deployments)
  are not undone. Production would need compensating actions per node.
* (-) One checkpoint level only; no history of older versions.

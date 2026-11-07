# Policy catalog

Every rule is a named policy. A violation always renders as `[category/policy-id] message`, so the
audit log, the run report and the metrics all say *which* rule fired.

| Policy id | Category | Where it is enforced | On violation |
|---|---|---|---|
| `no-hardcoded-secrets` | security | `implementation` exit gate | retry -> fallback -> safe-stop |
| `no-dangerous-calls` (eval/exec/os.system/subprocess) | security | `implementation` exit gate | same |
| `no-unsafe-imports` (ctypes/pickle/marshal) | security | `implementation` exit gate | same |
| `dependency-allowlist` (stdlib OK, third-party must be approved) | security | `implementation` exit gate | same |
| `public-api-documented` (docstrings) | compliance | `implementation` exit gate | same |
| `minimum-tests` (>= 3 `test_` functions) | compliance | `tests` exit gate | same |
| `docs-cover-public-api` | compliance | `docs` exit gate | same |
| `release-evidence` (all artifacts present + approving review) | compliance | `release` **entry** gate | safe-stop, release blocked |
| `max-files-changed`, `max-lines-changed` | change control | `implementation` exit gate (`ChangeSet`) | retry -> fallback -> safe-stop |
| `protected-paths` (`.env`, keys, CI, secrets) | change control | same | same |
| `no-file-deletion` | change control | same | same |
| `high-impact-requires-approval` | change control | **workflow lint**, before anything runs | workflow rejected |
| `code-requires-security-gate` | change control | workflow lint | workflow rejected |
| `release-requires-compliance-gate` | change control | workflow lint | workflow rejected |
| re-plan needs a reason; `max_replans` budget | change control | `Engine.invalidate()` | change rejected, system stays in last good state |
| `ui-no-external-resources` | security | `ui` exit gate | retry -> safe-stop |
| `ui-no-unsafe-dom-sinks` (innerHTML, eval, document.write...) | security | `ui` exit gate | same |
| `ui-no-inline-event-handlers`, `ui-no-inline-style-attributes` | security | `ui` exit gate | same |
| `ui-csp-compatible-blocks` (script/style must be bare so the server can hash them) | security | `ui` exit gate | same |
| `ui-uses-documented-api`, `ui-accessibility-basics` | compliance | `ui` exit gate | same |
| `impact-analysis-grounded` | compliance | `impact_analysis` exit gate (brownfield) | retry -> safe-stop |
| `no-tests-removed`, `regression-test-reproduces-bug` | compliance | `tests` exit gate (brownfield) | same |
| `changelog-references-ticket` | compliance | `docs` exit gate (brownfield) | same |
| `public-api-stable` | change control | `implementation` exit gate (brownfield, via `ChangeSet`) | same |
| `change-within-declared-impact` | change control | `implementation` exit gate (brownfield, ambiguous) | same |
| `plan-well-formed` (unique ids, valid dependencies, no cycles) | compliance | `plan` exit gate | retry -> safe-stop |
| `plan-maps-to-workflow` (tasks target real stages; key stages are planned) | compliance | `plan` exit gate | same |
| `plan-sequencing-matches-graph` (the plan's order is executable as written) | compliance | `plan` exit gate | same |
| `acceptance-criteria-traced` (every criterion maps to tasks and evidence) | compliance | `plan` exit gate | same |
| `traceability-evidence-exists` (promised tests and gates really exist) | compliance | `tests` exit gate | same |
| `decisions-cover-ambiguities` | compliance | `clarifications` exit gate | safe-stop (a person is not re-asked by retries) |
| `high-risk-needs-human` (agents may not assume high-risk answers) | compliance | `clarifications` exit gate, and every `revise()` of it | same |
| `answers-within-options`, `assumptions-have-rationale` | compliance | `clarifications` exit gate | same |
| `decisions-consistent` (answers must not contradict each other) | compliance | `clarifications` exit gate | same |
| `implementation-honours-decisions` (decisions are visible in the code) | compliance | `implementation` exit gate (ambiguous) | retry -> fallback -> safe-stop |

## Three layers, three subjects

* **Security** policies read *code* (a string).
* **Compliance** policies read *artifacts* (`{"implementation": ..., "docs": ...}`) and **fail closed**:
  a missing artifact is a violation, never a pass.
* **Change-control** policies read a *ChangeSet* (baseline vs proposed files), the *workflow graph*
  (linter), or the *engine's re-plan requests*.

## Why a workflow linter
A gate only protects you if it is attached. Nodes carry tags (`code`, `high_impact`, `release`) and
`lint_graph` refuses to run a workflow in which a tagged node lacks its mandatory control. Governance
cannot be silently configured away; the refusal itself is written to the audit log (`policy_lint`).

## Revisions obey the same policies
`Engine.revise()` runs the revised node's exit gates, so a human edit must satisfy the same policies as agent output
(for example a contradictory answer is rejected), and a gate that crashes rejects the revision instead of passing it.
See ADR 004 and ADR 005.

## Brownfield note
The brownfield policies are *bound to a baseline*: gates are built with the real baseline code/tests
(`gates.brownfield_change_gate`, `within_declared_impact`, `regression_reproduced`...). See ADR 003 and
`docs/scenarios/02-brownfield-orphaned-clicks.md`.

## Demos
```
python -m orchestrator.cli run slugify --approve auto --violate secret        # security
python -m orchestrator.cli run slugify --approve auto --violate import        # security (dependency)
python -m orchestrator.cli run slugify --approve auto --violate undocumented  # compliance, recovers
python -m orchestrator.cli run slugify --approve auto --violate thin-tests    # compliance, halts
python -m orchestrator.cli run slugify --approve auto --violate empty-docs    # compliance, halts
python -m orchestrator.cli run slugify --approve auto --revise-design ok --max-replans 0   # change control
python -m orchestrator.cli run shortener --approve auto --violate ui-innerhtml   # UI security policy
python -m orchestrator.cli run brownfield --approve auto --violate scope-creep    # brownfield change control
```
Open `runs/<id>/report.md`: the metrics table has `policy_violations` (counts per policy id, once per
blocked output) and `changes_rejected`.

## Known limits (say these before they are asked)
* Checks are static (AST/regex): they raise the bar but are not a substitute for a real SAST tool,
  dependency scanning or a sandbox.
* The secret patterns will miss obfuscated secrets and may flag test fixtures.
* The `ChangeSet` policies are wired for a single-file output today; the brownfield scenario will
  supply a real multi-file baseline.
* Policies are code, so changing one is itself a change: it should go through review like any other.

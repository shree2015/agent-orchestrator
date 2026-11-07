# ADR 002: Policy-as-code with three categories and a workflow linter

**Status:** accepted

## Context
The assignment asks for guardrails for security, compliance and change control, with audit-grade
traceability. Ad-hoc `if` checks inside gates would work but could not be audited by rule, counted,
or proven to be attached.

## Decision
1. Each rule is a `Policy(id, category, check)`; groups are a `PolicySet`; gates adapt a set to the
   engine. Violations render as `[category/id] message`.
2. Metrics count violations **per policy id, once per blocked output**, derived only from the audit log.
3. Compliance policies **fail closed** on missing evidence. `release` re-verifies evidence at its
   entry gate instead of trusting upstream nodes.
4. A workflow **linter** driven by node tags enforces that mandatory controls are attached, and runs
   before any agent does. It is fail-closed and audited.
5. Re-plans are change-controlled in the engine: a stated reason and a per-run budget; a rejected
   change leaves the system in its last good state.

## Alternatives considered
* *One monolithic policy function* - simpler, but no per-rule audit or metrics.
* *An external engine (OPA/Rego)* - more standard in industry, but a heavy dependency for a prototype
  that must run offline with only Python installed. Noted as the production direction.

## Consequences
* (+) Each guardrail is separately testable and mutation-tested; a new rule is ~10 lines.
* (+) Explainable: "which rule blocked it?" is answered by the log.
* (-) Tags are a convention: a node mislabelled as untagged escapes the linter. Mitigation: the
  workflow definition is code-reviewed; a production version would derive tags from a registry.
* (-) Static analysis only (see docs/policies.md, "Known limits").

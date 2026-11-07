# ADR 003: Brownfield changes - declare the blast radius first, then enforce it

**Status:** accepted

## Context
In an existing codebase the risk is not "does the new code work" but "what else did this change break or
touch". Reviewing a diff by eye does not scale to agent-authored patches.

## Decision
1. An `impact_analysis` stage runs before design. Its claims are **grounded**: every symbol it names must exist
   in the baseline AST, so it cannot describe code it has not read.
2. The patch is then **bound to that declaration**: any added, removed or modified symbol (function, method,
   class body, module level) that was not declared is a violation.
3. Compatibility and size are enforced mechanically: backward-compatible public signatures, a 40-line / 1-file
   budget (versus 900 lines by default for a new service).
4. A fix needs a **regression test that fails on the old code**, and existing tests can never be removed.

## Alternatives considered
* *Trust the reviewer agent to spot scope creep* - non-deterministic and itself an LLM.
* *Line-based diffs for scope* - noisy; a reformat would look like a change. Symbol fingerprints via `ast.dump`
  ignore formatting and line moves.

## Consequences
* (+) Deterministic, explainable blocks: the message names the symbol or test.
* (+) The same mechanism supports greenfield (everything is "added") and brownfield.
* (-) Python-only, symbol-level granularity (documented limits in the scenario write-up).
* (-) Declared-but-unchanged symbols are allowed, so the analysis can over-declare; the size budget bounds this.

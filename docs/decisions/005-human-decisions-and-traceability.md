# ADR 005: Ambiguity is resolved by the right party, and every decision stays traceable

**Status:** accepted

## Context
A vague request ("make links expire sensibly") can be "implemented" in many ways. An agent that silently picks one
has made a product decision nobody approved.

## Decision
1. Ambiguity becomes **structured questions**: id, options, risk level, proposed default, why it matters.
2. **Risk decides who may answer.** High risk needs a human; lower risk may be assumed, with a recorded reason.
   If no human is available the run stops safely instead of guessing.
3. Answers are **validated** (supported options, mutual consistency) by policy, not by trusting the agent.
4. Decisions must be **visible downstream**: constants in code (checked by AST, never executed), asserted by tests,
   recorded in the changelog with who decided.
5. A **plan** maps tasks to workflow stages and acceptance criteria to evidence; policies check the plan is
   acyclic, executable in the order stated, complete, and that promised tests really exist.

## Alternatives considered
* *Let the LLM ask questions conversationally*: unstructured answers cannot be validated or traced.
* *Block on every ambiguity*: safe but unusable; risk grading keeps humans on the decisions that matter.

## Consequences
* (+) Every behavioural decision has an owner, a timestamp (audit log) and an enforcement point.
* (-) Risk levels are assigned by the requirements agent; a mislabelled high-risk question would be assumable.
  Mitigation: the human reviews the questions at the design checkpoint.
* (-) Constant-based traceability suits configuration-like decisions; behavioural decisions need test-based evidence.

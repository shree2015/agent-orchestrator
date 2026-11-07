# ADR 009: A reviewer's rejection sends the work back, within a bound

**Status:** accepted

## Context
The second live run with a real model (Sonnet) stopped at `review`. The reviewer's reasons were, in part, right: the model's first
draft really did have a defect (a non-ASCII URL is accepted and then crashes the redirect). The pipeline treated the rejection like
any failed attempt: it **retried the reviewer** (which cannot fix the code) and then **halted**. A real model's first draft will
often have *some* defect, so a pipeline that halts at the first rejection can never converge. The retries were also pointless: a
reviewer's REJECT is a *verdict about earlier work*, not a malformed answer.

## Decision
1. **A node may declare `rework=Rework(target, when)`.** If its last output is rejected *and* `when(output)` says it is a real
   verdict (for the reviewer: it begins with REJECT), the engine does not fail the node: it sends the findings back.
2. **What is redone:** the target stage (the code) **and everything downstream of it** (tests, documentation, UI, the review).
   Earlier work (requirements, plan, design) is not redone.
3. **Every redone generating stage is told the findings and shown its own previous output**, with the instruction to fix only what
   concerns its artifact. The findings may concern the tests or the documentation, not only the code.
4. **Bounded:** `max_reworks` (default 2; `--max-reworks N`; 0 restores "halt at once"). A refused rework is audited, and the run
   halts with the reviewer's findings as the reason.
5. **A rework is an ordinary change set:** checkpointed, so a rework that fails its gates is rolled back whole; audited
   (`rework` event; `reworks` metric; the dashed arrow in the workflow graphs).
6. **A reply that is not a verdict** (malformed, or not beginning with APPROVE or REJECT) is retried as before, never sent back.
7. The reviewer itself gets **one** retry, which asks it to re-check its own verdict against the request and the definition of a
   *blocking* defect, so a nit does not cost a whole rework round.
8. The graph validates that a rework target is **upstream** of the node that sends work back.

## Alternatives considered
* *Halt on the first rejection* (what it did): safe, but cannot converge with a real model.
* *Retry the reviewer*: pointless; the same inputs give the same verdict.
* *Feed the findings to the developer only*: misses findings about tests and documentation, and lets the tests go stale.
* *A human decides after every rejection*: sound for a team, but it stops an unattended run; it remains possible by lowering the budget.

## Consequences
* (+) A real model's draft can converge: find, fix, re-review, all governed and audited.
* (+) The loop reuses re-plan, checkpoint and rollback; there is no new control-flow engine.
* (-) Cost: a rework redoes up to four stages and a review, roughly five more model calls per round (bounded by the budget).
* (-) A reviewer may find something new each round; the bound and the "blocking defects only" rule limit this.
* (-) Only stages downstream of the code are redone: a flaw in the design or the plan still halts the run.
* (-) The attempt budget (`max_total_attempts`, 50) is shared, so rework rounds use it up.

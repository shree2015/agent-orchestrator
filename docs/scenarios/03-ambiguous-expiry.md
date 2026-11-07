# Scenario 3: Ambiguous - "Make links expire sensibly" (FEAT-207)

**Input, in full:** `Make links expire sensibly.` Five words, no numbers, no scope, no list of what must not change.

```
python -m orchestrator.cli run ambiguous --approve auto                                   # nobody answers: stops safely
python -m orchestrator.cli run ambiguous --approve auto --answer default_ttl_days=90      # a human answers the high-risk question
python -m orchestrator.cli run ambiguous --approve ask                                    # you are asked in the terminal
```

## 1. Requirement understanding
The requirements agent turns the request into an engineering problem plus **structured open questions**
(`orchestrator/workflows/reference/ambiguous/requirements.json`):

| Question | Options | Risk | Proposed | Why it matters |
|---|---|---|---|---|
| `default_ttl_days`: how long should a new link live when no expiry is given? | 30 / 90 / 180 | **high** | 90 | Changes the lifetime of every future link; a wrong answer silently breaks links people already shared |
| `max_ttl_days`: longest expiry a client may request | 90 / 365 | medium | 365 | Keeping today's ceiling avoids rejecting requests that work now |
| `ttl_applies_to_aliases`: do custom-alias links get the default? | yes / no | medium | no | Alias links are usually deliberate campaign links |

Constraints stated up front: existing links are never modified; no API or signature changes; expired links keep
returning 410.

## 2. Controlled autonomy: who may answer what
```
requirements -> clarifications (HUMAN) --------------------------\
      \-> impact_analysis -> plan ------------------------------> design* -> implementation -> tests -\
                                                                                       -> docs  ----> review -> release*
```
* **High-risk questions can only be answered by a person.** An agent may *assume* low/medium-risk answers, but must
  record why (`assumptions-have-rationale`) and the changelog states who decided each one.
* With nobody to answer, the run **stops safely** and names the question:
  `[compliance/high-risk-needs-human] 'default_ttl_days' is high-risk and needs a human decision`.
  The decision stage is never retried: a person is not asked three times by a retry loop.
* Answers must be one of the supported options and must not contradict each other
  (`decisions-consistent`: default expiry may not exceed the maximum).
* The questions and the codebase analysis run **in parallel**, so the human is not on the critical path for analysis.

## 3. Decisions must reach the code
`implementation-honours-decisions` reads the generated module's constants (without running it) and compares them
to the recorded answers: `default_ttl_days = 90` requires `DEFAULT_TTL_S == 7776000`. An agent that ignores the
human's choice is blocked by name, with expected and actual values. The tests also assert the constants, and the
changelog records each decision and who made it.

## 4. Decomposition and traceability (the planner)
The `plan` stage produces tasks mapped to workflow stages, plus acceptance-criterion traceability
(`plan.json`). Policies check that the plan is acyclic, that every key stage is planned, that its **ordering is
executable as written** (a task that "follows" another must run after it in the real graph), that every acceptance
criterion maps to tasks and to evidence, and, once the tests exist, that every promised test really exists.

## 5. Automatic re-planning: the human changes their mind
```
python -m orchestrator.cli run ambiguous --approve auto --answer default_ttl_days=90 --revise-answer default_ttl_days=180
```
The answer is revised through `Engine.revise()`. The engine compares fingerprints and finds the nodes whose inputs
changed:

| Node | Attempts | |
|---|---|---|
| requirements, clarifications, impact_analysis, plan | 1 | untouched: they do not depend on the changed answer's consumers |
| design, implementation, tests, docs, review, release | 2 | redone, with approvals asked again at design and release |

Metrics record the cost of the change: `revisions 1, auto_replans 1, nodes_replanned 6`. Other outcomes:

| Command | Result |
|---|---|
| `--revise-answer default_ttl_days=90` (same answer) | **nothing happens**: identical output, no churn |
| `--answer default_ttl_days=30 --answer max_ttl_days=90 --revise-answer default_ttl_days=180` | **rejected** by `decisions-consistent`; the system stays in its last good state (exit code 3) |
| `--revise-answer default_ttl_days=180 --break-on-revise` | the re-run fails, the **whole change is rolled back, including the human's new answer**; the 90-day code and answer are restored |

An agent cannot overwrite a human decision with an assumption: `revise(..., by="agent")` on a high-risk decision is
rejected by `high-risk-needs-human`.

## 5b. See it working
```
python -m orchestrator.cli run ambiguous --approve auto --answer default_ttl_days=90
python runs\<run-id>\artifacts\implementation.py 8000          # Windows; use / on Linux and macOS
```
Open http://localhost:8000/. The run's `artifacts` folder is a complete deliverable: the changed service plus the
existing `ui.html`, which the change did not touch and the run carries over unchanged (listed in the report). Create a
link without choosing an expiry and the response now carries an `expires_at` 90 days out (the human's decision); a
custom-alias link stays permanent (the human's other decision). The UI's "Never" option now effectively means
"server default": that wording is the follow-up flagged in section 7.

## 6. Guardrails demonstrated (`--violate`, each misbehaves once then recovers)
`ignores-decision`, `scope-creep`, `untraced-tests`, `changelog-no-ticket`, `plan-cycle`, `plan-misses-criterion`,
`plan-wrong-order`. Each is blocked by exactly the policy named in `docs/policies.md`.

## 7. Risks, trade-offs and what the scenario deliberately leaves open
| Item | Treatment |
|---|---|
| Behaviour change for all future links | Surfaced as a high-risk question, decided by a human, recorded in the changelog |
| Links with an expiry are never reused by the idempotent `shorten` | Listed as a risk in the impact analysis; two submissions of the same URL now yield two links |
| The web UI's "Never" option now means "server default" | **Flagged, not fixed**: out of scope per the declared impact; needs a follow-up |
| Existing links | Untouched by constraint; no migration |
| Mock agents | Their output is a function of the recorded decisions, so changing an answer really changes the artifacts, but a real model's behaviour is not demonstrated |

# AI usage log

Tools: Claude (chat). Purpose of this log: show what the AI produced, what I verified, and what I changed.

| # | Prompt / task (summary) | What AI produced | What I verified / changed |
|---|---|---|---|
| 1 | Plan the assignment and project structure | Deliverables checklist, folder layout, DAG design | Chose a custom engine over LangGraph as the primary system, because it already covers the required orchestration controls |
| 2 | Engine skeleton and thin vertical slice | Graph, engine, mock LLM, hash-chained audit log, metrics, policies | Ran every demo; a run-ID collision bug was found by real runs, not unit tests |
| 3 | Simpler LangGraph-style explainer | `simple_graph/` | Ran all scenarios under both the plain runner and LangGraph on my machine |
| 4 | Add rollback, fallback, backoff | Engine changes, ADR 001, tests | Mutation-tested: disabling rollback, fallback or the fallback exit gates each fails specific tests. Two older tests changed because the fallback now rescues what used to halt; I reviewed that change |
| 5 | Add security, compliance and change-control policies | Policy framework, 15 named policies, workflow linter, re-plan limits, ADR 002, policy catalog | Mutation-tested five behaviours (allowlist, fail-closed compliance, re-plan budget, linter, protected paths). My own tests caught a metrics bug (one policy counted twice per blocked output); fixed and documented |
| 6 | Build the URL shortener as the greenfield scenario | Stdlib service (SQLite + WSGI), 27 tests, docs, shortener workflow, CLI generalisation, scenario write-up | Found my own test bug (probe URL had spaces, which the validator correctly rejected). Mutation-tested the service with 9 injected defects (all caught). Added a real-HTTP end-to-end test. Chose stdlib over FastAPI because the sandbox gate runs stdlib-only and nothing needs installing |
| 7 | Make the shortener tests individually runnable; add a live smoke test | Per-test pytest wrapper over the artifact tests, `scripts/smoke_test.py`, `make_link.py`, `pytest.ini`, `docs/testing.md` | Hit two real problems on my own machine: a script run from `tests/` could not import the package (path), and a `*_test.py` name would be collected by pytest. Fixed with `pytest.ini` and an import-safe script. Mutation-checked all four test levels |

## Rules I apply to AI output
* Nothing is accepted without a runnable demo or test.
* Behaviour changes to existing tests are reviewed deliberately, not "fixed" to go green.
* I must be able to explain every component in an interview.

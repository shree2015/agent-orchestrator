# Scenario 1: Greenfield - build the URL shortener

**Requirement (input):** "Build a URL shortener service: shorten, redirect with expiry, click analytics, custom
aliases, rate limiting and basic abuse protection. Ship tests and docs."

## 1. Decomposition (the dependency graph)
```
requirements -> plan -> design* -> implementation -> tests  ->\
                                          -> docs   ->  review -> release*
                                          -> ui     ->/
```
`*` = human approval checkpoint (design and release are tagged `high_impact`, and the workflow linter
refuses to run if they lack approval). `tests`, `docs` and `ui` run **in parallel** after `implementation`; `review` is
the synchronisation point and waits for all three.

| Node | Agent | Exit gate(s) that must pass before the output is committed |
|---|---|---|
| requirements | requirements | valid JSON with goal / functional / ambiguities / acceptance |
| plan | planner | tasks acyclic, mapped to real stages, ordered as the workflow can execute, every acceptance criterion traced to tests or a gate |
| design | architect | human approval |
| implementation | developer (fallback: developer_fallback) | valid Python; security policies; public API documented; change-control limits |
| tests | tester | valid Python; **tests pass against the implementation in a sandbox**; at least 3 tests; every test the plan promised exists |
| docs | doc_writer | docs mention every public function/class |
| ui | frontend | HTML/JS security (no external resources, no innerHTML/eval, no inline handlers or styles, CSP-compatible blocks); documented API used; accessibility basics; change-control limits |
| review | reviewer | must start with APPROVE |
| release | release_manager | **entry gate** re-verifies all evidence (including the UI and the plan); then human approval |

## 2. Orchestration behaviours to show
Run each and open `runs/<id>/report.md` (nodes, governance timeline, lineage, metrics):

```
python -m orchestrator.cli run shortener --approve auto                      # clean run
python -m orchestrator.cli run shortener --approve auto --violate secret     # policy blocks a leaked key, developer retried
python -m orchestrator.cli run shortener --approve auto --fail developer:5   # retries exhausted -> fallback agent
python -m orchestrator.cli run shortener --approve auto --violate thin-tests # compliance halts the release
python -m orchestrator.cli run shortener --approve auto --revise-design fail # re-plan fails -> whole change rolled back
python -m orchestrator.cli run shortener --approve deny:release              # human refuses the release
```

## 3. Validation (what makes the output trustworthy)
* The generated service is **executed**: its 32 tests run in an isolated process; a failing test blocks the commit.
* **Mutation check** (done by hand during development): 9 realistic defects injected into the service
  (expiry ignored, private hosts allowed, analytics failure breaking redirects, orphaned clicks, no idempotency,
  limiter never triggering, no collision retry, no alias reclaim, header injection) were all caught by those tests.
* `tests/test_ui.py` also drives the web page's real script (Node harness, fake DOM) against a live server.
* `tests/test_shortener_workflow.py` starts the generated file as a **real HTTP server** and drives shorten,
  redirect, stats and an SSRF-style rejection over the network.
* Governance evidence is in the hash-chained `audit.jsonl` (verified in the report: "Audit chain intact").

## 4. Key risks and trade-offs
| Risk | Control | Residual |
|---|---|---|
| Agent leaks a credential | `no-hardcoded-secrets` gate | pattern-based, misses obfuscation |
| Agent adds an unreviewed dependency | `dependency-allowlist` (stdlib only) | allowlist must be maintained by humans |
| Oversized change | `max-lines-changed` (900 for a new service by default, explicit and set per run) | numeric limits are a proxy for risk |
| Open redirect / SSRF abuse via submitted URLs | private/loopback/credentialed/non-ASCII URLs rejected | no DNS resolution, so a public name pointing at a private IP gets through |
| Abuse of write endpoints | per-client fixed-window rate limit, `Retry-After` | per-process state; behind a proxy needs trusted-header handling |
| Anyone can delete any link | none yet | needs owner tokens / auth (documented limitation) |

## 5. Honest limitation of the demo
The agents are an **offline mock** that return reviewed reference artifacts, so the graph, gates and failure
paths are real but the *generation quality* is not being demonstrated. A real LLM client plugs into the same
`LLMClient` interface; every gate would then judge genuinely generated code.

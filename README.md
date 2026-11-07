# Agent Orchestrator

Governed, agentic software-engineering automation: a prototype that turns a requirement into a reviewable
engineering outcome, with agents executing under explicit controls and humans owning approvals.

**Version: v0.1.0 (greenfield baseline)**

## What this version contains
* **Orchestration engine** (`orchestrator/core`): explicit dependency graph with entry/exit gates, parallel branches
  with a join, stateful execution, bounded retries with exponential backoff, fallback agents, checkpoint and
  rollback of failed re-plans, safe-stop, human approval checkpoints, change-controlled re-planning.
* **Governance** (`orchestrator/governance`): policy-as-code for security, compliance and change control (every
  block names its policy), a workflow linter that refuses to run a workflow missing mandatory controls, a
  sandbox that executes generated tests, a hash-chained audit log, and reliability metrics (success rate, retries,
  fallbacks, rollbacks, MTTR, latency).
* **Scenario 1, greenfield:** a URL shortener (standard library, SQLite, WSGI) built through the governed graph:
  code, 27 tests and docs, each passing its gates.
* **`simple_graph/`:** a small LangGraph-style version of the same idea, useful for understanding the design.
* Agents are an **offline mock LLM** that returns reviewed reference artifacts, so everything runs without API
  keys. The gates judge the real code either way.

**Planned for later versions:** web UI, brownfield scenario, automatic re-planning, ambiguous-requirement scenario,
real LLM client, architecture overview and final engineering summary.

## Setup (Python 3.10+)
```
python -m venv venv
venv\Scripts\activate            # Windows        (Linux/macOS: source venv/bin/activate)
pip install -r requirements-dev.txt
```

## Run the tests
```
python -m pytest -v
```
Expected: **128 passed, 1 skipped**. The skipped test compares the plain runner with LangGraph; install
`langgraph` (optional) and it runs too. Each of the shortener's 27 own tests is listed by name in
`tests/test_shortener_service.py`.

## Try it
```
python -m orchestrator.cli run shortener --approve auto                       # build the URL shortener
python runs/<run-id>/artifacts/implementation.py 8000                          # run the generated service
python scripts/smoke_test.py                                                   # 25 checks against the running server
python scripts/run_shortener_tests.py                                          # list the shortener's own tests
```
Governance and resilience demos (use `slugify` for the toy workflow or `shortener` for the real one):
```
python -m orchestrator.cli run shortener --approve auto --violate secret        # policy blocks a leaked key, agent retried
python -m orchestrator.cli run shortener --approve auto --violate import        # unapproved dependency blocked
python -m orchestrator.cli run shortener --approve auto --violate thin-tests    # compliance policy halts the run
python -m orchestrator.cli run shortener --approve auto --fail developer:5      # retries exhausted -> fallback agent
python -m orchestrator.cli run shortener --approve auto --revise-design ok      # design change -> re-plan, downstream redone
python -m orchestrator.cli run shortener --approve auto --revise-design fail    # re-run breaks -> change rolled back
python -m orchestrator.cli run shortener --approve deny:release                 # human refuses the release -> safe-stop
python -m orchestrator.cli run shortener                                        # interactive approvals
```
Every run writes `runs/<id>/{audit.jsonl, metrics.json, report.md, artifacts/}`; the report includes a governance
timeline of retries, fallbacks, re-plans, rollbacks and approvals.

Simple graph version: `python -m simple_graph.demo happy` (also `fix_loop`, `always_bad`, `reject_once`,
`--engine langgraph`, `--ask`).

## Layout
```
orchestrator/   engine, governance (policies, gates, sandbox), observability, workflows, mock LLM
simple_graph/   small LangGraph-style version
tests/          unit, scenario and end-to-end tests
scripts/        smoke test for a running server, helpers
docs/           testing approach, policy catalog, decisions (ADRs), scenario write-up, AI usage log
```

## Documentation
* `docs/scenarios/01-greenfield-url-shortener.md` - decomposition, orchestration, validation, risks
* `docs/policies.md` - catalog of every policy and where it is enforced
* `docs/testing.md` - testing approach and known gaps
* `docs/decisions/` - ADR 001 (resilience ladder), ADR 002 (policy-as-code)
* `docs/ai-usage-log.md` - how AI was used and how its output was verified

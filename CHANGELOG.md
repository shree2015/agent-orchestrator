# Changelog

## v0.1.0 - greenfield baseline

### Added
- Orchestration engine: dependency graph with entry/exit gates, parallel waves with join, bounded retries with
  exponential backoff, fallback agents, checkpoint and rollback, safe-stop, human approvals, change-controlled re-planning.
- Governance: policy-as-code for security, compliance and change control (15 named policies), workflow linter,
  sandboxed test execution, hash-chained audit log, reliability metrics.
- Greenfield scenario: URL shortener (shorten, redirect, expiry, custom aliases, click analytics, rate limiting,
  abuse protection) generated through the governed graph, with 27 tests of its own and documentation.
- Test suite (128 tests): engine, resilience, policies, workflow scenarios, real-HTTP end-to-end, smoke-test script.
- Docs: testing approach, policy catalog, ADR 001 and 002, greenfield scenario write-up, AI usage log.
- `simple_graph/`: small LangGraph-style version of the design.

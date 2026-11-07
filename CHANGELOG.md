# Changelog

## v0.5.4 - refusals are delivered cleanly (Windows connection resets)

### Fixed
- **A rate-limited request could arrive as "connection reset" on Windows** instead of the 429 reply (the smoke test's 40-request
  burst failed with `WinError 10054`). The service and the admin server replied to refused requests (429, 405, 404, 403, 413 and
  the Stop button's POST) **without reading the request body**; closing a connection that still holds unread incoming data makes
  Windows reset it, discarding the reply. Both servers now always read (and discard) the body first, in chunks and bounded at
  1 MiB, whatever the outcome. The brownfield baseline and fix are re-derived from the corrected service (same scenario, same
  31/3/34 test results, same 3 changed symbols).
- The plain-English requirement template now states the rule, so a real model is asked for it too.

### Added
- `tests/test_request_bodies.py`: checks directly that every refusal path consumes the body (all three service versions and the
  admin server), that draining is bounded, and sends a real 40-request burst over sockets.


## v0.5.3 - the Start button works with the default (relative) runs folder

### Fixed
- **"Start this service" failed with `FileNotFoundError ... service\runs\run-...\artifacts\implementation.py`** when the admin
  page was started the normal way (`python -m orchestrator.admin`, whose runs folder is the relative `runs`). The launcher starts
  the service in the run's own `service` folder, so a relative path to the service file was looked up from the wrong place.
  Every path handed to the child is now absolute, and the admin page resolves its runs folder once at start-up.
  Found on a Windows machine: all earlier tests used absolute folders and could not hit it.

### Added
- Two regression tests that use a relative runs folder, exactly as a default setup does.


## v0.5.2 - stop the whole service process tree (Windows fix)

### Fixed
- **Windows:** stopping a generated service left the real service process running as an orphan, with its port still open,
  because a virtual environment's `python.exe` is a launcher that starts the real interpreter as a child. Services are now
  started in their own process group and stopped as a whole tree (`taskkill /T /F` on Windows, `killpg` elsewhere), and
  "stopped" is reported only after the port has really closed. Found by a failing test on a Windows machine.
- Two older tests that started a generated service as a subprocess now stop the whole tree too, so they no longer leave
  stray servers behind on Windows.

### Added
- Regression tests that reproduce the launcher situation on any operating system (a parent that starts a listening child).


## v0.5.1 - start the generated service from the admin page

### Added
- **Start this service** / **Stop service** in the admin page's results, offered only for runs that produced a runnable service.
  The service starts on a free port the OS chooses, is reachable only from this computer, and its database and log live in
  `runs/<id>/service/`.
- Enforced limits: every `bind` in the child process is forced onto loopback (even if the code asks for `0.0.0.0`); the child's
  environment is scrubbed of credentials (the API key never reaches generated code); the security policies are re-run on the
  file right before launch; one service at a time; real-model code and runs of unknown origin need an explicit confirmation
  that the server enforces.
- API: `GET/POST/DELETE /api/service` (changes need the admin token); `runnable` flag on runs. ADR 008.
- 16 service tests (including inspection of the real listening socket and the child's environment) and 11 more page checks.

### Fixed
- A deliberate stop of a service is no longer reported as "stopped on its own".


## v0.5.0 - admin page

### Added
- `python -m orchestrator.admin`: a local control panel (standard library; one HTML page plus a JSON API). Write a
  requirement, switch between **mock and real agents**, generate; watch steps, an activity feed and metrics live; make
  every human decision on the page (design/release approvals that show what is approved, clarifying questions, stop,
  change an answer with an automatic re-plan); open artifacts, read the report, download a zip, verify the audit chain;
  browse earlier runs.
- Real mode needs an explicit confirmation and shows a call budget; the API key comes from the environment or is pasted
  and held in memory only (never written, logged, recorded or shown back).
- Local-only security: loopback bind, Host check (DNS rebinding), per-start token and Origin check (CSRF), size limits,
  hash-based CSP, `textContent` rendering; the page passes the pipeline's own UI security and accessibility policies.
- `Engine.stop()`; runner hooks `on_start` and `write_outputs`; `AdminState.shutdown()` so Ctrl+C never hangs on a
  waiting approval.
- Docs: `docs/admin-page.md`, ADR 007. Tests: 26 API tests and a 31-check script harness run against a real server.


## v0.4.0 - real LLM support and requirements in your own words

### Added
- Real LLM client for the Anthropic Messages API (standard library only): key from `ANTHROPIC_API_KEY`, retries with
  backoff for rate limits and server errors, fail-fast on authentication/model errors, hard call budget, truncated
  answers rejected, per-call audit events (role, model, tokens, latency, prompt hash, request id), key never logged.
- CLI: `--llm anthropic`, `--llm-roles` (hybrid), `--model`, `--max-tokens`, `--max-llm-calls`, `--llm-timeout`,
  `--record DIR` (saves every prompt/response; with the mock it is a free preview), `--requirement`, `--requirement-file`.
- Role output contracts and artifact cleaning (fences/preamble removed; clean artifacts never altered).
- Metrics: `llm_calls`, `llm_input_tokens`, `llm_output_tokens`.
- `examples/requirements/url-shortener.md` (plain-English requirement template) and `docs/real-llm.md`, ADR 006.

### Changed
- **Retries now carry feedback**: an agent is told why its previous attempt was rejected and shown that attempt's output;
  a fallback agent learns why the primary failed. Gates still judge the real committed context.
- The original request text is passed to the architect, developer and front-end agents; the tester and architect see the plan.


## v0.3.1 - complete deliverables for changes to an existing service

### Fixed
- Runs of the brownfield and ambiguous workflows wrote only the files they changed, so starting the generated service
  printed "Web UI: not found". The run now carries over the existing, unchanged `ui.html` next to the changed code
  (`run_workflow(..., assets=...)`, listed in the report). Carried-over files never overwrite agent output.

### Added
- End-to-end tests that start the generated ambiguous-scenario service and check the UI is served and the decided
  default expiry (and the alias exception) is applied over real HTTP.

## v0.3.0 - automatic re-planning, planner, ambiguous scenario

### Added
- Automatic re-planning: content fingerprints for every output, per-node records of the upstream fingerprints it
  consumed, stale-node detection and `Engine.revise()`. A changed upstream output redoes exactly the dependent
  nodes; an identical revision does nothing. Revisions run the node's exit gates, respect the re-plan budget and
  are checkpointed, so a failed re-run rolls back the whole change including the edit. New metrics: `revisions`,
  `revision_noops`, `revisions_rejected`, `auto_replans`, `nodes_replanned`.
- Planner stage in the greenfield workflow and 5 planning policies (well-formed, maps to workflow, sequencing matches
  the graph, acceptance criteria traced, promised tests exist).
- Ambiguous scenario ("make links expire sensibly", FEAT-207): structured questions with risk levels, human decision
  capture (`--answer`, `--approve ask`), 6 clarification policies (high-risk needs a human, answers within options,
  assumptions need rationale, consistency, decisions visible in code), decision-aware mock agents.
- CLI: `--answer`, `--revise-answer`, `--break-on-revise`; exit code 3 for a refused revision; new `--violate` demos.
- Docs: scenario 3 write-up, ADR 004 and 005, policy catalog and testing approach updated.

### Changed
- Gates that crash during a revision reject it instead of crashing the engine; policies that cannot evaluate a rule
  report a violation (fail closed).
- Mock LLM answers may depend on the prompt (so outputs follow what the agent read).
- Release evidence for the greenfield workflow now includes the plan.


## v0.2.0 - web UI and brownfield scenario

### Added
- Web UI for the shortener: one self-contained page served at `/`, generated by a new `ui` pipeline node (parallel
  with tests and docs), with a hash-based Content-Security-Policy and no external resources.
- 7 UI policies (5 security, 2 compliance), a UI test harness that runs the page's real script against a live
  server in Node, and a test that starts the generated service as a script and fetches the page.
- Brownfield scenario (BUG-142, orphaned click analytics): `impact_analysis` stage, AST code-intelligence helpers,
  6 policies (grounded impact analysis, declared-impact scope, backward-compatible public API, no removed tests,
  regression test must fail on the old code, changelog references the ticket), baseline and fix artifacts.
- `--violate` demos are now defined per workflow (brownfield and UI ones added).
- Docs: brownfield scenario write-up, ADR 003, policy catalog and testing approach updated.
- `scripts/run_shortener_tests.py` can run the brownfield baseline and fixed sets.

### Changed
- All file reads and writes are explicitly UTF-8, and console output tolerates non-UTF-8 Windows consoles.
- The release gate takes a per-workflow evidence list, and the workflow builder supports an optional UI node.
- The shortener service gained `csp_for` and an optional `ui_html` argument on `make_app`; test count 27 -> 32.


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

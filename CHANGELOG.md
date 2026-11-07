# Changelog

## v0.8.1 - the Start button for a real model's own code (found on the first successful live run)

### Fixed
- **After a successful run with Sonnet, the admin page offered no way to start the service.** The check for "runnable" required the literal
  function names `make_app` and `serve` (our reference service's internals), but the requirement template only asks for a program started
  with `python implementation.py 8000` that serves `ui.html`. Sonnet structured its service its own way, so the panel never appeared.
  "Runnable" now means *behaviour*: an `if __name__ == "__main__":` block and a web server being started.

### Added
- When a finished service run cannot be started, the page says why (no `__main__` block, or no web server) and how to run it by hand.
- If a program dies at start-up complaining about its arguments, the launcher tries `--port N` once (the template asks for a positional
  port, but a model may read it differently). `PORT` is also set in the child's environment. Any other crash is reported, not retried.
- 5 tests (a model-style service, the same code under different names, the `--port` form, an unrelated crash, the explanatory note).

### Changed
- The documents now record the live result honestly: one complete live run of the greenfield shortener with Sonnet (every gate and
  approval passed), after four fixes. One run is an existence proof, not a measure of reliability.


## v0.8.0 - review findings are sent back for rework (found on the second live run)

### Fixed (test only, found on the author's Windows run)
- `test_the_admin_page_lists_the_rejected_output_and_says_where_to_look` compared a saved artifact as the page serves it (CRLF line endings
  on Windows) with an LF string, so it failed on Windows and passed on Linux. It now ignores line endings. No product code changed.

### Why
The second live run's reviewer was partly **right**: Sonnet's first draft really did accept non-ASCII URLs and then crash the redirect
(the stored reference service already rejects them with a clear 400). The pipeline then halted. A real model's first draft will often
have some defect, so a pipeline that stops at the first rejection cannot converge. A reviewer's REJECT is a verdict about earlier work,
not a malformed answer to retry.

### Added
- **Review-driven rework** ([ADR 009](docs/decisions/009-review-driven-rework.md)): a node may declare `rework=Rework(target, when)`.
  A reviewer's REJECT now sends its findings back; the code stage and everything downstream are redone, each producing stage told the
  findings and shown its own previous output, and the review runs again. Bounded by `--max-reworks N` (default 2; 0 = halt at once),
  checkpointed (a failed rework rolls back whole) and audited (`rework` event, `reworks` and `reworks_refused` metrics, a dashed arrow
  in the workflow graphs). A reply that is not a verdict is retried as before. The graph rejects a rework target that is not upstream.
- 8 tests (`tests/test_rework.py`).

### Changed
- The reviewer is told how tests work here: a separate tester writes them and they run in the same namespace as the implementation, so
  they intentionally do not import it (a "missing import" is never a finding), and tests embedded in the implementation are not blocking.
- The developer is told **not to embed tests** in the module (the 687-line service contained its own test suite, which duplicated the
  tester's and inflated the size).
- The reviewer now gets one retry (a re-check of its own verdict) before a rework, not two.


## v0.7.2 - a reviewer that knows the request, says why, and re-checks (found on the second live run)

### Fixed
- **Second run with a real model (Sonnet) halted at `review`: three rejections, and only "reviewer did not approve" to show for it.**
  Three faults:
  - the reviewer judged the code, tests and documentation **without the original request, the web UI, or any statement of what had
    already been verified**, so a real critic had to guess what the software was meant to do;
  - its **reasons were discarded**: the gate said only "reviewer did not approve" (the verdict survived only in the recording file);
  - a retry told it that its verdict "was rejected by automated checks", so it repeated itself. Three identical calls were wasted.

### Changed
- One shared reviewer definition for all three workflows: the request is the standard; it is told the automated checks have already
  passed; it may REJECT only for a **blocking** defect (behaviour contradicting the request, a security flaw, tests that do not exercise
  the request, wrong documentation); style and optional improvements are not blocking. Brownfield and ambiguous add the impact
  analysis or the recorded decisions to its inputs.
- A reviewer retry now asks it to **re-check its own verdict** against the request and the definition of blocking.
- The gate message now includes the reviewer's reasons (shortened), so they appear in the activity feed, the halt reason and the audit log.

### Added
- **The last rejected output of a stage that stopped the run is kept** as `artifacts/REJECTED-<stage>` (and listed in the report and on
  the admin page), so a halted real-model run can be diagnosed from the run folder.
- 12 tests (`tests/test_review.py`).


## v0.7.1 - size limits the model knows about and the operator can set (found on the first live run)

### Fixed
- **First run with a real model (Sonnet) halted at `implementation`: `[change_control/max-lines-changed] 687 lines changed; limit is 600`.**
  Two gaps, not one: the model was never told the limit until it was rejected, and the limit could only be changed by editing code,
  although the documentation said to "raise the limit deliberately".

### Added
- The developer, fallback and UI prompts now state the size limit and ask the model to aim for about 80% of it.
- `--max-lines-changed N` and `--max-ui-lines N` (command line) and a **Size limits** section on the admin page set the limits per
  run (50 to 5000 lines). Workflows with scenario-fixed limits (brownfield, ambiguous) refuse them.
- A new first audit event, `run_config`, records the settings of every run (mode, model, size limits; never secrets or the
  requirement text). The activity feed shows it, and a run stopped by a size limit says how to continue.

### Changed
- Defaults for a whole generated service are now 900 lines for the code and 700 for the UI (they were 600 and 500; the stored
  reference service is 507 and 339 lines, and a real model is wordier). Limits are still enforced.
- 10 tests (`tests/test_size_limits.py`), including the exact 687-line case.


## v0.7.0 - final engineering summary

### Added
- `docs/final-summary.md` (and a PDF): the brief's requirement 8. Plan and rationale, artifacts, validation evidence, a risk register,
  assumptions, limitations and next steps, and an open account of how AI was used and how its output was checked.
- The summary's evidence is **generated by running the system**: tests by area (counted from the code), each of the three scenarios run
  for real (stages, waves, approvals, the brownfield bug reproduced and fixed, a re-plan after changing an answer), and every one of the
  24 scripted fault scenarios, recorded in `docs/architecture-reference.md` with the control that stopped it.
- Version numbers and counts quoted in the documents (version, policies, events, faults, tests) are generated from the code and README.

### Changed
- `docs/ai-usage-log.md` now says who did each check. It previously mixed first-person voice for checks Claude had performed; it is
  now explicit about what Claude did in its Linux sandbox and what the author did on Windows. Two rows were added.
- Requirement 8 in the traceability document is now **Met**.


## v0.6.0 - architecture overview and requirements traceability

### Added
- `docs/architecture.md` (and a PDF): components and layers, orchestration model, control flow of a node, dynamic change,
  governance, human oversight and trust boundaries, observability, the three scenarios as instances of one engine, agents and the
  LLM layer, a decision-and-trade-off table, quality attributes and limits. Diagrams are Mermaid.
- `docs/architecture-reference.md`: generated from the real code by `scripts/gen_architecture_docs.py`: every workflow graph, the
  controls on every stage, the policy catalogue (discovered by calling every policy factory), the event catalogue (read from the
  engine's own audit calls) and the metrics.
- `docs/requirements-traceability.md` (and a PDF): each item of the brief mapped to code, evidence and an honest status, with the
  known gaps stated.
- `scripts/build_docs_pdf.py` and `scripts/html_to_pdf.js`: developer tools that render the Mermaid diagrams and build the PDFs.
- `tests/test_docs.py`: fails if a generated part is out of date, a document names a file or link that does not exist, a policy in
  the code is missing from `policies.md`, or a diagram is malformed.
- Gates now report what they enforce (`describe_gate`, `policy_ids`, `label`), which is what makes "which control guards which
  stage" readable from the real graph.


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

# Testing approach

Testing happens at four levels. Each answers a different question, and each can fail independently.

| Level | What it tests | Where | How to run |
|---|---|---|---|
| 1. Engine and governance unit tests | Graph, retries, fallback, rollback, backoff, policies, linter, re-plan limits, metrics | `tests/test_core.py`, `test_resilience.py`, `test_policies.py`, `test_slice.py`, `test_simple_graph.py` | `pytest -v` |
| 2. Shortener service tests | The generated service's behaviour: validation, expiry, idempotency, analytics, rate limiting, concurrency, HTTP mapping | `orchestrator/workflows/reference/shortener/tests.py.txt`, exposed one-per-item by `tests/test_shortener_service.py` | `pytest tests/test_shortener_service.py -v` |
| 3. Orchestration scenario tests | The whole governed pipeline building the shortener, under each failure and policy scenario, plus a real HTTP run of the generated file | `tests/test_shortener_workflow.py` | `pytest tests/test_shortener_workflow.py -v` |
| 3b. Web UI | UI security/compliance policies; the page's real script driven by a Node harness against a live server (needs Node 18+, skipped otherwise); generated service started as a script and `/` fetched | `tests/test_ui.py`, `tests/ui_harness.js` | `pytest tests/test_ui.py -v` |
| 3c. Brownfield | Scenario integrity (bug is real, fix works), each guard in isolation, every violation under the orchestrator | `tests/test_codeintel.py`, `tests/test_brownfield.py` | `pytest tests/test_brownfield.py -v` |
| 3d. Re-planning, planning and ambiguity | Fingerprints, stale detection, `revise()` and its governance; planning and clarification policies; the ambiguous scenario end to end (blocked, answered, revised, rejected, rolled back) | `tests/test_replan.py`, `tests/test_planning.py`, `tests/test_clarification.py`, `tests/test_ambiguous.py` | `pytest tests/test_replan.py tests/test_ambiguous.py -v` |
| 3e. Real-LLM support | The real client against a fake Anthropic server (request shape, retries, fatal errors, budget, key never leaks), contracts and cleaning, retry feedback, hybrid routing, recording, whole workflows through the real client | `tests/test_real_llm.py` | `pytest tests/test_real_llm.py -v` |
| 3f. Admin page | Request security (Host, token, Origin, limits), key handling (never echoed or stored), validation, every way a run can go through the API (approvals, rejection, stop, questions, revision, real mode against a fake server), launching generated services (loopback forced, environment scrubbed, confirmation, policy re-check), the page's script clicked through against a real server | `tests/test_admin_api.py`, `tests/test_admin_service.py`, `tests/test_admin_ui.py`, `tests/admin_harness.js` | `pytest tests/test_admin_api.py tests/test_admin_ui.py -v` |
| 4. Live smoke test | A running server, driven over the network like a real client | `scripts/smoke_test.py`, checked by `tests/test_smoke_script.py` | `python scripts/smoke_test.py` |

## Single source of truth for the shortener tests
The 32 service tests are the **same file** in three roles: the quality gate inside the orchestrator (run in an
isolated sandbox before code is committed), individual pytest items (for readable CI output), and the
reference artifact a mock agent returns. A guard test fails if pytest and the sandbox ever disagree about
which tests exist, so tests cannot be dropped silently.

## How the tests are made trustworthy
* **Mutation checks.** Realistic defects were injected on purpose (expiry ignored, private hosts allowed,
  analytics failure breaking redirects, orphaned clicks, no idempotency, limiter never triggering, no collision
  retry, no alias reclaim, header injection) and the suite must go red. 9 of 9 were caught by the service tests
  alone. Disabling expiry fails tests at all four levels: the individual service tests, the orchestrator
  scenarios (the gate rejects the code), and the smoke tests.
* **Deterministic time.** The service takes an injectable clock, so expiry, per-day analytics and rate-limit
  windows are tested without sleeping.
* **Fail-closed checks tested explicitly.** Missing compliance evidence must block; a workflow lacking a
  mandatory control must refuse to start.
* **The checker is itself tested.** `test_smoke_script_detects_a_broken_service` proves the smoke test fails
  against a broken build, not only that it passes against a good one.

## Running everything
```
pip install pytest
pytest -v                          # all levels except the long-form manual checks
python scripts/smoke_test.py       # against a server you started yourself
```
`pytest.ini` limits collection to `tests/`, so scripts in `scripts/` are never mistaken for tests.

## Platform differences found by running on Windows
Everything above was first built and tested on Linux. Three bugs only appeared when the suite was run on a Windows machine,
each fixed and covered by a regression test that reproduces the cause on any OS:
1. **Process trees**: a virtual environment's `python.exe` is a launcher with a child, so stopping only the launcher orphaned the
   service and its port. Services now start in their own process group and are stopped as a tree.
2. **Relative paths**: a child started in another working folder could not find a file given by a relative path. Every path
   handed to a child is now absolute.
3. **Unread request bodies**: closing a connection that holds unread data resets it on Windows, so a 429 arrived as "connection
   reset". Both servers now read the body before replying.
The lesson: test with the defaults and the operating system a user actually has, not only a convenient setup.

## Testing a real model without calling one
The real client is exercised through an injectable transport: a fake server answers with the same JSON format as the
API, so retries, error handling and budgets are tested for real, and entire workflows run through the real client code.
What this cannot show is how a live model behaves with the prompts; that needs your first live runs (see `docs/real-llm.md`).

## Testing nondeterminism
Parallel waves are real threads. A race I found while writing the ambiguous tests: when a node blocks, siblings in the
same wave may or may not have started. The engine guarantees only that nothing from a *later* wave starts, so the tests
assert exactly that; the racy assertion was removed after repeating the suite to measure it. The full suite is run
several times in a row before delivery.

## UI testing without a browser
There is no browser in the test environment, so `tests/ui_harness.js` runs the page's own inline script in Node
with a minimal fake DOM and the real `fetch`, against a real server. It checks health display, creating links,
expiry display, click refresh, server error messages, rate-limit messaging, that markup in a URL is shown as
text, delete, and local-storage persistence. Four injected JavaScript defects were all caught. What it cannot
show: real layout, styling and cross-browser behaviour, which need a manual look in a browser.

## Known gaps
No visual or cross-browser UI testing; no load or soak testing (the development server is single-threaded); no multi-process or real-database
tests; DNS-based SSRF is untested because the service does not resolve names; authentication does not exist
yet; LLM-generated code quality is untested because the agents are offline mocks.

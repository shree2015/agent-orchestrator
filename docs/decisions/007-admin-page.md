# ADR 007: A thin local admin page; humans decide through blocking hooks

**Status:** accepted

## Context
Human oversight was a terminal prompt. A page makes controlled autonomy visible (approvals, questions, stop) and lets a
person start runs and switch between mock and real agents without remembering flags.

## Decision
1. **Thin layer.** The page calls the same `run_workflow` in a background thread. No pipeline logic lives in the page.
2. **Humans decide through the existing hooks.** The engine's approval hook and the clarifying-question hook simply *wait*
   for the page; no change to how the engine decides. Two small engine/runner additions: `Engine.stop()` (a safe-stop on
   request, first reason wins) and runner hooks (`on_start`, reusable `write_outputs`) so artifacts and report can be
   rewritten after a later revision.
3. **One active run at a time**, state in memory, finished runs on disk. Simple and predictable; concurrency is not needed.
4. **Local-only security**: loopback bind, Host check, per-start token plus Origin check for state changes, size limits,
   hash-based CSP, `textContent` only.
5. **Key handling**: environment first; a pasted key lives only in memory, is never echoed, written or stored in the browser.
6. **Real mode needs explicit consent** (a confirmation, a visible call budget) and defaults to mock.
7. **Standard library only** (WSGI + one HTML file), consistent with the rest of the project, and tested the same way
   (API tests plus the page's script run against a real server).

## Alternatives considered
* *A web framework + front-end build*: nicer ergonomics, but new dependencies and tooling for a prototype.
* *Server push (SSE/WebSocket)*: lower latency, more moving parts; polling at 0.7 s is enough and robust.
* *Multiple concurrent runs*: needs per-run isolation of approvals and keys; not required for the demo.

## Consequences
* (+) Everything the page shows is true of the command line too; nothing can drift.
* (+) Process shutdown releases waiting decisions, so Ctrl+C never hangs on an unanswered approval (found by a test).
* (-) No authentication or multi-user support: it must stay on a trusted local machine.
* (-) A running run is lost if the process exits.

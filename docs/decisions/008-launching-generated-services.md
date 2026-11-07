# ADR 008: Launching a generated service from the admin page, with enforced limits

**Status:** accepted

## Context
After a run, the next thing a person wants is to try the service it built. Doing that from the admin page means running
generated code with the user's own permissions, outside the test sandbox. For the reviewed mock output that is reasonable;
for output written by a real model, the pipeline's gates catch many unsafe patterns but are not a guarantee.

## Decision
1. **The button only exists for runs that produced a runnable service**: an implementation that can be started as a program and
   starts a web server (an `if __name__ == "__main__":` block and use of `wsgiref` or `http.server`). A toy-function run gets no button.
   The check looks at *behaviour*, never at the function names a particular implementation chose. (It first required our reference's
   `make_app` and `serve`, so the first successful live run, whose service was structured differently, got no Start button. When a
   service run cannot be started the page now says why. If the program reads its port as `--port N` instead of the positional port
   the requirement template specifies, the launcher tries that form once.)
2. **Loopback is enforced, not requested.** The service is started through a small wrapper that rewrites every `bind()` to
   127.0.0.1 (or ::1) inside the child process, so generated code that asks for `0.0.0.0` still listens only on this machine.
   A test inspects the real listening socket.
3. **The child gets a scrubbed environment**: names that look like keys, tokens, secrets, passwords or credentials are
   dropped, so the API key never reaches generated code.
4. **The child runs in its own folder** (`<run>/service/`): its database and log stay with the run.
5. **The security policies are re-run on the file immediately before launch** (it may have been edited since the run).
6. **Non-mock code needs explicit confirmation**: real-model output and runs of unknown origin (no `run.json`). The page shows
   the confirmation; the server refuses without it, whatever the page does.
7. **One service at a time, on a free port the OS chooses**, stopped when stopped, when the admin page exits, or when it
   dies (a service that dies on its own is reported; a deliberate stop is not).
8. The launch endpoints need the same admin token and Origin checks as every other state change.

## Addendum: stop the whole process TREE (found on Windows)
On Windows a virtual environment's `python.exe` is a launcher that starts the real interpreter as a *child*. Stopping only the
launcher left the real service running and its port open; the admin page said "stopped" while the port still answered. The child
is now started in its own process group (a POSIX session, or a Windows process group) and stopped as a tree (`killpg`, or
`taskkill /T /F`), and `stop` reports "stopped" only after the port has really closed. A regression test reproduces the situation
on any operating system with a parent that starts a listening child. (The wait-for-port step is a safety margin for Windows'
slower cleanup and is not directly tested, because Linux releases the port instantly.)

## Alternatives considered
* *Tell the user to open a second terminal*: simplest and safest, but clumsy for a demo.
* *Run the service inside the test sandbox*: it is built for short test runs (timeouts, isolation flags), not a long-lived server.
* *A container or VM*: the right production answer; far too heavy for a local prototype.

## Consequences
* (+) One window does everything, and the dangerous parts (bind address, secrets, folder, policy re-check) are enforced and tested.
* (-) **Not a sandbox.** Outbound network access and file access are the user's normal permissions. That is why real-model
  code needs the confirmation, and why this stays a local, single-user tool.
* (-) Loopback is forced at the socket level for Python's `socket`; code that spawned another program or used a different
  mechanism could bypass it. The policies already reject subprocess use, and the re-check runs before every launch.

# The admin page

A local control panel for the orchestrator. You write a requirement, choose **mock or real agents**, press **Generate**,
watch the run, and make every human decision (approvals, answers to questions, stop) on the page.

```
python -m orchestrator.admin              # then open http://127.0.0.1:8765/
python -m orchestrator.admin --port 9000 --run-root runs --open
```
It is a thin layer: the page calls the same `run_workflow`, engine, policies and audit log as the command line. It adds
no pipeline logic of its own, so everything you see here is also true of `python -m orchestrator.cli ...`.

## What you can do
| Area | What it does |
|---|---|
| **New run** | Pick a scenario, write or load a requirement, choose **Mock** or **Real model**, optionally auto-approve design and release (demo), then Generate |
| **Waiting for you** | Approvals show *what is being approved* (the design text, the release checklist). Questions show options, risk and why it matters. High-risk questions cannot be skipped or assumed |
| **Current run** | Steps with their state and attempts, a plain-language activity feed (retries, policy blocks, fallbacks, rollbacks), numbers (model calls, tokens, retries, policy blocks) |
| **Stop** | A safe-stop: nothing new starts, waiting decisions are released, work already in flight finishes |
| **Results** | Open any artifact, read the report, download everything as a zip, verify the audit trail's hash chain |
| **Run the generated service** | For runs that built a service: **Start this service** launches it on a free port, shows a link, and **Stop service** ends it. Described below |
| **Size limits** | The most lines the code and web UI stages may produce (defaults 900 and 700 for the shortener). The model is told them; raising one is recorded in the audit log, and a run stopped by a limit says how to continue |
| **Change an answer** | After the ambiguous scenario finishes, pick a decision and a new answer: only the dependent work is redone (automatic re-plan), or the change is refused/rolled back by the same rules as on the command line |
| **Earlier runs** | Everything in the runs folder, including runs made from the command line |

Scenarios: the URL shortener (greenfield, with web UI and plan), the ambiguous "make links expire sensibly" request, and a
quick toy function (the cheapest first live test). Brownfield is not offered: it needs a baseline codebase.

## Mock or real
* **Mock** (default): offline and free; the agents return stored artifacts. If you write your own requirement the page warns
  that mock agents **ignore it**: your wording only matters with a real model.
* **Real model**: shows the model name, a hard **call budget**, an API-key area and a **confirmation** you must tick
  (your requirement and the generated code are sent to the Anthropic API and this can cost money, at most N calls). Under
  "Advanced" you can send only some roles to the real model (hybrid) and choose whether prompts and responses are recorded
  with the run (default on for real runs; kept locally, excluded from downloads).

## Running the generated service
After a run that produced a service (the URL shortener, or the "make links expire sensibly" change), the Results area shows
**Run the generated service**. Press **Start this service**, open the link (for example `http://127.0.0.1:53124/`), and press
**Stop service** when you are done. It also stops when the admin page exits. The service's own web page is served at the
same address, and its database and log live in `runs/<run-id>/service/`, not in your project folder.

* The button appears **only for runs that produced a runnable service**. The toy-function run has none.
* **Mock runs** start with one click. **Real-model runs, and runs of unknown origin** (for example made from the command
  line) need you to tick a confirmation first: that code was not produced by the reviewed mock agents and will run with
  your normal permissions. The server enforces this, not just the page.
* **Enforced, not just requested:** the service can only listen on this computer (every `bind` is forced onto 127.0.0.1
  inside the child process, even if the code asks for every interface); it never receives your API key or any variable
  whose name looks like a credential; the security policies are re-run on the file immediately before it starts; only one
  service runs at a time.
* **Not enforced:** it is not a sandbox. The service can make outbound connections and touch files with your permissions,
  which is why unreviewed code needs the confirmation and why this stays a local tool.

## The API key
* Preferred: set `ANTHROPIC_API_KEY` in the terminal that starts the page; the page shows "found in the environment".
* Or paste a key into the page. It is held **only in this program's memory**: never written to disk, logs, recordings,
  run files or the browser (no cookies, no local storage), never sent back to the page, and cleared from the box as soon
  as it is accepted. "Clear key" removes it; closing the program forgets it. A pasted key takes precedence over the environment.
* Use a dedicated key with a spending limit, and revoke it when you are done.
* I cannot obtain a key for you: create one in the Anthropic console (API access is billed separately from a chat subscription).

## Security model (it is a local tool, and it is built like one)
| Threat | Protection |
|---|---|
| Another machine reaching it | Binds to `127.0.0.1` only |
| A malicious website talking to it (DNS rebinding) | `Host` must be `127.0.0.1:<port>` or `localhost:<port>` |
| A malicious website making your browser start a run (CSRF) | Every POST/DELETE needs a per-start secret token in `X-Admin-Token`, and an `Origin` header, if sent, must be this page |
| Script injection into the page | Strict hash-based Content-Security-Policy (no `unsafe-inline`); the page renders all data with `textContent`, never `innerHTML`; it passes the same UI security policies the pipeline enforces on generated UIs |
| Path tricks to read other files | Artifact names must match an existing file in the run's folder; run ids are validated |
| Oversized requests | 64 KB request limit; requirement limited to 20,000 characters |
| A generated service listening beyond this computer | The service is started through a wrapper that forces every `bind` onto loopback; a test inspects the real listening socket |
| A generated service reading your credentials | The child's environment is scrubbed of anything named like a key, token, secret or password |
| Running a file that changed after the checks | The security policies are re-run on the file immediately before each launch |
| Prompts leaking out | Downloads contain artifacts, report, metrics and audit trail, but never recorded prompts |

**What it is not:** a multi-user or internet-facing service. Generated code is executed by the test sandbox (an isolated
subprocess with a timeout, not a hard security boundary), which is another reason to keep this on your own machine.
There is no login, quota or per-user separation.

## Limits to state honestly
* One active run and one running service at a time. State of a running run is in memory: closing the program loses it (finished runs stay on disk).
* The page polls (about every 0.7 s) instead of pushing events.
* Only some scenarios are offered (see above); custom baselines for brownfield are not supported on the page.
* Like the real-LLM client, the real-model path through the page has been tested against a fake Anthropic server, not the live API.

## Clearing leftover servers (Windows)
Versions before 0.5.2 could leave a generated service running after Stop, and the test suite could leave some behind too. To find
processes listening on this computer's loopback address and end the stray ones:
```
netstat -ano | findstr LISTENING | findstr 127.0.0.1
taskkill /PID <the number in the last column> /F
```
Leave the admin page itself (port 8765) running if you are using it. Restarting the computer also clears them.

## Troubleshooting
| You see | Do this |
|---|---|
| "Real mode needs an API key" | set `ANTHROPIC_API_KEY` and restart the page, or paste a key |
| Generate stays disabled | a run is still active (stop it), or Real mode's confirmation is unticked |
| "unexpected Host header" / "missing or wrong admin token" | open the page at `http://127.0.0.1:<port>/` or `http://localhost:<port>/` and reload it |
| A run waits forever | it is waiting for you: look at "Waiting for you", or press Stop |
| A run halts with `[change_control/max-lines-changed]` | a stage produced more lines than its size limit. Raise it under "Size limits" and generate again, or ask for something smaller |
| "the security policies now reject this file" | the implementation was edited after the run and now breaks a rule: regenerate it, or fix the named problem |
| The service's port is still busy after Stop (Windows) | an older version left an orphaned process behind: see "Clearing leftover servers" below, then update |
| "The service stopped on its own" | the generated program crashed; see `runs/<run-id>/service/service.log` |
| Ctrl+C does not exit at once | it is releasing a waiting decision and stopping the run; it exits in a moment |

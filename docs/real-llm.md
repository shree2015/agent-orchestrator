# Running with a real LLM, and writing your own requirements

By default every agent is an **offline mock** that returns stored, reviewed artifacts. That is what the tests and
demos use. This guide shows how to put a real model behind the agents and how to give the pipeline requirements in
your own words. The two go together: **a mock ignores the requirement text** (it says so when you give it one), so
your wording only matters once a real model is behind the agents.

> **What has and has not been tested.** The real client, the output contracts, the retry-with-feedback loop and all
> the safety limits are tested against a *fake Anthropic server* that uses the same request and response format.
> They have **not** been run against the live API from the environment this was built in. Expect to tune prompts
> after your first live runs; the recording option below is how you capture what happened.

> **Prefer buttons?** The same mock/real switch, key handling and requirement box are on the admin page:
> `python -m orchestrator.admin` (see `docs/admin-page.md`).

## 1. One-time setup
Get an API key from the Anthropic console, then set it **in the terminal, never in a file you commit**:
```
Command Prompt:   set ANTHROPIC_API_KEY=sk-ant-...
PowerShell:       $env:ANTHROPIC_API_KEY="sk-ant-..."
Linux / macOS:    export ANTHROPIC_API_KEY=sk-ant-...
```
The key lives only in that terminal session and inside the client object. It is never written to the audit log,
reports, recordings or error messages (tests check this). No new packages are needed: the client uses the standard library.

The default model is `claude-sonnet-5-5`. Change it with `--model NAME` or the `ANTHROPIC_MODEL` environment variable.
If your account does not have a model, the first call fails fast with "set --model". Model names and the API itself
are documented at https://docs.claude.com/en/api/overview.

## 2. A safe way to start: preview, then the smallest live test, then more
**a) Free preview (no API call).** Run on the mock but record every prompt the pipeline builds:
```
python -m orchestrator.cli run shortener --approve auto --record llm-records/preview
```
Open `llm-records/preview/*.json`: each file has the `system` prompt (the output contract for that role), the
`prompt` (including the original request and the upstream artifacts) and the mock's `response`. A clean shortener run
makes **8 calls**; the prompts total roughly 117,000 characters (on the order of 30,000 input tokens) and the stored
answers about 57,000 characters (roughly 14,000 output tokens). Retries add calls. Use this to see what you would pay for.

**b) Smallest live test: the toy workflow, every role real.**
```
python -m orchestrator.cli run slugify --llm anthropic --approve auto --record llm-records/slugify-1
```
About 6 calls. It proves your key, the model name and the loop work. Open the run's `report.md`: the metrics table shows
`llm_calls` and the input/output token counts, and `governance timeline` shows every retry and why.

**c) Hybrid: real model for some roles only.** The others stay on the mock:
```
python -m orchestrator.cli run shortener --llm anthropic --llm-roles architect,doc_writer,reviewer --approve auto
```
Choose roles that do not make promises other artifacts must keep. For example do not make `requirements` real while
the planner is a mock, because the stored plan cannot trace the acceptance criteria your model invents.

**d) Everything real, your own requirement (the shortener).** See section 3.

## 3. Writing the requirements in English
The pipeline's input is plain English. Edit `examples/requirements/url-shortener.md` (or write your own file) and run:
```
python -m orchestrator.cli run shortener --llm anthropic --requirement-file examples/requirements/url-shortener.md --approve auto --record llm-records/shortener-1
```
or for a one-liner: `--requirement "Build a ..."`. What happens to your text:
1. The **requirements** agent turns it into structured requirements (goal, functional, constraints, ambiguities,
   acceptance criteria).
2. The **original text** is also given to the architect, developer and front-end agents, so exact names, routes and
   numbers survive the summarising step.
3. The **planner** decomposes the work and traces every acceptance criterion to tests the **tester** must write.
4. Every stage's output must pass its gates (security, compliance, change control, tests run in the sandbox).

Keep the **API routes** and **technical constraints** in the template unless you also expect to change what depends on
them: the generated web page calls those routes, and the checks (for example "the UI uses the documented API") look
for them. If you make the requirement much bigger, the change-control size limit (600 lines for the service, 500 for
the UI) will say so; raise it in `orchestrator/workflows/shortener.py` deliberately, or ask for a smaller solution.

## 4. How the loop works with a real model
* Each role has an **output contract** (`orchestrator/llm/contracts.py`) that states what its gates enforce: for
  example "every public function has a docstring", "tests must not import the module", "no innerHTML".
* A real model often wraps answers in markdown fences or adds a sentence of preamble. These are removed; clean
  artifacts are never altered.
* When a gate rejects an attempt, the **retry prompt contains the exact reason and the model's previous output**
  (and a fallback agent also learns why the primary failed). A mock ignores this; a real model uses it to fix the problem.
* A cut-off answer (output limit hit) is **rejected**, never passed to the gates as broken code. Raise `--max-tokens`.

## 5. Cost and safety limits
| Control | Default | Flag |
|---|---|---|
| Hard budget on model calls for the whole run | 40 | `--max-llm-calls N` |
| Output limit per call | 16000 tokens | `--max-tokens N` |
| Wait per call | 300 s | `--llm-timeout S` |
| Rate limits (429) and server errors (5xx, 529) | retried with backoff, honouring Retry-After | |
| Wrong key, no access, unknown model | **fatal, fail fast**, no further calls | |
| Engine-level limits | per-node retries, global attempt budget, safe-stop | |

Your requirement text, the upstream artifacts and the generated code are sent to the API. Do not put confidential
material in a requirement file (for example, do not paste the assignment PDF, which is marked internal).

## 6. When a stage keeps failing
| You see | Likely cause | What to do |
|---|---|---|
| `check ANTHROPIC_API_KEY` | key missing or wrong in this terminal | set it again, open a new terminal after `setx` |
| `set --model` | the model name is not available to your account | `--model <a model you have>` |
| `cut off at max_tokens` | the artifact is longer than the limit | raise `--max-tokens` or ask for a smaller solution |
| `ModuleNotFoundError` in the tests stage | the model imported the module in its tests | the contract forbids it; the retry feedback usually fixes it |
| `[change_control/max-lines-changed]` | the service is bigger than the budget | ask for a smaller one, or raise the limit deliberately |
| `[compliance/traceability-evidence-exists]` | the tester skipped a test the plan promised | normally fixed by the retry |
| `[security/...]` | the model used a forbidden call, import or pattern | the message names the rule; the retry fixes it |
| `LLM call budget exhausted` | the run needed more than `--max-llm-calls` | raise it if intended |

If a stage keeps failing across retries, run again with `--record llm-records/<name>` and keep the `report.md` and the
recorded files: they show exactly what the model was asked and what it said, which is what is needed to tune a contract.
Real models are not deterministic, so two runs can differ.

## 7. Limits to state honestly
* Not yet exercised against the live API in the build environment (see the note at the top).
* Quality depends on the model; the gates guarantee **conformance** (it parses, is safe, documented, tested), not that
  the design is the best one.
* The mock remains the default so tests and demos stay offline, free and deterministic.

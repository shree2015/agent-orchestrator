# ADR 006: Real LLM integration behind the existing interface, with feedback and hard limits

**Status:** accepted

## Context
Every agent was a mock. The assignment is about agents executing under controlled autonomy, so the design must hold up
with a real, non-deterministic model: it must be able to read why it was rejected, cost must be bounded, and nothing
secret may leak into the audit trail.

## Decision
1. **Same interface** (`complete(role, prompt) -> str`). A real client is a drop-in; no workflow changes.
2. **Standard-library client** for the Messages API: no SDK dependency, an injectable transport for offline tests.
3. **Gate feedback on retry.** The engine gives the agent the rejection reason and its previous output (and gives a
   fallback agent the primary's). Without this a real model repeats the same mistake. Gates still judge the real context.
4. **Output contracts per role** state what the gates enforce; a cleaner removes chat packaging but is the identity on
   well-formed artifacts (tested against every stored reference artifact).
5. **Hard limits**: call budget, per-call output limit, retries with backoff for transient errors, fail-fast on
   authentication/model errors, truncated answers rejected.
6. **Observability without leakage**: every call is an audit event (role, model, tokens, latency, prompt hash, request
   id). Prompts are only saved when the user asks (`--record`). The key is never logged, recorded or put in errors.
7. **Hybrid routing** lets a user move one role at a time from mock to real.

## Alternatives considered
* *Official SDK*: nicer ergonomics, but another dependency for a prototype that otherwise needs none, and harder to fake.
* *Re-prompt without feedback*: simplest, but ineffective with a real model.
* *Free-form prompts*: cheaper to write, but the gates would reject far more output and the failures would be opaque.

## Consequences
* (+) The same governance (gates, policies, rollback, audit) applies unchanged to real output.
* (+) Cost is bounded and visible; failures are diagnosable from recordings.
* (-) Prompts and contracts are untested against a live model in the build environment and will need tuning.
* (-) Non-determinism: two real runs can differ; the deterministic mock remains the default for tests.
* (-) Sending requirement text and code to an external API is a data-handling decision for the user.

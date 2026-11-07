"""Generic LLM-backed agent: role + instruction + declared inputs.

Every output records `derived_from`, which is the seed of decision lineage. On a retry the engine supplies the
reason the previous attempt was rejected (and that attempt's output) under the reserved context key
"__feedback__"; the prompt then asks the model to fix exactly those problems. Mock LLMs simply ignore it.
"""
from __future__ import annotations

from ..llm.contracts import clean_artifact

MAX_PREVIOUS_CHARS = 30_000


def llm_agent(llm, role: str, instruction: str, inputs: tuple[str, ...] = (), seed: str = "", retry_note: str = ""):
    def run(ctx):
        parts = [seed] if seed else []
        parts += [f"### {k}\n{ctx[k]['content']}" for k in inputs if k in ctx]
        feedback = ctx.get("__feedback__")
        if feedback and retry_note:             # e.g. a reviewer: its verdict is not a malformed artifact to "fix", it is to be re-checked
            parts.append("### Re-check your previous reply\n" + retry_note)
            previous = str(feedback.get("previous") or "")
            if previous:
                parts.append("### Your previous reply (repeated for you to re-check)\n" + previous[:MAX_PREVIOUS_CHARS])
        elif feedback:
            parts.append("### Your previous attempt was REJECTED by automated checks\n" + str(feedback["content"]))
            previous = str(feedback.get("previous") or "")
            if previous:
                parts.append("### Your previous output (fix the problems above, then return the complete corrected artifact)\n"
                             + previous[:MAX_PREVIOUS_CHARS])
        prompt = f"{instruction}\n\n" + "\n\n".join(parts)
        return {"role": role, "content": clean_artifact(role, llm.complete(role, prompt)), "derived_from": list(inputs)}
    return run


REVIEW_RETRY = ("Your previous reply is repeated below. Re-check EACH reason against the original request and the definition of a blocking "
                "defect. Drop every reason that is not blocking. If no blocking defect remains, reply APPROVE; if one does, reply REJECT and keep "
                "only the blocking reasons.")


def review_instruction(ui: bool = False, existing_code: bool = False) -> str:
    """What the final reviewer is told: the request is the standard, what has ALREADY been verified, and what counts as blocking."""
    verified = ("the code parses and satisfies the security, compliance and change-control policies; the tests run and pass against the code in a "
                "sandbox" + (" (including every existing test)" if existing_code else "") + "; the documentation covers every public name"
                + ("; the web UI satisfies the security and accessibility policies" if ui else ""))
    return ("You are the final reviewer. Judge the artifacts below against the ORIGINAL REQUEST. All automated checks have ALREADY PASSED for them: "
            + verified + ". REJECT only for a BLOCKING defect: behaviour that contradicts the request, a security flaw, tests that do not exercise "
            "what the request asks for, or documentation that is wrong. Style, naming, extra features, hypothetical edge cases and optional "
            "improvements are NOT blocking: after APPROVE you may list at most three as 'Suggestions'. HOW TESTS WORK HERE: a separate tester writes the "
            "tests, and they are executed in the SAME namespace as the implementation (its names are already in scope), so they intentionally do "
            "not import it: never report a missing import of the code under test. If the implementation module also contains its own tests, "
            "that is not blocking. Reply format: the first line is exactly "
            "APPROVE or REJECT; then at most five short bullet points, each saying what you checked (APPROVE) or naming the file and what is wrong (REJECT).")


def review_agent(llm, original: str, ui: bool = False, existing_code: bool = False, extra_inputs: tuple[str, ...] = ()):
    inputs = ("requirements", "implementation", "tests", "docs") + (("ui",) if ui else ()) + extra_inputs
    return llm_agent(llm, "reviewer", review_instruction(ui, existing_code), inputs, seed=original, retry_note=REVIEW_RETRY)

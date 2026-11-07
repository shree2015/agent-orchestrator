"""Generic LLM-backed agent: role + instruction + declared inputs.

Every output records `derived_from`, which is the seed of decision lineage. On a retry the engine supplies the
reason the previous attempt was rejected (and that attempt's output) under the reserved context key
"__feedback__"; the prompt then asks the model to fix exactly those problems. Mock LLMs simply ignore it.
"""
from __future__ import annotations

from ..llm.contracts import clean_artifact

MAX_PREVIOUS_CHARS = 30_000


def llm_agent(llm, role: str, instruction: str, inputs: tuple[str, ...] = (), seed: str = ""):
    def run(ctx):
        parts = [seed] if seed else []
        parts += [f"### {k}\n{ctx[k]['content']}" for k in inputs]
        feedback = ctx.get("__feedback__")
        if feedback:
            parts.append("### Your previous attempt was REJECTED by automated checks\n" + str(feedback["content"]))
            previous = str(feedback.get("previous") or "")
            if previous:
                parts.append("### Your previous output (fix the problems above, then return the complete corrected artifact)\n"
                             + previous[:MAX_PREVIOUS_CHARS])
        prompt = f"{instruction}\n\n" + "\n\n".join(parts)
        return {"role": role, "content": clean_artifact(role, llm.complete(role, prompt)), "derived_from": list(inputs)}
    return run

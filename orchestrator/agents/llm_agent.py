"""Generic LLM-backed agent: role + instruction + declared inputs.

Every output records `derived_from`, which is the seed of decision lineage.
"""
from __future__ import annotations


def llm_agent(llm, role: str, instruction: str, inputs: tuple[str, ...] = (), seed: str = ""):
    def run(ctx):
        parts = [seed] if seed else []
        parts += [f"### {k}\n{ctx[k]['content']}" for k in inputs]
        prompt = f"{instruction}\n\n" + "\n\n".join(parts)
        return {"role": role, "content": llm.complete(role, prompt), "derived_from": list(inputs)}
    return run

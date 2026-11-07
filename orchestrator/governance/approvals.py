"""Human approval checkpoints. Engine denies by default if none is supplied."""
from __future__ import annotations

import sys


def cli_approver(node, output) -> bool:
    preview = str(output.get("content", output))[:600]
    print(f"\n=== APPROVAL REQUIRED: {node.id} ({node.description}) ===\n{preview}\n")
    if not sys.stdin.isatty():
        print("(no interactive terminal -> denying)")
        return False
    return input("Approve? [y/N] ").strip().lower() in {"y", "yes"}


def auto_approver(node, output) -> bool:
    return True


def deny_approver(*rejected_nodes):
    """Approve everything except the named nodes (for demos/tests)."""
    return lambda node, output: node.id not in rejected_nodes


# ---- clarifying questions: "askers" take a question dict and return the human's answer, or None ----
def no_asker(item):
    """Nobody is available to answer: every question stays unanswered (high-risk ones will block the run)."""
    return None


def dict_asker(answers: dict):
    """Pre-supplied answers, e.g. from --answer id=value (matched by question id)."""
    return lambda item: answers.get(item["id"])


def cli_asker(item):
    """Ask a person in the terminal. Empty input means 'no answer' (the proposed default may then be assumed
    for low/medium-risk questions; high-risk ones are asked up to three times)."""
    if not sys.stdin.isatty():
        return None
    print(f"\n=== QUESTION [{item.get('risk', '?').upper()} RISK]: {item['question']}")
    print(f"    options: {item.get('options')}   proposed: {item.get('proposed')}   why: {item.get('why', '')}")
    for _ in range(3 if item.get("risk") == "high" else 1):
        answer = input("    your answer (Enter = no answer): ").strip()
        if answer:
            return answer
    return None


def layered_asker(*askers):
    """First asker that has an answer wins (e.g. --answer values first, then ask the terminal)."""
    def ask(item):
        for a in askers:
            answer = a(item)
            if answer is not None:
                return answer
        return None
    return ask

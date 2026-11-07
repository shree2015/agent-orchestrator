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

"""Task and run state definitions."""
from enum import Enum


class NodeState(str, Enum):
    PENDING = "PENDING"                      # waiting on dependencies
    RUNNING = "RUNNING"
    AWAITING_APPROVAL = "AWAITING_APPROVAL"  # human checkpoint
    DONE = "DONE"
    FAILED = "FAILED"                        # retries exhausted or gate failed
    REJECTED = "REJECTED"                    # human rejected the output
    SKIPPED = "SKIPPED"                      # upstream failed; never ran
    STALE = "STALE"                          # upstream changed; must re-run
    ROLLED_BACK = "ROLLED_BACK"              # a failed change was reverted to the last good output


class RunStatus(str, Enum):
    COMPLETED = "COMPLETED"
    HALTED = "HALTED"        # safe-stop: failure, rejection or budget exceeded
    DEADLOCK = "DEADLOCK"    # nothing runnable but work remains (should not happen)

"""Append-only, hash-chained JSONL audit log (tamper-evident)."""
from __future__ import annotations

import hashlib
import json
import threading
from pathlib import Path

GENESIS = "0" * 64


def _digest(rec: dict) -> str:
    return hashlib.sha256(json.dumps(rec, sort_keys=True, default=str).encode()).hexdigest()


class AuditLog:
    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text("", encoding="utf-8")
        self._prev, self._seq = GENESIS, 0
        self._lock = threading.Lock()
        self.events: list[dict] = []          # in-memory copy for metrics

    def __call__(self, event: dict) -> None:   # usable as the engine's audit sink
        with self._lock:
            rec = {**event, "seq": self._seq, "prev_hash": self._prev}
            rec["hash"] = _digest(rec)
            with self.path.open("a", encoding="utf-8") as f:
                f.write(json.dumps(rec, default=str) + "\n")
            self._prev, self._seq = rec["hash"], self._seq + 1
            self.events.append(rec)

    @staticmethod
    def verify(path: str | Path) -> bool:
        prev = GENESIS
        for line in Path(path).read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            claimed = rec.pop("hash")
            if rec["prev_hash"] != prev or _digest(rec) != claimed:
                return False
            prev = claimed
        return True

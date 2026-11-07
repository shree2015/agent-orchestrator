"""Run generated code + tests in an isolated subprocess with a timeout.

NOTE: this is process isolation, NOT a security sandbox. Production would use
containers / gVisor / seccomp. Documented as a known limitation.
"""
from __future__ import annotations

import json
import subprocess
import sys

_RUNNER = r'''
import json, sys, traceback
d = json.load(sys.stdin); ns = {}
exec(d["code"], ns); exec(d["tests"], ns)
res = {"passed": [], "failed": {}}
for name, fn in list(ns.items()):
    if name.startswith("test_") and callable(fn):
        try: fn(); res["passed"].append(name)
        except Exception: res["failed"][name] = traceback.format_exc(limit=2)
print(json.dumps(res))
'''


def run_tests(code: str, tests: str, timeout: int = 10) -> dict:
    try:
        p = subprocess.run([sys.executable, "-I", "-c", _RUNNER], input=json.dumps(
            {"code": code, "tests": tests}), capture_output=True, text=True, timeout=timeout)
    except subprocess.TimeoutExpired:
        return {"passed": [], "failed": {"<runner>": "timeout"}}
    if p.returncode != 0:
        return {"passed": [], "failed": {"<runner>": p.stderr[-400:]}}
    return json.loads(p.stdout)

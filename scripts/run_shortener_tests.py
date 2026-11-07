"""Run the shortener's own test suite through the orchestrator's sandbox and list each test.

    python scripts/run_shortener_tests.py

Works from any directory: it finds the project root from its own location.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.governance import sandbox  # noqa: E402

REF = ROOT / "orchestrator" / "workflows" / "reference" / "shortener"
code = (REF / "shortener.py.txt").read_text(encoding="utf-8")
tests = (REF / "tests.py.txt").read_text(encoding="utf-8")
result = sandbox.run_tests(code, tests, timeout=60)
print(f"{len(result['passed'])} passed; {len(result['failed'])} failed")
for name in sorted(result["passed"]):
    print("  PASS", name)
for name, trace in result["failed"].items():
    print("  FAIL", name, "\n", trace)
sys.exit(1 if result["failed"] else 0)

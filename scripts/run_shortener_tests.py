"""Run the shortener's own test suite through the orchestrator's sandbox and list each test.

    python scripts/run_shortener_tests.py            # greenfield service (default)
    python scripts/run_shortener_tests.py fixed      # brownfield: the BUG-142 fix and its regression tests
    python scripts/run_shortener_tests.py baseline   # brownfield: the buggy legacy code with its legacy tests

Works from any directory: it finds the project root from its own location.
"""
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from orchestrator.governance import sandbox  # noqa: E402

REF = ROOT / "orchestrator" / "workflows" / "reference"
SETS = {
    "greenfield": (REF / "shortener" / "shortener.py.txt", REF / "shortener" / "tests.py.txt"),
    "fixed": (REF / "brownfield" / "fixed_shortener.py.txt", REF / "brownfield" / "fixed_tests.py.txt"),
    "baseline": (REF / "brownfield" / "baseline_shortener.py.txt", REF / "brownfield" / "baseline_tests.py.txt"),
}
which = sys.argv[1] if len(sys.argv) > 1 else "greenfield"
if which not in SETS:
    sys.exit(f"unknown set '{which}'; choose from: {', '.join(SETS)}")
code_file, tests_file = SETS[which]
result = sandbox.run_tests(code_file.read_text(encoding="utf-8"), tests_file.read_text(encoding="utf-8"), timeout=60)
print(f"[{which}] {len(result['passed'])} passed; {len(result['failed'])} failed")
for name in sorted(result["passed"]):
    print("  PASS", name)
for name, trace in result["failed"].items():
    print("  FAIL", name, "\n", trace)
sys.exit(1 if result["failed"] else 0)

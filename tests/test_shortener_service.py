"""The shortener's own test suite, exposed as individual pytest items.

The 27 tests live in `orchestrator/workflows/reference/shortener/tests.py.txt` in the format the
orchestrator's sandbox gate runs: plain functions executed in the same namespace as the code under test.
This module loads exactly those files (one source of truth) and registers every `test_*` function as
its own pytest test, so `pytest -v` lists them by name.

Note: pytest's assertion rewriting does not apply to code executed this way, so a failure shows a plain
AssertionError with the source line rather than pytest's introspected values.
"""
import linecache

from orchestrator.governance import sandbox
from orchestrator.workflows import shortener

CODE_FILE, TESTS_FILE = "shortener.py.txt", "tests.py.txt"


def _load_namespace() -> dict:
    ns: dict = {}
    for filename in (CODE_FILE, TESTS_FILE):
        source = shortener.read(filename)
        # register the source so tracebacks can show the offending line
        linecache.cache[filename] = (len(source), None, source.splitlines(True), filename)
        exec(compile(source, filename, "exec"), ns)
    return ns


_NS = _load_namespace()


def _register(name: str, fn) -> None:
    def runner():
        fn()

    runner.__name__ = runner.__qualname__ = name
    runner.__doc__ = fn.__doc__
    globals()[name] = runner


_NAMES = sorted(n for n, v in _NS.items() if n.startswith("test_") and callable(v))
for _name in _NAMES:
    _register(_name, _NS[_name])


def test_every_artifact_test_is_registered_and_agrees_with_the_sandbox_gate():
    """Guards against silently losing tests: pytest sees the same set the orchestrator's gate runs."""
    result = sandbox.run_tests(shortener.read(CODE_FILE), shortener.read(TESTS_FILE), timeout=60)
    assert not result["failed"], result["failed"]
    assert sorted(result["passed"]) == _NAMES and len(_NAMES) >= 20

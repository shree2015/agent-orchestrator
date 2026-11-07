"""AST helpers behind the brownfield gates."""
from orchestrator.governance import codeintel as ci

BASE = '''import os
LIMIT = 3


class Repo:
    """doc"""
    SCHEMA = "x"

    def __init__(self, path=":memory:"):
        self.path = path

    def get(self, code):
        return code

    def _private(self):
        pass


def helper(a, b=1):
    return a + b
'''


def test_symbols_cover_functions_methods_class_bodies_and_module_level_code():
    assert set(ci.symbols(BASE)) == {"<module>", "Repo.<class>", "Repo.__init__", "Repo.get", "Repo._private", "helper"}


def test_formatting_and_line_moves_are_not_changes():
    reformatted = BASE.replace("return a + b", "return (a +\n            b)").replace("\n\n\ndef helper", "\n\n\n\n\ndef helper")
    assert ci.changed_symbols(BASE, reformatted) == {}


def test_changed_symbols_reports_added_removed_and_modified():
    new = BASE.replace("return code", "return code.strip()").replace("    def _private(self):\n        pass\n", "") + "\ndef extra():\n    pass\n"
    assert ci.changed_symbols(BASE, new) == {"Repo.get": "modified", "Repo._private": "removed", "extra": "added"}


def test_module_level_and_class_level_changes_are_tracked():
    assert ci.changed_symbols(BASE, BASE.replace("LIMIT = 3", "LIMIT = 4")) == {"<module>": "modified"}
    assert ci.changed_symbols(BASE, BASE.replace('SCHEMA = "x"', 'SCHEMA = "y"')) == {"Repo.<class>": "modified"}


def test_test_names_lists_only_top_level_test_functions():
    code = "def test_a():\n    pass\n\ndef helper():\n    pass\n\nclass T:\n    def test_nested(self):\n        pass\n"
    assert ci.test_names(code) == {"test_a"}


def test_public_api_excludes_private_names_but_includes_init():
    assert set(ci.public_api(BASE)) == {"Repo", "Repo.__init__", "Repo.get", "helper"}


def _compat(old_src, new_src, name="helper"):
    return ci.incompatibilities(ci.public_api(old_src)[name], ci.public_api(new_src)[name])


def test_adding_an_optional_parameter_is_compatible():
    assert _compat(BASE, BASE.replace("def helper(a, b=1):", "def helper(a, b=1, c=2):")) == []


def test_breaking_signature_changes_are_detected():
    assert _compat(BASE, BASE.replace("def helper(a, b=1):", "def helper(a, b):"))                  # default dropped
    assert _compat(BASE, BASE.replace("def helper(a, b=1):", "def helper(a, b=1, c):".replace("b=1, c", "b=1, *, c")))  # new required kw-only
    assert _compat(BASE, BASE.replace("def helper(a, b=1):", "def helper(b, a=1):"))               # renamed / reordered
    assert _compat(BASE, BASE.replace("def helper(a, b=1):", "def helper(a):"))                     # parameter removed


def test_keyword_only_and_varargs_rules():
    old = "def f(a, *args, k=1, **kw):\n    pass\n"
    assert ci.incompatibilities(ci.public_api(old)["f"], ci.public_api("def f(a, *args, **kw):\n    pass\n")["f"])
    assert ci.incompatibilities(ci.public_api(old)["f"], ci.public_api("def f(a, k=1, **kw):\n    pass\n")["f"])
    assert ci.incompatibilities(ci.public_api(old)["f"], ci.public_api("def f(a, *args, k=1):\n    pass\n")["f"])
    assert ci.incompatibilities(ci.public_api(old)["f"], ci.public_api(old)["f"]) == []


def test_module_constants_evaluates_simple_expressions_without_running_code():
    code = "A = 3\nB = 90 * 24 * 3600\nC = -5\nD = 'x'\nE = True\nF = 7 // 2\nG = open('/etc/passwd')\nH = A + 1\nimport os\nos.getcwd()\n"
    assert ci.module_constants(code) == {"A": 3, "B": 7776000, "C": -5, "D": "x", "E": True, "F": 3}


def test_module_constants_ignores_names_defined_inside_functions_and_classes():
    code = "X = 1\n\ndef f():\n    X = 2\n\nclass C:\n    X = 3\n"
    assert ci.module_constants(code) == {"X": 1}

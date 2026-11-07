"""Web UI: policies, pipeline scenarios, and real behaviour of the page script against a real server."""
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

import pytest

from orchestrator import cli
from orchestrator.admin.service import spawn, terminate_tree
from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals
from orchestrator.governance.policies import change_control as cc, compliance as comp, security as sec
from orchestrator.runner import run_workflow
from orchestrator.workflows import shortener

HARNESS = Path(__file__).resolve().parent / "ui_harness.js"
UI = shortener.GOOD_UI


def ids(violations):
    return {f"{v.category}/{v.policy}" for v in violations}


def go(tmp_path, llm=None, **kw):
    graph, agents = shortener.build(llm or shortener.make_llm())
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="ui", **kw)


# ---- UI security policies ----------------------------------------------------
def test_reference_ui_passes_every_ui_policy():
    assert sec.ui_set().evaluate(UI) == [] and comp.ui_set().evaluate({"ui": UI}) == []


def test_external_scripts_stylesheets_and_images_are_blocked():
    for snippet in ['<script src="https://cdn.example.com/x.js"></script>', '<link rel="stylesheet" href="//fonts.example.com/a.css">',
                    '<img src="http://tracker.example.com/p.gif">', "<style>@import url(https://x.example/a.css);</style>"]:
        assert "security/ui-no-external-resources" in ids(sec.ui_set().evaluate(UI + snippet)), snippet


def test_unsafe_dom_sinks_are_blocked_but_textcontent_is_fine():
    for sink in ["el.innerHTML = x;", "el.outerHTML = x;", "el.insertAdjacentHTML('beforeend', x);", "document.write(x);", "eval(x);", "new Function(x);"]:
        assert "security/ui-no-unsafe-dom-sinks" in ids(sec.ui_set().evaluate(f"<script>{sink}</script>")), sink
    assert sec.ui_set().evaluate("<script>el.textContent = x;</script>") == []


def test_inline_handlers_and_style_attributes_are_blocked():
    assert "security/ui-no-inline-event-handlers" in ids(sec.ui_set().evaluate('<button onclick="go()">x</button>'))
    assert "security/ui-no-inline-style-attributes" in ids(sec.ui_set().evaluate('<div style="color:red">x</div>'))


def test_script_and_style_blocks_must_be_bare_so_the_csp_can_hash_them():
    assert "security/ui-csp-compatible-blocks" in ids(sec.ui_set().evaluate('<script type="module">1</script>'))
    assert "security/ui-csp-compatible-blocks" in ids(sec.ui_set().evaluate('<style media="print">a{}</style>'))
    assert sec.ui_set().evaluate("<script>1</script><style>a{}</style>") == []


# ---- UI compliance policies --------------------------------------------------
def test_ui_must_call_the_documented_api_and_fails_closed_when_missing():
    assert ids(comp.ui_set().evaluate({"ui": "<html lang='en'><title>t</title></html>"})) == {"compliance/ui-uses-documented-api"}
    assert comp.ui_set().evaluate({})          # no ui artifact at all: violation, never a pass


def test_ui_accessibility_basics():
    no_lang = UI.replace('<html lang="en">', "<html>")
    assert any("lang" in v.message for v in comp.ui_set().evaluate({"ui": no_lang}))
    no_title = UI.replace("<title>Shortener - URL shortener</title>", "<title></title>")
    assert any("title" in v.message for v in comp.ui_set().evaluate({"ui": no_title}))
    unlabelled = shortener.FIXTURES["ui_unlabelled"]
    assert any("alias" in v.message for v in comp.ui_set().evaluate({"ui": unlabelled}))


# ---- pipeline scenarios ------------------------------------------------------
def test_ui_is_a_parallel_branch_and_the_workflow_still_passes_lint():
    graph, _ = shortener.build(shortener.make_llm())
    assert cc.lint_graph(graph) == []
    assert graph.nodes["ui"].depends_on == ("implementation",)
    assert set(graph.nodes["review"].depends_on) == {"tests", "docs", "ui"}


def test_run_produces_the_ui_artifact_identical_to_the_reference(tmp_path):
    r = go(tmp_path)
    assert r["status"] is RunStatus.COMPLETED
    assert (r["run_dir"] / "artifacts" / "ui.html").read_text(encoding="utf-8") == UI


@pytest.mark.parametrize("fixture,policy", [
    ("ui_innerhtml", "security/ui-no-unsafe-dom-sinks"),
    ("ui_cdn", "security/ui-no-external-resources"),
    ("ui_unlabelled", "compliance/ui-accessibility-basics"),
])
def test_bad_ui_is_blocked_by_the_named_policy_then_a_clean_retry_succeeds(tmp_path, fixture, policy):
    llm = shortener.make_llm(scripted={"frontend": [shortener.FIXTURES[fixture], UI]})
    r = go(tmp_path, llm)
    assert r["status"] is RunStatus.COMPLETED and r["metrics"]["policy_violations"][policy] == 1


def test_a_ui_that_never_passes_halts_the_run_and_blocks_release(tmp_path):
    r = go(tmp_path, shortener.make_llm(scripted={"frontend": [shortener.FIXTURES["ui_innerhtml"]]}))
    assert r["status"] is RunStatus.HALTED
    assert r["engine"].state["release"] is NodeState.SKIPPED and "ui" not in r["engine"].context


def test_release_blocks_when_the_ui_evidence_is_missing():
    from orchestrator.governance import gates as g
    gate = g.release_compliance_for(comp.RELEASE_EVIDENCE + ("ui",))
    ctx = {k: {"content": "x"} for k in comp.RELEASE_EVIDENCE}
    ctx["review"] = {"content": "APPROVE: ok"}
    ok, why = gate(ctx)
    assert not ok and "artifact: ui" in why


def test_cli_ui_violations(tmp_path):
    base = ["run", "shortener", "--approve", "auto", "--run-root", str(tmp_path)]
    for name in ("ui-innerhtml", "ui-cdn", "ui-unlabelled"):
        assert cli.main(base + ["--violate", name]) == 0


# ---- the page script, run for real -------------------------------------------
def _node_ok():
    if not shutil.which("node"):
        return False
    return subprocess.run(["node", "-e", "process.exit(typeof fetch === 'function' ? 0 : 1)"]).returncode == 0


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


@pytest.mark.skipif(not _node_ok(), reason="needs Node.js 18+ (for fetch) on PATH")
def test_page_script_works_end_to_end_against_a_real_server(tmp_path):
    r = go(tmp_path)
    art = r["run_dir"] / "artifacts"
    mod = _load(art / "implementation.py", "generated_for_ui_harness")

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    server = make_server("127.0.0.1", 0, lambda e, s: None, handler_class=Quiet)   # placeholder to learn a free port
    port = server.server_port
    server.server_close()
    base = f"http://127.0.0.1:{port}"
    app = mod.make_app(mod.ShortenerService(mod.LinkRepository()), mod.RateLimiter(limit=10), base_url=base,
                       ui_html=(art / "ui.html").read_text(encoding="utf-8"))
    server = make_server("127.0.0.1", port, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run(["node", str(HARNESS), str(art / "ui.html"), base], capture_output=True, text=True,
                              timeout=90, encoding="utf-8")
    finally:
        server.shutdown()
        server.server_close()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("PASS") >= 15 and "FAIL" not in proc.stdout


# ---- run the generated service the way a person would ------------------------
def test_generated_service_serves_its_ui_when_started_as_a_script(tmp_path):
    r = go(tmp_path)
    art = r["run_dir"] / "artifacts"
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    proc = spawn([sys.executable, str(art / "implementation.py"), str(port)], tmp_path, os.environ.copy(), subprocess.DEVNULL)
    try:
        page, headers = None, None
        for _ in range(50):
            try:
                with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=2) as resp:
                    page, headers = resp.read().decode("utf-8"), resp.headers
                break
            except OSError:
                time.sleep(0.1)
        assert page is not None, "server did not start"
        assert "<title>Shortener" in page and "default-src 'none'" in headers["Content-Security-Policy"]
    finally:
        terminate_tree(proc)                      # the whole tree: on Windows a venv's python.exe is a launcher with a child

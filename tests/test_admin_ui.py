"""The admin page: it must obey the same UI policies the pipeline enforces, and work end to end against a real server."""
import json
import os
import shutil
import subprocess
import threading
import urllib.request
from pathlib import Path

import pytest

from orchestrator.admin import server as admin_server
from orchestrator.llm import anthropic_client as ac
from orchestrator.workflows import shortener
from tests.test_real_llm import FakeServer
from orchestrator.admin.security import csp_for
from orchestrator.governance.policies import compliance as comp, security as sec

PAGE = Path(admin_server.PAGE)
HARNESS = Path(__file__).resolve().parent / "admin_harness.js"
SECRET = "sk-ant-HARNESS-SECRET-1234567890abcdef"


def test_the_admin_page_passes_the_same_ui_security_and_accessibility_policies_as_generated_uis():
    html = PAGE.read_text(encoding="utf-8")
    assert sec.ui_set().evaluate(html) == []
    assert comp.ui_accessibility_basics().evaluate({"ui": html}) == []


def test_the_page_never_uses_unsafe_dom_apis_and_loads_nothing_from_outside():
    html = PAGE.read_text(encoding="utf-8")
    for forbidden in ("innerHTML", "outerHTML", "insertAdjacentHTML", "document.write", "eval(", "localStorage", "sessionStorage", "http://", "https://"):
        assert forbidden not in html, forbidden


def test_the_page_never_stores_the_key_anywhere_in_the_browser():
    html = PAGE.read_text(encoding="utf-8")
    assert "localStorage" not in html and "sessionStorage" not in html and "indexedDB" not in html and "cookie" not in html.lower()


def _serve(tmp_path):
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    ac.TRANSPORT_OVERRIDE = FakeServer(defaults=shortener.DEFAULTS)         # a "real" model that returns the stored artifacts
    httpd, app, state = admin_server.build_server(0, tmp_path / "runs")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, app, state, saved


def _stop(httpd, state, saved):
    ac.TRANSPORT_OVERRIDE = None
    state.shutdown()
    httpd.shutdown()
    httpd.server_close()
    if saved is not None:
        os.environ["ANTHROPIC_API_KEY"] = saved


def test_a_real_server_serves_the_page_with_a_strict_csp_and_only_on_loopback(tmp_path):
    httpd, app, state, saved = _serve(tmp_path)
    try:
        port = httpd.server_port
        assert httpd.server_address[0] == "127.0.0.1"                                  # bound to loopback only
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as resp:
            body = resp.read().decode("utf-8")
            csp = resp.headers["Content-Security-Policy"]
            assert resp.status == 200 and "Agent Orchestrator - Admin" in body
            assert resp.headers["X-Content-Type-Options"] == "nosniff" and resp.headers["Cache-Control"] == "no-store"
        assert csp == csp_for(body) and "unsafe-inline" not in csp and "default-src 'none'" in csp and "connect-src 'self'" in csp
        req = urllib.request.Request(f"http://127.0.0.1:{port}/api/config", headers={"Host": "evil.example.com"})
        with pytest.raises(urllib.error.HTTPError):
            urllib.request.urlopen(req, timeout=5)                                     # DNS-rebinding style request refused
    finally:
        _stop(httpd, state, saved)


def _node_ok():
    return bool(shutil.which("node")) and subprocess.run(["node", "-e", "process.exit(typeof fetch === 'function' ? 0 : 1)"]).returncode == 0


@pytest.mark.skipif(not _node_ok(), reason="needs Node.js 18+ (for fetch) on PATH")
def test_the_page_script_works_end_to_end_against_a_real_admin_server(tmp_path):
    httpd, app, state, saved = _serve(tmp_path)
    try:
        proc = subprocess.run(["node", str(HARNESS), str(PAGE), f"http://127.0.0.1:{httpd.server_port}", SECRET],
                              capture_output=True, text=True, timeout=240, encoding="utf-8")
    finally:
        _stop(httpd, state, saved)
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert proc.stdout.count("PASS") >= 48 and "FAIL" not in proc.stdout
    leaked = b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert SECRET.encode() not in leaked                                                # nothing the harness did wrote the key to disk

"""Runs scripts/smoke_test.py against the generated service served over real HTTP."""
import importlib.util
import subprocess
import sys
import threading
from pathlib import Path
from wsgiref.simple_server import WSGIRequestHandler, make_server

from orchestrator.workflows import shortener

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "smoke_test.py"


def _serve(tmp_path):
    path = tmp_path / "generated_shortener.py"
    path.write_text(shortener.GOOD_CODE, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("generated_shortener_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    server = make_server("127.0.0.1", 0, mod.make_app(mod.ShortenerService(mod.LinkRepository())), handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    return server


def test_smoke_script_passes_against_a_live_server_including_rate_limit(tmp_path):
    server = _serve(tmp_path)
    try:
        proc = subprocess.run([sys.executable, str(SCRIPT), f"http://127.0.0.1:{server.server_port}", "--rate-limit"],
                              capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
        server.server_close()
    assert proc.returncode == 0, proc.stdout + proc.stderr
    assert "ALL CHECKS PASSED" in proc.stdout and "FAIL" not in proc.stdout


def test_smoke_script_reports_unreachable_server_with_a_clear_exit_code():
    proc = subprocess.run([sys.executable, str(SCRIPT), "http://127.0.0.1:9"], capture_output=True, text=True, timeout=30)
    assert proc.returncode == 2 and "Is the server running?" in proc.stdout


def test_smoke_script_detects_a_broken_service(tmp_path):
    """The checker must fail when the service misbehaves: here, a build with expiry disabled."""
    broken = shortener.GOOD_CODE.replace('return link["expires_at"] is not None and now >= link["expires_at"]',
                                         "return False")
    assert broken != shortener.GOOD_CODE
    path = tmp_path / "broken_shortener.py"
    path.write_text(broken, encoding="utf-8")
    spec = importlib.util.spec_from_file_location("broken_shortener_smoke", path)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    server = make_server("127.0.0.1", 0, mod.make_app(mod.ShortenerService(mod.LinkRepository())), handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        proc = subprocess.run([sys.executable, str(SCRIPT), f"http://127.0.0.1:{server.server_port}"],
                              capture_output=True, text=True, timeout=120)
    finally:
        server.shutdown()
        server.server_close()
    assert proc.returncode == 1 and "FAIL  it returns 410 Gone after expiry" in proc.stdout

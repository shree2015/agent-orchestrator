"""Starting a generated service from the admin page: what is enforced, not just hoped for."""
import io
import json
import os
import socket
import subprocess
import sys
import time
import zipfile
from pathlib import Path
import urllib.request

from orchestrator.admin import service as svc
from orchestrator.admin.server import make_app
from orchestrator.admin.session import AdminState
from orchestrator.workflows import shortener
from tests.test_admin_api import Admin, KEY, FakeServer, real_run, with_clean_env


def closed(port):
    try:
        socket.create_connection(("127.0.0.1", port), 0.3).close()
        return False
    except OSError:
        return True


def wait_closed(port, timeout=4.0):
    """The operating system may take a moment to release a port after its owner is killed."""
    end = time.time() + timeout
    while time.time() < end:
        if closed(port):
            return True
        time.sleep(0.05)
    return closed(port)


def get(url, timeout=5):
    with urllib.request.urlopen(url, timeout=timeout) as resp:
        return resp.status, resp.read().decode("utf-8")


def post(url, body):
    req = urllib.request.Request(url, json.dumps(body).encode())
    with urllib.request.urlopen(req, timeout=5) as resp:
        return resp.status, json.loads(resp.read())


def shortener_run(a, **over):
    a.run(scenario="shortener", requirement="Build a shortener.", **over)
    return a.finish()["run"]["id"]


def start(a, run_id, **extra):
    return a.call("POST", "/api/service", {"run_id": run_id, **extra})


# a service structured the way a real model structures it: classes and its own function names, not our reference's make_app / serve
MODEL_STYLE = '''import sys
from wsgiref.simple_server import make_server


class Config:
    port = 8000


class App:
    def __call__(self, environ, start_response):
        start_response("200 OK", [("Content-Type", "application/json")])
        return [b'{"status": "ok"}']


def main(argv=None):
    argv = argv if argv is not None else sys.argv
    port = int(argv[1]) if len(argv) > 1 else Config.port
    make_server("127.0.0.1", port, App()).serve_forever()


if __name__ == "__main__":
    main()
'''

# the same, but the port is read as --port (a model may read "python implementation.py 8000" differently)
ARGPARSE_STYLE = '''import argparse
from wsgiref.simple_server import make_server


def app(environ, start_response):
    start_response("200 OK", [("Content-Type", "application/json")])
    return [b'{"status": "ok"}']


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8000)
    make_server("127.0.0.1", parser.parse_args().port, app).serve_forever()
'''


def fake_run(a, name, code, with_meta=None):
    """A run folder made by hand (as if from the command line, or edited afterwards)."""
    d = a.state.run_root / name
    (d / "artifacts").mkdir(parents=True)
    (d / "audit.jsonl").write_text("", encoding="utf-8")
    (d / "artifacts" / "implementation.py").write_text(code, encoding="utf-8")
    if with_meta is not None:
        (d / "run.json").write_text(json.dumps(with_meta), encoding="utf-8")
    return name


# ------------------------------------------------------------------------------- who gets a button
@with_clean_env
def test_only_runs_that_produced_a_runnable_service_are_offered_one(tmp_path):
    a = Admin(tmp_path)
    toy = a.run()
    s = a.finish()
    assert s["run"]["runnable"] is False and a.call("GET", f"/api/runs/{toy}")[2]["runnable"] is False
    real_one = shortener_run(a)
    assert a.snap()["run"]["runnable"] is True
    detail = a.call("GET", f"/api/runs/{real_one}")[2]
    assert detail["runnable"] is True and detail["mode"] == "mock"
    assert svc.is_runnable(MODEL_STYLE) and svc.is_runnable(shortener.GOOD_CODE)
    assert not svc.is_runnable("def make_app(): pass\ndef serve(): pass\nif __name__ == '__main__':\n    serve()\n")     # our names alone prove nothing
    assert not svc.is_runnable("def slugify(text): return text\n")


@with_clean_env
def test_a_run_that_is_still_working_is_not_runnable_and_cannot_be_started(tmp_path):
    a = Admin(tmp_path)
    a.run(scenario="shortener", requirement="x", auto_approve=False)
    s = a.wait(lambda s: s["pending"])
    assert s["run"]["runnable"] is False
    assert start(a, s["run"]["id"])[0] == 409
    a.call("POST", "/api/runs/current/stop", {})
    a.finish()


@with_clean_env
def test_unknown_malformed_and_unrunnable_runs_are_refused_clearly(tmp_path):
    a = Admin(tmp_path)
    toy = a.run()
    a.finish()
    assert start(a, "run-does-not-exist")[0] == 404
    assert start(a, "../etc")[0] in (400, 404)
    assert start(a, None)[0] == 400
    status, _, res = start(a, toy)
    assert status == 400 and "did not produce a runnable service" in res["error"]["message"]
    assert a.call("GET", "/api/service")[2] == {"running": False, "last_exit": None}


# ------------------------------------------------------------------------------ starting and stopping
@with_clean_env
def test_a_mock_service_starts_on_loopback_works_over_http_and_stops_cleanly(tmp_path):
    a = Admin(tmp_path)
    run_id = shortener_run(a)
    stray_db = Path.cwd() / "shortener.db"
    existed = stray_db.exists()
    status, _, st = start(a, run_id)
    assert status == 200 and st["running"] and st["url"] == f"http://127.0.0.1:{st['port']}/" and st["run_id"] == run_id
    assert get(st["url"] + "healthz") == (200, '{"status": "ok"}')
    code, page = get(st["url"])
    assert code == 200 and "<title>Shortener" in page                                  # the UI that ships next to the code
    created = post(st["url"] + "api/shorten", {"url": "https://example.com/from-the-admin-page"})
    assert created[0] == 201 and created[1]["short_url"].startswith(f"http://127.0.0.1:{st['port']}/") or created[0] == 201
    run_dir = tmp_path / "runs" / run_id
    assert (run_dir / "service" / "shortener.db").exists() and (run_dir / "service" / "service.log").exists()
    assert stray_db.exists() == existed                                                # nothing leaked into the project folder
    assert a.call("GET", "/api/service")[2]["running"] is True
    stopped = a.call("DELETE", "/api/service")[2]
    assert stopped == {"running": False, "last_exit": None} and wait_closed(st["port"])   # a deliberate stop is not a crash
    names = zipfile.ZipFile(io.BytesIO(a.call("GET", f"/api/runs/{run_id}/download")[2])).namelist()
    assert not any("/service/" in n for n in names)                                    # the service's own files are not downloaded


@with_clean_env
def test_only_one_service_runs_at_a_time(tmp_path):
    a = Admin(tmp_path)
    first = shortener_run(a)
    st = start(a, first)[2]
    second = shortener_run(a)
    status, _, res = start(a, second)
    assert status == 409 and first in res["error"]["message"] and not closed(st["port"])
    a.call("DELETE", "/api/service")
    status, _, st2 = start(a, second)
    assert status == 200 and st2["run_id"] == second
    a.call("DELETE", "/api/service")


@with_clean_env
def test_changing_services_needs_the_admin_token_but_status_does_not(tmp_path):
    a = Admin(tmp_path)
    run_id = shortener_run(a)
    assert a.call("POST", "/api/service", {"run_id": run_id}, token=False)[0] == 403
    assert a.call("DELETE", "/api/service", token=False)[0] == 403
    assert a.call("POST", "/api/service", {"run_id": run_id}, origin="http://evil.example.com")[0] == 403
    assert a.call("GET", "/api/service", token=False)[0] == 200 and not a.state.services.status()["running"]


@with_clean_env
def test_shutting_the_admin_down_stops_the_service(tmp_path):
    a = Admin(tmp_path)
    st = start(a, shortener_run(a))[2]
    assert not closed(st["port"])
    a.state.shutdown()
    assert wait_closed(st["port"])


@with_clean_env
def test_a_service_that_dies_is_reported_and_one_that_cannot_start_says_why(tmp_path):
    a = Admin(tmp_path)
    st = start(a, shortener_run(a))[2]
    a.state.services._proc.kill()
    a.state.services._proc.wait()
    status = a.call("GET", "/api/service")[2]
    assert status["running"] is False and status["last_exit"]["exit_code"] != 0 and status["last_exit"]["run_id"]
    boom = ("import sys\nfrom wsgiref.simple_server import make_server\ndef make_app(): pass\ndef serve(): pass\nif __name__ == '__main__':\n"
            "    print('boom: cannot bind', file=sys.stderr)\n    sys.exit(3)\n")
    run = fake_run(a, "run-boom", boom, {"mode": "mock"})
    code, _, res = start(a, run)
    assert code == 500 and "exited while starting" in res["error"]["message"] and "boom: cannot bind" in res["error"]["message"]


# ------------------------------------------------------------------------- confirmation for unreviewed code
@with_clean_env
def test_real_model_code_needs_confirmation_and_never_receives_the_api_key(tmp_path):
    a = Admin(tmp_path)
    os.environ["MY_SECRET_TOKEN"] = "super-secret-value"
    try:
        run_id = real_run(a, FakeServer(defaults=shortener.DEFAULTS), scenario="shortener", requirement="Build a shortener.")
        s = a.finish()
        assert s["run"]["status"] == "completed" and s["run"]["mode"] == "real" and s["run"]["runnable"] is True
        status, _, res = start(a, run_id)
        assert status == 400 and "confirm" in res["error"]["message"] and "mode: real" in res["error"]["message"]
        assert start(a, run_id, confirm_unreviewed="yes")[0] == 400                    # must be a real boolean true
        status, _, st = start(a, run_id, confirm_unreviewed=True)
        assert status == 200 and not closed(st["port"])
        environ = Path(f"/proc/{a.state.services._proc.pid}/environ")
        if environ.exists():                                                           # Linux: look at the child's real environment
            seen = environ.read_bytes().decode("utf-8", "replace")
            assert KEY not in seen and "super-secret-value" not in seen and "ANTHROPIC_API_KEY" not in seen
        log = (tmp_path / "runs" / run_id / "service" / "service.log").read_text(encoding="utf-8", errors="replace")
        assert KEY not in log
    finally:
        os.environ.pop("MY_SECRET_TOKEN", None)


@with_clean_env
def test_runs_of_unknown_origin_need_confirmation_too(tmp_path):
    a = Admin(tmp_path)
    code = shortener.GOOD_CODE
    cli_run = fake_run(a, "run-from-the-command-line", code)                          # no run.json: origin unknown
    status, _, res = start(a, cli_run)
    assert status == 400 and "mode: unknown" in res["error"]["message"]
    assert start(a, cli_run, confirm_unreviewed=True)[0] == 200
    a.call("DELETE", "/api/service")


# ------------------------------------------------------------------------------- enforced, not hoped for
@with_clean_env
def test_security_policies_are_rechecked_right_before_starting(tmp_path):
    a = Admin(tmp_path)
    run_id = shortener_run(a)
    impl = tmp_path / "runs" / run_id / "artifacts" / "implementation.py"
    impl.write_text(impl.read_text(encoding="utf-8") + "\neval('1+1')\n", encoding="utf-8")        # edited after the run
    status, _, res = start(a, run_id)
    assert status == 400 and "security policies now reject" in res["error"]["message"] and "no-dangerous-calls" in res["error"]["message"]
    assert not a.state.services.status()["running"]


def run_wrapper(script_body, tmp_path):
    script = tmp_path / "probe.py"
    script.write_text(script_body, encoding="utf-8")
    out = subprocess.run([sys.executable, "-c", svc.WRAPPER, str(script)], capture_output=True, text=True, timeout=20)
    return out.stdout.strip(), out.stderr


def test_the_wrapper_forces_every_bind_onto_loopback(tmp_path):
    out, err = run_wrapper("import socket\ns = socket.socket()\ns.bind(('0.0.0.0', 0))\nprint(s.getsockname()[0])\n", tmp_path)
    assert out == "127.0.0.1", err
    out, err = run_wrapper("import socket\ns = socket.socket()\ns.bind(('', 0))\nprint(s.getsockname()[0])\n", tmp_path)
    assert out == "127.0.0.1", err
    out, err = run_wrapper("import socket\ns = socket.socket()\ns.bind(('192.168.1.77', 0))\nprint(s.getsockname()[0])\n", tmp_path)
    assert out == "127.0.0.1", err
    if socket.has_ipv6:
        out, err = run_wrapper("import socket\ns = socket.socket(socket.AF_INET6)\ns.bind(('::', 0, 0, 0))\nprint(s.getsockname()[0])\n", tmp_path)
        assert out in ("::1", ""), err


def test_the_wrapper_runs_the_file_as_a_script_with_its_own_arguments(tmp_path):
    out, err = run_wrapper("import sys\nprint(__name__, len(sys.argv))\n", tmp_path)
    assert out == "__main__ 1", err


@with_clean_env
def test_generated_code_that_asks_for_every_interface_still_listens_on_loopback_only(tmp_path):
    a = Admin(tmp_path)
    run_id = shortener_run(a)
    impl = tmp_path / "runs" / run_id / "artifacts" / "implementation.py"
    code = impl.read_text(encoding="utf-8")
    assert 'def serve(host: str = "127.0.0.1"' in code
    impl.write_text(code.replace('def serve(host: str = "127.0.0.1"', 'def serve(host: str = "0.0.0.0"'), encoding="utf-8")   # what a careless model might write
    status, _, st = start(a, run_id)
    assert status == 200 and get(st["url"] + "healthz")[0] == 200
    tcp = Path("/proc/net/tcp")
    if tcp.exists():                                                                    # Linux: inspect the real listening socket
        listening = [line.split()[1] for line in tcp.read_text().splitlines()[1:] if line.split()[3] == "0A"]
        port_hex = f":{st['port']:04X}"
        mine = [addr for addr in listening if addr.endswith(port_hex)]
        assert mine and all(addr.startswith("0100007F") for addr in mine), mine          # 127.0.0.1, never 00000000 (every interface)
    a.call("DELETE", "/api/service")


def test_the_environment_given_to_generated_code_has_no_credentials():
    env = {"PATH": "/usr/bin", "HOME": "/home/x", "ANTHROPIC_API_KEY": "k", "GITHUB_TOKEN": "t", "AWS_SECRET_ACCESS_KEY": "s",
           "DB_PASSWORD": "p", "MY_AUTH_HEADER": "a", "api_key": "lower", "SYSTEMROOT": r"C:\Windows"}
    clean = svc.safe_env(env)
    assert set(clean) == {"PATH", "HOME", "SYSTEMROOT", "PYTHONUNBUFFERED", "PYTHONIOENCODING"}
    assert env["ANTHROPIC_API_KEY"] == "k"                                              # the caller's own environment is untouched


def test_stopping_a_service_also_stops_the_processes_it_started(tmp_path):
    """On Windows a virtualenv's python.exe is a launcher that starts the real interpreter as a CHILD. Stopping only the launcher
    left the real service running and its port open (found on a Windows machine). Reproduced here on any OS: a parent that starts
    a listening child, stopped the way the admin page stops a service."""
    port = svc.free_port()
    child = f"import socket, time\ns = socket.socket()\ns.bind(('127.0.0.1', {port}))\ns.listen()\ntime.sleep(120)\n"
    parent = f"import subprocess, sys, time\nsubprocess.Popen([sys.executable, '-c', {child!r}])\ntime.sleep(120)\n"
    with (tmp_path / "log.txt").open("wb") as log:
        proc = svc.spawn([sys.executable, "-c", parent], tmp_path, svc.safe_env(dict(os.environ)), log)
    try:
        end = time.time() + 8
        while closed(port) and time.time() < end:
            time.sleep(0.05)
        assert not closed(port), "the child never started listening"
        svc.terminate_tree(proc)
        assert wait_closed(port), "the child outlived its parent: an orphaned service"
    finally:
        svc.terminate_tree(proc)


def test_terminating_an_already_dead_leader_still_sweeps_up_its_children(tmp_path):
    port = svc.free_port()
    child = f"import socket, time\ns = socket.socket()\ns.bind(('127.0.0.1', {port}))\ns.listen()\ntime.sleep(120)\n"
    parent = f"import subprocess, sys\nsubprocess.Popen([sys.executable, '-c', {child!r}])\n"          # the parent exits at once
    with (tmp_path / "log.txt").open("wb") as log:
        proc = svc.spawn([sys.executable, "-c", parent], tmp_path, svc.safe_env(dict(os.environ)), log)
    proc.wait(10)
    end = time.time() + 8
    while closed(port) and time.time() < end:
        time.sleep(0.05)
    if os.name != "nt":                         # (taskkill cannot find the children of a dead parent; POSIX process groups can)
        assert not closed(port)
        svc.terminate_tree(proc)
        assert wait_closed(port)


def test_the_launcher_works_when_given_a_relative_run_folder(tmp_path):
    """Found on a Windows machine: with the default relative `runs` folder, the child (started in ANOTHER folder, the run's own
    `service` folder) looked for its file relative to that folder and found nothing. Every path handed to it must be absolute."""
    artifacts = tmp_path / "runs" / "run-rel" / "artifacts"
    artifacts.mkdir(parents=True)
    (artifacts / "implementation.py").write_text(shortener.GOOD_CODE, encoding="utf-8")
    before = os.getcwd()
    os.chdir(tmp_path)
    mgr = svc.ServiceManager()
    try:
        st = mgr.start(Path("runs") / "run-rel", "run-rel")                              # a RELATIVE path, as in a default setup
        assert st["running"] and get(st["url"] + "healthz")[0] == 200
        assert (tmp_path / "runs" / "run-rel" / "service" / "shortener.db").exists()      # and the child's files landed where intended
        assert not (tmp_path / "runs" / "run-rel" / "service" / "runs").exists()          # nothing doubled up
    finally:
        mgr.stop()
        os.chdir(before)
    assert wait_closed(st["port"])


@with_clean_env
def test_an_admin_page_started_with_the_default_relative_runs_folder_can_start_a_service(tmp_path):
    before = os.getcwd()
    os.chdir(tmp_path)
    try:
        a = Admin(tmp_path)
        a.state = AdminState("runs")                                                     # what `python -m orchestrator.admin` does by default
        a.app = make_app(a.state, {"127.0.0.1:8765", "localhost:8765"})
        assert a.state.run_root.is_absolute() and a.state.run_root == (tmp_path / "runs").resolve()
        run_id = shortener_run(a)
        status, _, st = start(a, run_id)
        assert status == 200 and get(st["url"] + "healthz")[0] == 200
        assert (tmp_path / "runs" / run_id / "service" / "shortener.db").exists()
        assert a.call("DELETE", "/api/service")[2]["running"] is False
        assert wait_closed(st["port"])
    finally:
        os.chdir(before)


def test_the_reasons_a_file_is_not_offered_as_a_service_are_specific():
    assert svc.why_not_runnable(MODEL_STYLE) is None
    assert "no `if __name__" in svc.why_not_runnable("def slugify(t):\n    return t\n")
    assert "no `if __name__" in svc.why_not_runnable("from wsgiref.simple_server import make_server\ndef run():\n    pass\n")
    assert "does not appear to start a web server" in svc.why_not_runnable("if __name__ == '__main__':\n    print('hi')\n")
    assert svc.is_runnable("if __name__ == \"__main__\":\n    HTTPServer(('127.0.0.1', 8000), H).serve_forever()\n")


@with_clean_env
def test_a_service_with_a_models_own_structure_is_offered_and_starts(tmp_path):
    """Found on the first successful live run: the page offered no Start button, because 'runnable' meant 'defines make_app and serve'."""
    a = Admin(tmp_path)
    renamed = shortener.GOOD_CODE.replace("make_app", "build_application").replace("serve(", "run_server(")
    assert "def make_app" not in renamed and "def serve(" not in renamed and "def run_server(" in renamed
    for name, code, path in (("run-model-classes", MODEL_STYLE, ""), ("run-renamed-reference", renamed, "healthz")):
        run = fake_run(a, name, code, {"mode": "real"})
        assert a.state.runnable(run) and a.call("GET", f"/api/runs/{run}")[2]["runnable"] is True
        assert start(a, run)[0] == 400                                                  # real-model code still needs the confirmation
        status, _, st = start(a, run, confirm_unreviewed=True)
        assert status == 200 and get(st["url"] + path) == (200, '{"status": "ok"}'), name       # it really answers over HTTP
        assert a.call("DELETE", "/api/service")[2]["running"] is False and wait_closed(st["port"])


@with_clean_env
def test_a_program_that_reads_its_port_as_a_flag_is_started_on_the_second_try(tmp_path):
    a = Admin(tmp_path)
    run = fake_run(a, "run-argparse", ARGPARSE_STYLE, {"mode": "real"})
    status, _, st = start(a, run, confirm_unreviewed=True)
    assert status == 200 and get(st["url"])[0] == 200
    log = (tmp_path / "runs" / run / "service" / "service.log").read_text(encoding="utf-8", errors="replace")
    assert "unrecognized arguments" in log                                             # the first form was tried and refused
    a.call("DELETE", "/api/service")


@with_clean_env
def test_an_unrelated_crash_is_not_retried_with_a_different_argument_form(tmp_path):
    a = Admin(tmp_path)
    boom = "import sys\nfrom wsgiref.simple_server import make_server\nif __name__ == '__main__':\n    print('boom: database is locked', file=sys.stderr)\n    sys.exit(3)\n"
    run = fake_run(a, "run-crash", boom, {"mode": "mock"})
    status, _, res = start(a, run)
    assert status == 500 and "boom: database is locked" in res["error"]["message"]
    assert (tmp_path / "runs" / run / "service" / "service.log").read_text(encoding="utf-8").count("boom: database is locked") == 1


@with_clean_env
def test_when_a_finished_service_run_cannot_be_started_the_page_says_why(tmp_path):
    a = Admin(tmp_path)
    run_id = shortener_run(a)
    assert a.snap()["run"]["runnable"] is True and not any("cannot offer" in n for n in a.snap()["notes"])
    (tmp_path / "runs" / run_id / "artifacts" / "implementation.py").write_text("def shorten(url):\n    return url\n", encoding="utf-8")
    snap = a.snap()
    assert snap["run"]["runnable"] is False
    assert any("cannot offer to start it as a service" in n and "no `if __name__" in n and "python implementation.py 8000" in n for n in snap["notes"])
    a.run()                                                                            # the toy scenario produces a function, not a service
    assert not any("cannot offer" in n for n in a.finish()["notes"])


def test_free_port_is_a_real_unused_loopback_port():
    p = svc.free_port()
    assert 1024 <= p <= 65535 and closed(p)

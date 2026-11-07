"""Greenfield scenario: the orchestrator builds the URL shortener, and the result really runs."""
import http.client
import importlib.util
import json
import threading
from wsgiref.simple_server import WSGIRequestHandler, make_server

from orchestrator.core.state import NodeState, RunStatus
from orchestrator.governance import approvals
from orchestrator.governance.policies import change_control as cc
from orchestrator.runner import run_workflow
from orchestrator.workflows import shortener, slugify


def go(tmp_path, llm=None, **kw):
    llm = llm or shortener.make_llm()
    graph, agents = shortener.build(llm)
    return run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="s", **kw)


def test_workflow_passes_governance_lint():
    graph, _ = shortener.build(shortener.make_llm())
    assert cc.lint_graph(graph) == []


def test_greenfield_run_completes_and_writes_all_artifacts(tmp_path):
    r = go(tmp_path)
    assert r["status"] is RunStatus.COMPLETED
    arts = r["run_dir"] / "artifacts"
    assert {"implementation.py", "tests.py", "docs.md", "requirements.json", "design.md", "review.md"} <= {p.name for p in arts.iterdir()}
    assert r["metrics"]["retries"] == 0 and r["metrics"]["policy_violations"] == {}
    assert r["engine"].attempts["tests"] == 1


def test_generated_service_runs_end_to_end_over_real_http(tmp_path):
    r = go(tmp_path)
    spec = importlib.util.spec_from_file_location("generated_shortener", r["run_dir"] / "artifacts" / "implementation.py")
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    class Quiet(WSGIRequestHandler):
        def log_message(self, *a):
            pass

    app = mod.make_app(mod.ShortenerService(mod.LinkRepository()), mod.RateLimiter(limit=100))
    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        def call(method, path, body=None, headers=None):
            c = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=5)
            c.request(method, path, json.dumps(body) if body is not None else None, headers or {})
            resp = c.getresponse()
            data = resp.read()
            return resp.status, dict(resp.getheaders()), json.loads(data) if data else None

        status, _, created = call("POST", "/api/shorten", {"url": "https://example.com/some/long/path"})
        assert status == 201
        status, headers, _ = call("GET", "/" + created["code"], headers={"Referer": "https://ref.example/"})
        assert status == 302 and headers["Location"] == "https://example.com/some/long/path"
        status, _, stats = call("GET", f"/api/links/{created['code']}/stats")
        assert status == 200 and stats["total_clicks"] == 1
        assert call("POST", "/api/shorten", {"url": "http://localhost/admin"})[0] == 400
        assert call("GET", "/healthz")[0] == 200
    finally:
        server.shutdown()
        server.server_close()


def test_secret_in_generated_service_is_blocked_then_clean_retry_succeeds(tmp_path):
    fx = shortener.FIXTURES
    r = go(tmp_path, shortener.make_llm(scripted={"developer": [fx["bad_secret"], fx["good_code"]]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"security/no-hardcoded-secrets": 1}


def test_unapproved_dependency_and_missing_docstring_are_each_named(tmp_path):
    fx = shortener.FIXTURES
    r = go(tmp_path, shortener.make_llm(scripted={"developer": [fx["bad_import"], fx["undocumented"], fx["good_code"]]}))
    assert r["status"] is RunStatus.COMPLETED
    assert r["metrics"]["policy_violations"] == {"security/dependency-allowlist": 1, "compliance/public-api-documented": 1}


def test_thin_tests_block_release(tmp_path):
    r = go(tmp_path, shortener.make_llm(scripted={"tester": [shortener.FIXTURES["thin_tests"]]}))
    assert r["status"] is RunStatus.HALTED and r["engine"].state["release"] is NodeState.SKIPPED


def test_change_control_size_limit_blocks_an_oversized_change(tmp_path):
    """The same service under the toy 200-line limit is refused: limits are explicit, reviewed settings."""
    llm = shortener.make_llm()
    graph, agents = slugify.build(llm, shortener.REQUIREMENT, code_path="shortener.py", max_lines=200)
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="big")
    assert r["status"] is RunStatus.HALTED
    assert "change_control/max-lines-changed" in r["metrics"]["policy_violations"]
    assert "implementation" not in r["engine"].context


def test_primary_failure_falls_back_and_still_completes(tmp_path):
    r = go(tmp_path, shortener.make_llm(fail_times={"developer": 5}))
    assert r["status"] is RunStatus.COMPLETED and r["engine"].producer["implementation"] == "developer_fallback"


def test_design_revision_that_fails_is_rolled_back_with_the_service_intact(tmp_path):
    llm = shortener.make_llm()

    def break_developers():
        llm.inject_failures("developer", 99)
        llm.inject_failures("developer_fallback", 99)

    graph, agents = shortener.build(llm)
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="rb",
                     revisions=[("design", "pagination redesign", break_developers)])
    assert r["status"] is RunStatus.HALTED and r["metrics"]["rollbacks"] == 1
    assert (r["run_dir"] / "artifacts" / "implementation.py").read_text() == shortener.GOOD_CODE


def test_cli_runs_the_shortener_scenarios(tmp_path):
    from orchestrator import cli
    base = ["run", "shortener", "--approve", "auto", "--run-root", str(tmp_path)]
    assert cli.main(base) == 0
    assert cli.main(base + ["--violate", "secret"]) == 0
    assert cli.main(base + ["--fail", "developer:5"]) == 0
    assert cli.main(base + ["--violate", "thin-tests"]) == 1
    assert cli.main(base + ["--revise-design", "fail"]) == 1

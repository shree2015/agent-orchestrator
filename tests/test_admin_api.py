"""Admin backend: security, key handling, validation, and every way a run can go, driven through the HTTP API."""
import io
import json
import os
import time
import zipfile

import pytest

from orchestrator.admin.server import make_app
from orchestrator.admin.session import AdminState, describe
from orchestrator.llm import anthropic_client as ac
from tests.test_real_llm import KEY, FakeServer, error

HOST = "127.0.0.1:8765"


LIVE = []


class Admin:
    """Calls the WSGI app directly (no sockets) and remembers everything the server ever said, to scan for secrets."""

    def __init__(self, tmp_path):
        self.state = AdminState(tmp_path / "runs")
        LIVE.append(self)
        self.app = make_app(self.state, {HOST, "localhost:8765"})
        self.spoken = []

    def call(self, method, path, body=None, *, token=True, host=HOST, origin=None, raw=None):
        data = raw if raw is not None else (json.dumps(body).encode() if body is not None else b"")
        environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(data)), "wsgi.input": io.BytesIO(data),
                   "HTTP_HOST": host}
        if token:
            environ["HTTP_X_ADMIN_TOKEN"] = self.app.token if token is True else token
        if origin:
            environ["HTTP_ORIGIN"] = origin
        seen = {}

        def start_response(status, headers):
            seen["status"], seen["headers"] = int(status.split()[0]), dict(headers)

        out = b"".join(self.app(environ, start_response))
        self.spoken.append(out)
        ctype = seen["headers"].get("Content-Type", "")
        if ctype.startswith("application/json"):
            return seen["status"], seen["headers"], json.loads(out)
        return seen["status"], seen["headers"], out

    def run(self, **over):
        body = {"scenario": "slugify", "requirement": "Write slugify(text).", "mode": "mock", "auto_approve": True}
        body.update(over)
        status, _, res = self.call("POST", "/api/runs", body)
        assert status == 201, res
        return res["run_id"]

    def snap(self):
        return self.call("GET", "/api/runs/current")[2]

    def wait(self, cond, timeout=20.0):
        end = time.time() + timeout
        while time.time() < end:
            s = self.snap()
            if cond(s):
                return s
            time.sleep(0.02)
        raise AssertionError(f"timed out; last status: {self.snap()['run']['status']}")

    def finish(self, **kw):
        return self.wait(lambda s: not s["run"]["busy"], **kw)


def with_clean_env(fn):
    def wrapper(tmp_path):
        saved = os.environ.pop("ANTHROPIC_API_KEY", None)
        try:
            fn(tmp_path)
        finally:
            for admin in LIVE:                       # a run blocked on a decision must not keep the process alive
                admin.state.shutdown()
            LIVE.clear()
            ac.TRANSPORT_OVERRIDE = None
            if saved is not None:
                os.environ["ANTHROPIC_API_KEY"] = saved
    wrapper.__name__ = fn.__name__
    return wrapper


# ------------------------------------------------------------------------------ request security
@with_clean_env
def test_only_loopback_hosts_are_answered_dns_rebinding(tmp_path):
    a = Admin(tmp_path)
    assert a.call("GET", "/api/config")[0] == 200
    for bad in ("evil.example.com", "evil.example.com:8765", "127.0.0.1:9999", "0.0.0.0:8765", ""):
        assert a.call("GET", "/api/config", host=bad)[0] == 403, bad
    assert a.call("GET", "/", host="evil.example.com")[0] == 403


@with_clean_env
def test_state_changing_requests_need_the_token_and_a_matching_origin(tmp_path):
    a = Admin(tmp_path)
    body = {"scenario": "slugify", "requirement": "x", "mode": "mock"}
    assert a.call("POST", "/api/runs", body, token=False)[0] == 403
    assert a.call("POST", "/api/runs", body, token="wrong-token")[0] == 403
    assert a.call("DELETE", "/api/key", token=False)[0] == 403
    assert a.call("POST", "/api/runs", body, origin="http://evil.example.com")[0] == 403          # a cross-site page
    assert a.call("POST", "/api/key", {"key": "x" * 30}, origin="http://evil.example.com")[0] == 403
    assert a.call("POST", "/api/runs/current/stop", {}, token=False)[0] == 403
    assert a.state.session is None and a.state.key_status() == "none"                                # nothing happened
    assert a.call("GET", "/api/runs", token=False)[0] == 200                                         # reads need no token
    assert a.call("POST", "/api/key", {"key": "k" * 30}, origin=f"http://{HOST}")[0] == 200          # our own origin is fine


@with_clean_env
def test_oversized_malformed_and_unknown_requests_are_refused_cleanly(tmp_path):
    a = Admin(tmp_path)
    assert a.call("POST", "/api/runs", raw=b"x" * 70_000)[0] == 413
    assert a.call("POST", "/api/runs", raw=b"{not json")[0] == 400
    assert a.call("POST", "/api/runs", raw=b"[1,2]")[0] == 400
    assert a.call("GET", "/api/nothing")[0] == 404
    assert a.call("PUT", "/api/runs")[0] == 405
    status, _, body = a.call("GET", "/api/runs/current")
    assert status == 200 and body == {"run": None}


# ------------------------------------------------------------------------------------------ config
@with_clean_env
def test_config_describes_scenarios_key_status_and_templates(tmp_path):
    a = Admin(tmp_path)
    _, _, cfg = a.call("GET", "/api/config")
    assert cfg["key_status"] == "none" and cfg["token"] == a.app.token and cfg["default_model"]
    assert [s["id"] for s in cfg["scenarios"]] == ["shortener", "ambiguous", "slugify"]
    assert all(s["default_requirement"] and s["description"] for s in cfg["scenarios"])
    assert {"developer", "tester", "planner"} <= set(cfg["roles"])
    _, _, t = a.call("GET", "/api/template/shortener")
    assert "short links" in t["requirement"] and "POST /api/shorten" in t["requirement"] and "Edit this file" not in t["requirement"]
    assert a.call("GET", "/api/template/nope")[0] == 404


# -------------------------------------------------------------------------------------------- key
@with_clean_env
def test_the_api_key_is_kept_in_memory_only_and_never_echoed(tmp_path):
    a = Admin(tmp_path)
    secret = "sk-ant-ADMIN-PAGE-SECRET-1234567890"
    status, _, res = a.call("POST", "/api/key", {"key": f"  {secret}  "})
    assert status == 200 and res == {"key_status": "session"}
    assert a.call("GET", "/api/config")[2]["key_status"] == "session"
    assert a.call("DELETE", "/api/key")[2] == {"key_status": "none"}
    assert all(secret.encode() not in out for out in a.spoken)
    assert not any(secret in p.read_text(encoding="utf-8", errors="ignore") for p in tmp_path.rglob("*") if p.is_file())


@with_clean_env
def test_nonsense_keys_are_refused_and_an_environment_key_is_detected(tmp_path):
    a = Admin(tmp_path)
    for bad in ("short", "has spaces in the middle of the key 1234", "x" * 400, "", None, 12345, ["a"], "line\nbreak" + "x" * 30):
        assert a.call("POST", "/api/key", {"key": bad})[0] == 400, repr(bad)
    os.environ["ANTHROPIC_API_KEY"] = "sk-ant-from-the-environment-0000"
    try:
        assert a.call("GET", "/api/config")[2]["key_status"] == "env"
        a.call("POST", "/api/key", {"key": "k" * 30})
        assert a.call("GET", "/api/config")[2]["key_status"] == "session"                # a pasted key wins
        assert a.call("DELETE", "/api/key")[2]["key_status"] == "env"
    finally:
        os.environ.pop("ANTHROPIC_API_KEY", None)


# ------------------------------------------------------------------------------------ validation
@with_clean_env
def test_starting_a_run_validates_every_input(tmp_path):
    a = Admin(tmp_path)
    ok = {"scenario": "slugify", "requirement": "Write slugify.", "mode": "mock", "auto_approve": True}
    bad = [({**ok, "scenario": "brownfield"}, "scenario"), ({**ok, "requirement": "   "}, "requirement"),
           ({**ok, "requirement": 5}, "requirement"), ({**ok, "requirement": "x" * 20_001}, "too long"),
           ({**ok, "mode": "turbo"}, "mode"), ({**ok, "mode": "real"}, "no API key")]
    for body, needle in bad:
        status, _, res = a.call("POST", "/api/runs", body)
        assert status == 400 and needle in res["error"]["message"], (body.get("mode"), res)
    a.call("POST", "/api/key", {"key": "k" * 30})
    real = {**ok, "mode": "real"}
    for body, needle in [(real, "confirm"), ({**real, "confirm_real": True, "model": "bad model!"}, "model"),
                         ({**real, "confirm_real": True, "max_calls": 0}, "call budget"), ({**real, "confirm_real": True, "max_calls": 201}, "call budget"),
                         ({**real, "confirm_real": True, "max_calls": True}, "call budget"), ({**real, "confirm_real": True, "max_tokens": 10}, "output limit"),
                         ({**real, "confirm_real": True, "roles": ["hacker"]}, "roles")]:
        status, _, res = a.call("POST", "/api/runs", body)
        assert status == 400 and needle in res["error"]["message"], (body, res)
    assert a.state.session is None


# ---------------------------------------------------------------------------------- a mock run
@with_clean_env
def test_a_mock_run_completes_and_everything_about_it_is_inspectable(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run()
    s = a.finish()
    assert s["run"]["status"] == "completed" and s["run"]["id"] == run_id and s["run"]["mode"] == "mock"
    assert [n["id"] for n in s["nodes"]][0] == "requirements" and all(n["state"] == "DONE" for n in s["nodes"])
    assert s["metrics"]["run_status"] == "COMPLETED" and s["metrics"]["llm_calls"] == 0
    assert "implementation.py" in s["artifacts"] and "report.md" not in s["artifacts"]
    texts = [t["text"] for t in s["timeline"]]
    assert texts[0] == "Run started" and any("implementation done" in t for t in texts) and texts[-1] == "Run finished: COMPLETED"
    # history and detail
    runs = a.call("GET", "/api/runs")[2]["runs"]
    assert runs[0]["run_id"] == run_id and runs[0]["scenario"] == "slugify" and runs[0]["status"] == "completed"
    detail = a.call("GET", f"/api/runs/{run_id}")[2]
    assert detail["requirement"] == "Write slugify(text)." and "# Run report" in detail["report"] and "implementation.py" in detail["artifacts"]
    art = a.call("GET", f"/api/runs/{run_id}/artifacts/implementation.py")[2]
    assert "def slugify" in art["content"] and art["size"] > 0 and not art["truncated"]
    assert a.call("GET", f"/api/runs/{run_id}/audit-check")[2] == {"intact": True, "events": a.call("GET", f"/api/runs/{run_id}/audit-check")[2]["events"]}


@with_clean_env
def test_artifact_access_cannot_escape_the_run_folder(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run()
    a.finish()
    (tmp_path / "secret.txt").write_text("TOP SECRET", encoding="utf-8")
    for name in ("..", "../secret.txt", "..%2fsecret.txt", "audit.jsonl", "nope.py", "run.json"):
        status, _, body = a.call("GET", f"/api/runs/{run_id}/artifacts/{name}")
        assert status in (400, 404) and b"TOP SECRET" not in json.dumps(body).encode(), name
    assert a.call("GET", "/api/runs/..%2f..%2fetc/artifacts/passwd")[0] in (400, 404)
    assert a.call("GET", "/api/runs/does-not-exist")[0] == 404
    assert a.call("GET", "/api/runs/bad id")[0] == 404


@with_clean_env
def test_download_contains_the_deliverables_and_the_audit_trail_but_never_the_prompts(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run(record=True)
    a.finish()
    assert (tmp_path / "runs" / run_id / "llm-records").is_dir()
    status, headers, data = a.call("GET", f"/api/runs/{run_id}/download")
    assert status == 200 and headers["Content-Type"] == "application/zip" and run_id in headers["Content-Disposition"]
    names = zipfile.ZipFile(io.BytesIO(data)).namelist()
    assert f"{run_id}/report.md" in names and f"{run_id}/audit.jsonl" in names and f"{run_id}/artifacts/implementation.py" in names
    assert not any("llm-records" in n for n in names)


@with_clean_env
def test_a_tampered_audit_log_is_detected_by_the_check(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run()
    a.finish()
    log = tmp_path / "runs" / run_id / "audit.jsonl"
    log.write_text(log.read_text(encoding="utf-8").replace("COMPLETED", "FAKED!!!!", 1), encoding="utf-8")
    assert a.call("GET", f"/api/runs/{run_id}/audit-check")[2]["intact"] is False


@with_clean_env
def test_only_one_run_at_a_time_and_a_new_one_can_start_afterwards(tmp_path):
    a = Admin(tmp_path)
    a.run(auto_approve=False)
    a.wait(lambda s: s["pending"])
    status, _, res = a.call("POST", "/api/runs", {"scenario": "slugify", "requirement": "x", "mode": "mock"})
    assert status == 409 and res["error"]["code"] == "busy"
    a.call("POST", "/api/runs/current/stop", {})
    a.finish()
    assert a.call("POST", "/api/runs", {"scenario": "slugify", "requirement": "y", "mode": "mock", "auto_approve": True})[0] == 201
    a.finish()


# ----------------------------------------------------------------------------- human decisions
@with_clean_env
def test_approvals_are_made_on_the_page_and_show_what_is_being_approved(tmp_path):
    a = Admin(tmp_path)
    a.run(auto_approve=False)
    s = a.wait(lambda s: s["pending"])
    assert s["run"]["status"] == "waiting"
    p = s["pending"][0]
    assert p["kind"] == "approval" and p["node"] == "design" and "design" in p["title"].lower() and len(p["content"]) > 20
    assert a.call("POST", f"/api/runs/current/decisions/{p['id']}", {"approve": "yes"})[0] == 400       # must be a real boolean
    assert a.call("POST", f"/api/runs/current/decisions/{p['id']}", {"approve": True})[0] == 200
    assert a.call("POST", f"/api/runs/current/decisions/{p['id']}", {"approve": True})[0] == 404         # already answered
    assert a.call("POST", "/api/runs/current/decisions/nonexistent", {"approve": True})[0] == 404
    s = a.wait(lambda s: any(x["node"] == "release" for x in s["pending"]))
    assert any(t["text"] == "design: approved by a human" for t in s["timeline"])
    assert a.call("POST", f"/api/runs/current/decisions/{s['pending'][0]['id']}", {"approve": True})[0] == 200
    s = a.finish()
    assert s["run"]["status"] == "completed" and s["metrics"]["approvals_requested"] == 2


@with_clean_env
def test_rejecting_an_approval_halts_the_run_safely(tmp_path):
    a = Admin(tmp_path)
    a.run(auto_approve=False)
    s = a.wait(lambda s: s["pending"])
    a.call("POST", f"/api/runs/current/decisions/{s['pending'][0]['id']}", {"approve": False})
    s = a.finish()
    assert s["run"]["status"] == "halted" and "design rejected by human" in s["run"]["halt_reason"]
    assert {n["id"]: n["state"] for n in s["nodes"]}["implementation"] == "SKIPPED"


@with_clean_env
def test_stop_is_a_safe_stop_that_releases_waiting_decisions(tmp_path):
    a = Admin(tmp_path)
    a.run(auto_approve=False)
    a.wait(lambda s: s["pending"])
    assert a.call("POST", "/api/runs/current/stop", {})[0] == 200
    s = a.finish()
    assert s["run"]["status"] == "stopped" and s["run"]["halt_reason"] == "stopped by the operator"
    assert s["pending"] == [] and not any(n["state"] == "RUNNING" for n in s["nodes"])
    assert {n["id"]: n["state"] for n in s["nodes"]}["implementation"] == "SKIPPED"
    assert a.call("POST", "/api/runs/current/stop", {})[0] == 409                      # nothing is running any more


# ------------------------------------------------------------------- the ambiguous scenario
def answer(a, value=None, skip=False):
    s = a.wait(lambda s: s["pending"])
    p = s["pending"][0]
    assert p["kind"] == "question"
    body = {"skip": True} if skip else {"answer": value}
    return p, a.call("POST", f"/api/runs/current/decisions/{p['id']}", body)


@with_clean_env
def test_questions_are_asked_on_the_page_and_high_risk_ones_cannot_be_skipped(tmp_path):
    a = Admin(tmp_path)
    a.run(scenario="ambiguous", requirement="Make links expire sensibly.", auto_approve=True)    # auto: only high-risk is asked
    p, (status, _, res) = answer(a, skip=True)
    assert p["id"] and p["risk"] == "high" and p["options"] == [30, 90, 180] and p["proposed"] == 90
    assert status == 400 and "high-risk" in res["error"]["message"]
    assert a.call("POST", f"/api/runs/current/decisions/{p['id']}", {"answer": 45})[0] == 400          # not an option
    assert a.call("POST", f"/api/runs/current/decisions/{p['id']}", {"answer": "90"})[0] == 200        # text is matched to the option
    s = a.finish()
    assert s["run"]["status"] == "completed"
    assert "DEFAULT_TTL_S = 90 * 24 * 3600" in a.call("GET", f"/api/runs/{s['run']['id']}/artifacts/implementation.py")[2]["content"]


@with_clean_env
def test_lower_risk_questions_can_be_assumed_or_answered(tmp_path):
    a = Admin(tmp_path)
    a.run(scenario="ambiguous", requirement="Make links expire sensibly.", auto_approve=False)
    p, _ = answer(a, 180)                                  # high risk
    p2, (status, _, _) = answer(a, skip=True)              # medium risk: accept the proposed default
    assert p2["risk"] == "medium" and status == 200
    p3, (status, _, _) = answer(a, "yes")                  # medium risk: explicit answer
    assert status == 200
    for _ in range(2):                                     # design and release approvals
        s = a.wait(lambda s: s["pending"] and s["pending"][0]["kind"] == "approval")
        a.call("POST", f"/api/runs/current/decisions/{s['pending'][0]['id']}", {"approve": True})
        time.sleep(0.05)
    s = a.finish()
    d = {x["id"]: x for x in s["decisions"]}
    assert d["default_ttl_days"]["answer"] == 180 and d["default_ttl_days"]["decided_by"] == "human"
    assert d["max_ttl_days"]["decided_by"] == "assumed" and d["ttl_applies_to_aliases"]["answer"] == "yes"


@with_clean_env
def test_changing_an_answer_afterwards_redoes_only_the_dependent_work(tmp_path):
    a = Admin(tmp_path)
    a.run(scenario="ambiguous", requirement="Make links expire sensibly.", auto_approve=True)
    answer(a, 90)
    s = a.finish()
    assert s["can_revise"] and {d["id"] for d in s["decisions"]} == {"default_ttl_days", "max_ttl_days", "ttl_applies_to_aliases"}
    status, _, res = a.call("POST", "/api/runs/current/revise", {"decision_id": "default_ttl_days", "value": "180"})
    assert status == 200 and res["accepted"] and res["changed"] and set(res["affected"]) == {"design", "implementation", "tests", "docs", "review", "release"}
    s = a.finish()
    assert s["run"]["status"] == "completed" and s["metrics"]["revisions"] == 1 and s["metrics"]["nodes_replanned"] == 6
    assert "DEFAULT_TTL_S = 180 * 24 * 3600" in a.call("GET", f"/api/runs/{s['run']['id']}/artifacts/implementation.py")[2]["content"]
    assert any("revised by human" in t["text"] for t in s["timeline"])
    assert {n["id"]: n["attempts"] for n in s["nodes"]}["requirements"] == 1


@with_clean_env
def test_revision_edge_cases_same_answer_invalid_option_contradiction_and_no_run(tmp_path):
    a = Admin(tmp_path)
    assert a.call("POST", "/api/runs/current/revise", {"decision_id": "x", "value": 1})[0] == 404       # no run yet
    a.run(scenario="ambiguous", requirement="Make links expire sensibly.", auto_approve=True)
    answer(a, 30)
    a.finish()
    same = a.call("POST", "/api/runs/current/revise", {"decision_id": "default_ttl_days", "value": 30})[2]
    assert same["accepted"] and not same["changed"]
    assert a.call("POST", "/api/runs/current/revise", {"decision_id": "default_ttl_days", "value": 45})[0] == 400
    assert a.call("POST", "/api/runs/current/revise", {"decision_id": "nonsense", "value": 1})[0] == 400
    a.call("POST", "/api/runs/current/revise", {"decision_id": "max_ttl_days", "value": 90})
    a.finish()
    refused = a.call("POST", "/api/runs/current/revise", {"decision_id": "default_ttl_days", "value": 180})[2]
    assert refused["accepted"] is False and "decisions-consistent" in refused["why"]
    assert a.snap()["run"]["status"] == "completed"                                                    # the system stays as it was


@with_clean_env
def test_non_question_scenarios_offer_no_revision(tmp_path):
    a = Admin(tmp_path)
    a.run()
    s = a.finish()
    assert s["can_revise"] is False and a.call("POST", "/api/runs/current/revise", {"decision_id": "x", "value": 1})[0] == 400


# ------------------------------------------------------------------------------------ real mode
def real_run(a, server, **over):
    ac.TRANSPORT_OVERRIDE = server
    a.call("POST", "/api/key", {"key": KEY})
    return a.run(mode="real", confirm_real=True, model="test-model", **over)


@with_clean_env
def test_a_real_run_uses_the_session_key_reports_usage_and_leaks_nothing(tmp_path):
    a = Admin(tmp_path)
    server = FakeServer()
    run_id = real_run(a, server)
    s = a.finish()
    assert s["run"]["status"] == "completed" and s["run"]["mode"] == "real" and s["run"]["model"] == "test-model"
    assert len(server.requests) == 6 and server.requests[0]["headers"]["x-api-key"] == KEY
    assert server.requests[0]["payload"]["model"] == "test-model"
    assert s["llm_usage"]["calls"] == 6 and s["metrics"]["llm_calls"] == 6 and s["metrics"]["llm_input_tokens"] > 0
    assert any(t["text"].startswith("model call: requirements") for t in s["timeline"])
    run = tmp_path / "runs" / run_id
    assert len(list((run / "llm-records").iterdir())) == 6                                  # recording defaults to ON for real runs
    everything = b"".join(a.spoken) + b"".join(p.read_bytes() for p in tmp_path.rglob("*") if p.is_file())
    assert KEY.encode() not in everything                                                  # not in responses, logs, records or files
    meta = json.loads((run / "run.json").read_text(encoding="utf-8"))
    assert meta["mode"] == "real" and "api" not in json.dumps(meta).lower().replace("api_", "")


@with_clean_env
def test_hybrid_roles_and_the_call_budget_are_honoured_on_the_page(tmp_path):
    a = Admin(tmp_path)
    server = FakeServer()
    real_run(a, server, roles=["architect", "reviewer"])
    s = a.finish()
    assert s["run"]["status"] == "completed" and sorted(server.roles()) == ["architect", "reviewer"]
    b = Admin(tmp_path / "b")
    server2 = FakeServer()
    real_run(b, server2, max_calls=2)
    s = b.finish()
    assert s["run"]["status"] == "halted" and "budget exhausted" in (s["run"]["halt_reason"] or "") and len(server2.requests) == 2


@with_clean_env
def test_an_authentication_failure_stops_the_run_with_a_helpful_reason(tmp_path):
    a = Admin(tmp_path)
    server = FakeServer(lambda *x: error(401, "authentication_error", "invalid x-api-key"))
    real_run(a, server)
    s = a.finish()
    assert s["run"]["status"] == "halted" and "ANTHROPIC_API_KEY" in s["run"]["halt_reason"] and len(server.requests) == 1


@with_clean_env
def test_the_environment_key_is_used_when_nothing_was_pasted(tmp_path):
    a = Admin(tmp_path)
    os.environ["ANTHROPIC_API_KEY"] = KEY
    ac.TRANSPORT_OVERRIDE = server = FakeServer()
    a.run(mode="real", confirm_real=True)
    s = a.finish()
    assert s["run"]["status"] == "completed" and server.requests[0]["headers"]["x-api-key"] == KEY and a.state.key_status() == "env"


@with_clean_env
def test_mock_mode_warns_that_it_ignores_a_custom_requirement(tmp_path):
    a = Admin(tmp_path)
    a.run(requirement="Something completely different.")
    s = a.finish()
    assert any("ignore the requirement" in n for n in s["notes"])
    a.run(requirement=__import__("orchestrator.workflows.slugify", fromlist=["x"]).REQUIREMENT)
    assert a.finish()["notes"] == []


# ------------------------------------------------------------------------------- the activity feed
def test_describe_turns_audit_events_into_plain_sentences():
    assert describe({"event": "attempt_failed", "node": "implementation", "attempt": 2, "stage": "primary",
                     "error": "[security/no-hardcoded-secrets] leak", "will_retry": True}).endswith("- retrying")
    assert describe({"event": "rollback", "restored": ["a", "b"]}) == "ROLLBACK: restored a, b"
    assert describe({"event": "state_change"}) is None and describe({"event": "exit_gates"}) is None
    assert "automatic" in describe({"event": "replan", "auto": True, "invalidated": ["x"]})

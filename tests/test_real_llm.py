"""Real-LLM support, tested without a network: the real client code runs against a fake Anthropic server."""
import json
import os
import threading

import pytest

from orchestrator import cli
from orchestrator.core.engine import Engine
from orchestrator.core.graph import Graph, Node
from orchestrator.core.state import RunStatus
from orchestrator.governance import approvals
from orchestrator.llm import anthropic_client as ac, contracts, mock_client as mc
from orchestrator.llm.base import LLMError
from orchestrator.llm.routing import RecordingLLM, RoutingLLM
from orchestrator.runner import run_workflow
from orchestrator.workflows import ambiguous as am, brownfield as bf, shortener, slugify

KEY = "sk-ant-TEST-SECRET-0123456789"


class FakeServer:
    """Stands in for api.anthropic.com. Answers each role with its stored artifact unless `respond` overrides it."""

    def __init__(self, respond=None, defaults=None):
        self.requests, self.respond, self.lock = [], respond, threading.Lock()
        self.defaults = {**mc.DEFAULTS, **(defaults or {})}
        self.by_system = {contracts.system_for(r): r for r in list(contracts.CONTRACTS) + ["release_manager"]}

    def __call__(self, url, headers, body, timeout):
        payload = json.loads(body)
        role = self.by_system.get(payload["system"], "?")
        prompt = payload["messages"][0]["content"]
        with self.lock:
            self.requests.append({"url": url, "headers": dict(headers), "payload": payload, "role": role, "prompt": prompt})
            attempt = sum(1 for r in self.requests if r["role"] == role)
        answer = self.respond(role, prompt, attempt) if self.respond else None
        if isinstance(answer, tuple):                                   # (status, headers, body) override
            return answer
        text = answer if answer is not None else self.defaults[role]
        body = json.dumps({"id": "msg_1", "type": "message", "role": "assistant", "model": payload["model"],
                           "content": [{"type": "text", "text": text}], "stop_reason": "end_turn",
                           "usage": {"input_tokens": len(prompt) // 4, "output_tokens": len(text) // 4}})
        return 200, {"request-id": "req_fake"}, body.encode()

    def roles(self):
        return [r["role"] for r in self.requests]


def client(server, **kw):
    kw.setdefault("sleep", lambda s: None)
    return ac.AnthropicClient(model="test-model", api_key=KEY, transport=server, **kw)


def http(status, body, headers=None):
    return (status, headers or {}, json.dumps(body).encode())


def error(status, kind, message, headers=None):
    return http(status, {"type": "error", "error": {"type": kind, "message": message}}, headers)


# ---------------------------------------------------------------- the request itself
def test_request_follows_the_messages_api_shape_and_never_puts_the_key_in_the_body():
    server = FakeServer()
    text = client(server).complete("developer", "Write it.")
    req = server.requests[0]
    assert req["url"] == "https://api.anthropic.com/v1/messages"
    assert req["headers"] == {"x-api-key": KEY, "anthropic-version": "2023-06-01", "content-type": "application/json"}
    p = req["payload"]
    assert set(p) == {"model", "max_tokens", "system", "messages"} and p["model"] == "test-model" and p["max_tokens"] == 16000
    assert p["messages"] == [{"role": "user", "content": "Write it."}] and p["system"] == contracts.system_for("developer")
    assert KEY not in json.dumps(p) and text == mc.DEFAULTS["developer"]


def test_multiple_text_blocks_are_joined_and_other_block_types_ignored():
    body = {"content": [{"type": "text", "text": "A"}, {"type": "tool_use", "name": "x"}, {"type": "text", "text": "B"}],
            "usage": {"input_tokens": 1, "output_tokens": 2}, "stop_reason": "end_turn"}
    assert client(FakeServer(lambda *a: http(200, body))).complete("reviewer", "p") == "AB"


def test_a_missing_key_is_a_clear_error_that_explains_where_to_set_it(monkeypatch=None):
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        with pytest.raises(LLMError, match="ANTHROPIC_API_KEY is not set"):
            ac.AnthropicClient()
    finally:
        if saved:
            os.environ["ANTHROPIC_API_KEY"] = saved


# --------------------------------------------------------------------------- failures
def test_a_cut_off_answer_and_an_empty_answer_are_rejected_not_passed_to_the_gates():
    cut = {"content": [{"type": "text", "text": "def half("}], "stop_reason": "max_tokens", "usage": {}}
    with pytest.raises(LLMError, match="cut off at max_tokens"):
        client(FakeServer(lambda *a: http(200, cut))).complete("developer", "p")
    empty = {"content": [], "stop_reason": "end_turn", "usage": {}}
    with pytest.raises(LLMError, match="no text"):
        client(FakeServer(lambda *a: http(200, empty))).complete("developer", "p")


def test_rate_limits_and_server_errors_are_retried_with_backoff_and_retry_after():
    answers = iter([error(429, "rate_limit_error", "slow down", {"retry-after": "2"}), error(500, "api_error", "oops"), None])
    sleeps = []
    server = FakeServer(lambda *a: next(answers))
    assert client(server, sleep=sleeps.append).complete("reviewer", "p") == mc.DEFAULTS["reviewer"]
    assert sleeps == [2.0, 2.0] and len(server.requests) == 3


def test_overloaded_and_network_errors_are_retried_then_the_run_gives_up_with_the_reason():
    server = FakeServer(lambda *a: error(529, "overloaded_error", "busy"))
    with pytest.raises(LLMError, match=r"giving up after 4 attempts: HTTP 529 overloaded_error: busy"):
        client(server).complete("reviewer", "p")
    calls = []

    def flaky(url, headers, body, timeout):
        calls.append(1)
        if len(calls) < 3:
            raise ConnectionResetError("reset")
        return FakeServer()(url, headers, body, timeout)
    assert client(flaky).complete("reviewer", "p") == mc.DEFAULTS["reviewer"] and len(calls) == 3


def test_authentication_and_unknown_model_errors_are_fatal_and_fail_fast():
    server = FakeServer(lambda *a: error(401, "authentication_error", "invalid x-api-key"))
    c = client(server)
    with pytest.raises(LLMError, match="check ANTHROPIC_API_KEY"):
        c.complete("developer", "p")
    with pytest.raises(LLMError):
        c.complete("developer", "p")
    assert len(server.requests) == 1                                          # no further network calls
    model = client(FakeServer(lambda *a: error(404, "not_found_error", "model: nope")))
    with pytest.raises(LLMError, match="set --model"):
        model.complete("developer", "p")


def test_a_bad_request_is_reported_but_not_fatal():
    server = FakeServer(lambda r, p, n: error(400, "invalid_request_error", "bad") if n == 1 else None)
    c = client(server)
    with pytest.raises(LLMError, match="invalid_request_error: bad"):
        c.complete("developer", "p")
    assert c.complete("developer", "p")                                       # the next call goes out normally


def test_the_call_budget_is_a_hard_limit():
    server = FakeServer()
    c = client(server, max_calls=2)
    c.complete("reviewer", "a")
    c.complete("reviewer", "b")
    with pytest.raises(LLMError, match="budget exhausted"):
        c.complete("reviewer", "c")
    assert len(server.requests) == 2


def test_the_api_key_never_appears_in_errors_audit_events_or_recordings(tmp_path):
    events = []
    server = FakeServer(lambda r, p, n: error(500, "api_error", "boom") if r == "developer" else None)
    c = client(server, max_retries=1)
    c.bind_audit(events.append)
    rec = RecordingLLM(c, tmp_path)
    rec.complete("reviewer", "fine")
    with pytest.raises(LLMError) as exc:
        rec.complete("developer", "will fail")
    blob = json.dumps(events) + str(exc.value) + "".join(f.read_text(encoding="utf-8") for f in tmp_path.iterdir())
    assert KEY not in blob and "sk-ant" not in blob


def test_usage_and_audit_events_are_exact_even_with_concurrent_calls():
    server, events = FakeServer(), []
    c = client(server)
    c.bind_audit(events.append)
    threads = [threading.Thread(target=lambda: [c.complete("reviewer", "x" * 40) for _ in range(5)]) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    per_call_in = 40 // 4
    assert c.usage["calls"] == 40 and c.usage["input_tokens"] == 40 * per_call_in
    assert len(events) == 40 and all(e["event"] == "llm_call" and e["request_id"] == "req_fake" for e in events)
    assert all(len(e["prompt_sha256"]) == 16 and "prompt" not in e for e in events)           # a hash, never the prompt


# ----------------------------------------------------------------- output contracts
def test_every_role_the_workflows_use_has_a_contract_and_the_contracts_state_the_gate_rules():
    roles = {"requirements", "planner", "analyst", "architect", "developer", "developer_fallback", "tester", "doc_writer",
             "frontend", "reviewer"}
    assert roles <= set(contracts.CONTRACTS)
    assert "docstring" in contracts.CONTRACTS["developer"] and "eval" in contracts.CONTRACTS["developer"]
    assert "innerHTML" in contracts.CONTRACTS["frontend"] and "bare" in contracts.CONTRACTS["frontend"]
    assert "SAME NAMESPACE" in contracts.CONTRACTS["tester"] and "AC1" in contracts.CONTRACTS["planner"]
    assert contracts.system_for("unknown-role") == contracts.BASE


def test_cleaning_removes_fences_and_preamble_but_never_touches_a_clean_artifact():
    assert contracts.clean_artifact("developer", "Sure! Here it is:\n```python\nx = 1\n```\nHope that helps.") == "x = 1\n"
    assert contracts.clean_artifact("developer", "```\nprint(1)\n```") == "print(1)\n"
    assert contracts.clean_artifact("requirements", 'Here you go: {"goal": "g"} done') == '{"goal": "g"}'
    assert contracts.clean_artifact("planner", '```json\n{"tasks": []}\n```') == '{"tasks": []}'
    assert contracts.clean_artifact("doc_writer", "```markdown\n# Title\n```") == "# Title"
    docs_with_example = "# Docs\n\n```python\nrun()\n```\n"
    assert contracts.clean_artifact("doc_writer", docs_with_example) == docs_with_example        # fences inside docs are content


def test_cleaning_is_the_identity_on_every_stored_reference_artifact():
    decided = {"default_ttl_days": 90, "max_ttl_days": 365, "ttl_applies_to_aliases": "no"}
    samples = []
    for table in (mc.DEFAULTS, shortener.DEFAULTS, bf.DEFAULTS, am.DEFAULTS):
        samples += [(role, v) for role, v in table.items() if isinstance(v, str)]
    samples += [("developer", am.render_code(decided)), ("tester", am.render_tests(decided)), ("architect", am.render_design(decided)),
                ("doc_writer", am.render_changelog(decided)), ("frontend", shortener.GOOD_UI)]
    assert len(samples) > 25
    for role, text in samples:
        assert contracts.clean_artifact(role, text) is text or contracts.clean_artifact(role, text) == text


# ---------------------------------------------------------- feedback to the agent on retries
class Probe:
    def __init__(self):
        self.seen = []

    def __call__(self, ctx):
        self.seen.append(ctx.get("__feedback__"))
        return {"role": "p", "content": f"v{len(self.seen)}", "derived_from": []}


def test_a_retry_tells_the_agent_why_it_was_rejected_and_shows_its_previous_output():
    probe, gate_ctx = Probe(), []

    def gate(ctx, out):
        gate_ctx.append("__feedback__" in ctx)
        return (out["content"] == "v3", f"not good enough: {out['content']}")
    eng = Engine(Graph().add(Node("x", "p", exit_gates=(gate,), max_retries=2)), {"p": probe})
    assert eng.run() is RunStatus.COMPLETED
    assert probe.seen[0] is None
    assert probe.seen[1]["content"] == "not good enough: v1" and probe.seen[1]["previous"] == "v1"
    assert probe.seen[2]["content"] == "not good enough: v2" and probe.seen[2]["previous"] == "v2"
    assert not any(gate_ctx)                                          # gates judge the real context, never the feedback


def test_the_fallback_agent_also_learns_why_the_primary_failed():
    primary, fallback = Probe(), Probe()
    graph = Graph().add(Node("x", "primary", max_retries=0, fallback_agent="fb", exit_gates=(lambda c, o: (o["role"] == "fb", "primary rejected"),)))
    fallback_agent = lambda ctx: {"role": "fb", "content": str(ctx.get("__feedback__")), "derived_from": []}
    eng = Engine(graph, {"primary": primary, "fb": fallback_agent})
    assert eng.run() is RunStatus.COMPLETED
    seen = eng.context["x"]["content"]
    assert "primary rejected" in seen and "'previous': 'v1'" in seen            # why it failed AND what it had produced


def test_the_prompt_carries_the_rejection_and_the_previous_output_on_retry_only():
    server = FakeServer(lambda role, prompt, n: mc.BAD_CODE_SECRET if role == "developer" and "REJECTED" not in prompt else None)
    graph, agents = slugify.build(ac_llm(server))
    r = run_workflow(graph, agents, approvals.auto_approver, run_root=pytest_tmp(), run_id="fb")
    dev = [q for q in server.requests if q["role"] == "developer"]
    assert r["status"] is RunStatus.COMPLETED and len(dev) == 2
    assert "REJECTED" not in dev[0]["prompt"]
    assert "REJECTED by automated checks" in dev[1]["prompt"] and "no-hardcoded-secrets" in dev[1]["prompt"]
    assert "API_KEY" in dev[1]["prompt"]                              # the model sees its own previous output to fix it
    assert r["metrics"]["policy_violations"] == {"security/no-hardcoded-secrets": 1} and r["metrics"]["retries"] == 1


def ac_llm(server, **kw):
    return client(server, **kw)


_TMP = []


def pytest_tmp():
    import tempfile
    d = tempfile.mkdtemp()
    _TMP.append(d)
    return d


# -------------------------------------------------------- whole workflows through the real client
def run_real(tmp_path, workflow=slugify, server=None, **client_kw):
    server = server or FakeServer()
    llm = ac_llm(server, **client_kw)
    graph, agents = workflow.build(llm)
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="real", llm=llm)
    return res, server, llm


def test_a_whole_toy_workflow_runs_through_the_real_client_and_usage_reaches_the_audit_log_and_metrics(tmp_path):
    r, server, llm = run_real(tmp_path)
    assert r["status"] is RunStatus.COMPLETED
    assert sorted(server.roles()) == sorted(["requirements", "architect", "developer", "tester", "doc_writer", "reviewer"])
    m = r["metrics"]
    assert m["llm_calls"] == 6 and m["llm_input_tokens"] == llm.usage["input_tokens"] > 0 and m["llm_output_tokens"] > 0
    audit_text = (r["run_dir"] / "audit.jsonl").read_text(encoding="utf-8")
    assert '"event": "llm_call"' in audit_text and KEY not in audit_text


def test_the_shortener_with_ui_and_plan_runs_through_the_real_client_too(tmp_path):
    server = FakeServer(defaults=shortener.DEFAULTS)
    r, server, _ = run_real(tmp_path, shortener, server)
    assert r["status"] is RunStatus.COMPLETED and {"planner", "frontend", "tester"} <= set(server.roles())


def test_model_answers_wrapped_in_fences_and_prose_still_pass_the_gates(tmp_path):
    def chatty(role, prompt, n):
        base = mc.DEFAULTS[role]
        if role in ("developer", "tester"):
            return f"Here is the {role} artifact:\n```python\n{base}```\nLet me know if you need changes."
        if role == "requirements":
            return f"Sure.\n```json\n{base}\n```"
        return None
    r, server, _ = run_real(tmp_path, server=FakeServer(chatty))
    assert r["status"] is RunStatus.COMPLETED and r["metrics"]["retries"] == 0
    assert r["engine"].context["implementation"]["content"] == mc.DEFAULTS["developer"].rstrip("\n") + "\n"


def test_an_authentication_failure_stops_the_run_at_once_with_one_network_call(tmp_path):
    server = FakeServer(lambda *a: error(401, "authentication_error", "invalid x-api-key"))
    r, server, _ = run_real(tmp_path, server=server)
    assert r["status"] is RunStatus.HALTED and len(server.requests) == 1
    assert "ANTHROPIC_API_KEY" in r["metrics"]["halt_reason"]


def test_the_call_budget_stops_a_run_cleanly(tmp_path):
    r, server, _ = run_real(tmp_path, max_calls=3)
    assert r["status"] is RunStatus.HALTED and len(server.requests) == 3
    assert "budget exhausted" in r["metrics"]["halt_reason"]


# -------------------------------------------------------------- hybrid, recording, CLI
def test_hybrid_routing_sends_only_the_chosen_roles_to_the_real_model(tmp_path):
    server = FakeServer()
    real = client(server)
    llm = RoutingLLM(slugify.make_llm(), {r: real for r in ("requirements", "doc_writer")})
    graph, agents = slugify.build(llm)
    res = run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path, run_id="h", llm=llm)
    assert res["status"] is RunStatus.COMPLETED and sorted(server.roles()) == ["doc_writer", "requirements"]
    assert llm.usage["calls"] == 2 and res["metrics"]["llm_calls"] == 2


def test_recording_saves_system_prompt_prompt_and_response_of_every_call(tmp_path):
    llm = RecordingLLM(slugify.make_llm(), tmp_path / "recordings")
    graph, agents = slugify.build(llm, "Write slugify exactly my way.")
    run_workflow(graph, agents, approvals.auto_approver, run_root=tmp_path / "runs", run_id="r1")
    files = sorted((tmp_path / "recordings").iterdir())
    assert len(files) == 6 and files[0].name == "001-requirements.json"
    first = json.loads(files[0].read_text(encoding="utf-8"))
    assert "Write slugify exactly my way." in first["prompt"] and first["system"] == contracts.system_for("requirements")
    assert first["response"] == mc.DEFAULTS["requirements"]
    dev = json.loads(next(f for f in files if "developer" in f.name).read_text(encoding="utf-8"))
    assert "original request" in dev["prompt"] and "exactly my way" in dev["prompt"]          # exact wording reaches the developer


def cli_env(server):
    class Ctx:
        def __enter__(self):
            self.old_t, self.old_k = ac.TRANSPORT_OVERRIDE, os.environ.get("ANTHROPIC_API_KEY")
            ac.TRANSPORT_OVERRIDE, os.environ["ANTHROPIC_API_KEY"] = server, KEY

        def __exit__(self, *exc):
            ac.TRANSPORT_OVERRIDE = self.old_t
            if self.old_k is None:
                os.environ.pop("ANTHROPIC_API_KEY", None)
            else:
                os.environ["ANTHROPIC_API_KEY"] = self.old_k
    return Ctx()


def test_cli_runs_a_workflow_on_the_real_client_with_a_requirement_file_and_recording(tmp_path):
    req = tmp_path / "req.md"
    req.write_text("Write slugify(text) that also handles émojis and keeps digits. Plain English, my words.", encoding="utf-8")
    server = FakeServer()
    with cli_env(server):
        code = cli.main(["run", "slugify", "--llm", "anthropic", "--model", "m-1", "--requirement-file", str(req), "--approve", "auto",
                         "--record", str(tmp_path / "rec"), "--run-root", str(tmp_path / "runs")])
    assert code == 0 and len(server.requests) == 6 and server.requests[0]["payload"]["model"] == "m-1"
    assert "my words" in server.requests[0]["prompt"] and "my words" in next(r for r in server.requests if r["role"] == "developer")["prompt"]
    assert len(list((tmp_path / "rec").iterdir())) == 6


def test_cli_hybrid_mode_and_the_budget_flag(tmp_path):
    server = FakeServer()
    with cli_env(server):
        assert cli.main(["run", "slugify", "--llm", "anthropic", "--llm-roles", "architect,reviewer", "--approve", "auto",
                         "--run-root", str(tmp_path / "a")]) == 0
        assert sorted(server.roles()) == ["architect", "reviewer"]
        assert cli.main(["run", "slugify", "--llm", "anthropic", "--max-llm-calls", "1", "--approve", "auto",
                         "--run-root", str(tmp_path / "b")]) == 1


def test_cli_without_a_key_fails_cleanly_and_unsafe_flag_combinations_are_refused(tmp_path):
    saved = os.environ.pop("ANTHROPIC_API_KEY", None)
    try:
        assert cli.main(["run", "slugify", "--llm", "anthropic", "--approve", "auto", "--run-root", str(tmp_path)]) == 2
    finally:
        if saved:
            os.environ["ANTHROPIC_API_KEY"] = saved
    for bad in (["--llm", "anthropic", "--violate", "secret"], ["--llm", "anthropic", "--fail", "developer:1"], ["--llm-roles", "architect"]):
        with pytest.raises(SystemExit):
            cli.main(["run", "slugify", "--approve", "auto", "--run-root", str(tmp_path)] + bad)


def test_the_mock_mode_says_plainly_that_it_ignores_a_custom_requirement(tmp_path, capsys=None):
    import io, contextlib
    buf = io.StringIO()
    with contextlib.redirect_stdout(buf):
        assert cli.main(["run", "slugify", "--requirement", "Something else entirely.", "--approve", "auto", "--run-root", str(tmp_path)]) == 0
    assert "mock agents return stored artifacts and ignore the requirement text" in buf.getvalue()

"""Change-control size limits: the model is told them up front, the operator can set them per run, and the settings are audited.

Found on the first live run with a real model: the developer agent wrote a 687-line service, the 600-line limit rejected it, and
the model had never been told the limit. Limits are now stated in the prompts, configurable, and recorded in the audit log.
"""
import contextlib
import io
import json

import pytest

from orchestrator import cli
from orchestrator.governance import gates as g
from orchestrator.workflows import shortener, slugify
from tests.test_admin_api import Admin, with_clean_env


def padded(lines: int) -> str:
    """The reference service padded with comment lines to an exact length (what a wordier model's output looks like to the gate)."""
    body = shortener.GOOD_CODE.rstrip("\n").splitlines()
    return "\n".join(["# padding"] * (lines - len(body)) + body) + "\n"


def test_the_defaults_accept_the_687_line_service_that_the_old_limit_rejected():
    gate = g.change_control_gate("shortener.py", max_lines=shortener.MAX_LINES)
    assert shortener.MAX_LINES == 900 and shortener.MAX_UI_LINES == 700
    assert len(padded(687).splitlines()) == 687
    assert gate({}, {"content": padded(687)})[0] is True                       # the reported case
    old = g.change_control_gate("shortener.py", max_lines=600)
    ok, why = old({}, {"content": padded(687)})
    assert ok is False and "687 lines changed; limit is 600" in why             # exactly the message from the live run
    ok, why = gate({}, {"content": padded(901)})
    assert ok is False and "limit is 900" in why                                # the limit still bites


class Recorder:
    """A fake model that records every prompt and answers with the stored reference artifacts."""

    def __init__(self):
        self.inner = shortener.make_llm()
        self.prompts = {}

    def complete(self, role, prompt):
        self.prompts.setdefault(role, []).append(prompt)
        return self.inner.complete(role, prompt)


def test_the_model_is_told_its_size_limits_up_front():
    rec = Recorder()
    graph, agents = shortener.build(rec, max_lines=800, max_ui_lines=650)
    ctx = {"requirements": {"content": "{}"}, "design": {"content": "design"}, "implementation": {"content": "code"}}
    for role in ("developer", "developer_fallback"):
        agents[role](ctx)
    agents["frontend"](ctx)
    assert "longer than 800 lines" in rec.prompts["developer"][0] and "about 640" in rec.prompts["developer"][0]
    assert "longer than 800 lines" in rec.prompts["developer_fallback"][0]
    assert "longer than 650 lines" in rec.prompts["frontend"][0] and "about 520" in rec.prompts["frontend"][0]
    assert shortener.SIZE_LIMITS == {"max_lines": 900, "max_ui_lines": 700} and slugify.SIZE_LIMITS == {"max_lines": 200}


def run_cli(tmp_path, *args):
    code = cli.main(["run", *args, "--approve", "auto", "--run-root", str(tmp_path)])
    run = sorted(p for p in tmp_path.iterdir() if p.is_dir())[-1]
    events = [json.loads(line) for line in (run / "audit.jsonl").read_text(encoding="utf-8").splitlines()]
    return code, events


def test_a_low_limit_from_the_command_line_halts_with_the_message_the_operator_would_see(tmp_path):
    code, events = run_cli(tmp_path, "shortener", "--max-lines-changed", "80")
    assert code == 1
    failed = [e for e in events if e["event"] == "attempt_failed"]
    assert failed and all("[change_control/max-lines-changed]" in e["error"] and "limit is 80" in e["error"] for e in failed)
    config = events[0]
    assert config["event"] == "run_config" and config["config"]["limits"] == {"max_lines": 80, "max_ui_lines": 700}       # recorded first


def test_the_ui_limit_is_enforced_separately_from_the_code_limit(tmp_path):
    code, events = run_cli(tmp_path, "shortener", "--max-ui-lines", "100")
    assert code == 1
    failed = [e for e in events if e["event"] == "attempt_failed"]
    assert failed and {e["node"] for e in failed} == {"ui"} and all("limit is 100" in e["error"] for e in failed)    # the code stage was fine


def test_a_raised_limit_is_recorded_and_the_run_completes(tmp_path):
    code, events = run_cli(tmp_path, "shortener", "--max-lines-changed", "1000", "--max-ui-lines", "800")
    assert code == 0
    assert events[0]["config"]["limits"] == {"max_lines": 1000, "max_ui_lines": 800}
    assert events[0]["config"]["llm"] == "mock" and "key" not in json.dumps(events[0]).lower()


def test_limits_that_do_not_apply_or_are_out_of_range_are_refused(tmp_path):
    captured = io.StringIO()
    with contextlib.redirect_stderr(captured):
        for args in (["brownfield", "--max-lines-changed", "80"], ["ambiguous", "--max-ui-lines", "80"], ["slugify", "--max-ui-lines", "300"],
                     ["shortener", "--max-lines-changed", "10"], ["shortener", "--max-lines-changed", "99999"]):
            with pytest.raises(SystemExit):
                cli.main(["run", *args, "--approve", "auto", "--run-root", str(tmp_path)])
    err = captured.getvalue()
    assert "does not apply to 'brownfield'" in err and "between 50 and 5000" in err


# ------------------------------------------------------------------------------------------------ admin page
@with_clean_env
def test_the_admin_page_lists_each_scenarios_default_limits(tmp_path):
    a = Admin(tmp_path)
    scenarios = {s["id"]: s for s in a.call("GET", "/api/config")[2]["scenarios"]}
    assert scenarios["shortener"]["limits"] == {"max_lines": 900, "max_ui_lines": 700}
    assert scenarios["slugify"]["limits"] == {"max_lines": 200} and scenarios["ambiguous"]["limits"] == {}


@with_clean_env
def test_a_limit_set_on_the_page_is_enforced_recorded_and_explained(tmp_path):
    a = Admin(tmp_path)
    run_id = a.run(scenario="shortener", requirement="Build a shortener.", limits={"max_lines": 80})
    s = a.finish()
    assert s["run"]["status"] == "halted" and "limit is 80" in s["run"]["halt_reason"] and s["run"]["limits"] == {"max_lines": 80, "max_ui_lines": 700}
    assert any("size limit" in n.lower() and "Size limits" in n for n in s["notes"])                      # tells the operator what to do
    assert any(t["text"].startswith("Settings recorded") and "max lines 80" in t["text"] for t in s["timeline"])
    run = tmp_path / "runs" / run_id
    assert json.loads((run / "run.json").read_text(encoding="utf-8"))["limits"] == {"max_lines": 80, "max_ui_lines": 700}
    first = json.loads((run / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["event"] == "run_config" and first["config"]["limits"]["max_lines"] == 80 and first["config"]["mode"] == "mock"
    assert "requirement" not in first["config"] and first["config"]["requirement_chars"] == len("Build a shortener.")


@with_clean_env
def test_the_page_can_raise_a_limit_and_the_run_completes_with_defaults_otherwise(tmp_path):
    a = Admin(tmp_path)
    a.run(scenario="shortener", requirement="Build a shortener.", limits={"max_lines": 1200, "max_ui_lines": 900})
    s = a.finish()
    assert s["run"]["status"] == "completed" and s["run"]["limits"] == {"max_lines": 1200, "max_ui_lines": 900} and s["notes"] != []   # (the mock note)
    b = Admin(tmp_path / "second")
    b.run(scenario="shortener", requirement="Build a shortener.")
    assert b.finish()["run"]["limits"] == {"max_lines": 900, "max_ui_lines": 700}


@with_clean_env
def test_the_page_refuses_limits_that_do_not_apply_or_make_no_sense(tmp_path):
    a = Admin(tmp_path)
    ok = {"scenario": "shortener", "requirement": "x", "mode": "mock", "auto_approve": True}
    for limits, needle in [({"max_lines": 10}, "50 to 5000"), ({"max_lines": 99999}, "50 to 5000"), ({"max_lines": "900"}, "whole number"),
                           ({"max_lines": True}, "whole number"), ({"max_lines": 900.5}, "whole number"), ({"colour": 3}, "does not accept"),
                           ("900", "must be an object")]:
        status, _, res = a.call("POST", "/api/runs", {**ok, "limits": limits})
        assert status == 400 and needle in res["error"]["message"], (limits, res)
    status, _, res = a.call("POST", "/api/runs", {**ok, "scenario": "ambiguous", "limits": {"max_lines": 100}})
    assert status == 400 and "does not accept" in res["error"]["message"]
    status, _, res = a.call("POST", "/api/runs", {**ok, "scenario": "slugify", "limits": {"max_ui_lines": 100}})
    assert status == 400 and "(it accepts: max_lines)" in res["error"]["message"]
    assert a.state.session is None


@with_clean_env
def test_the_recorded_settings_never_contain_the_api_key(tmp_path):
    from orchestrator.llm import anthropic_client as ac
    from tests.test_real_llm import KEY, FakeServer
    a = Admin(tmp_path)
    ac.TRANSPORT_OVERRIDE = FakeServer(defaults=shortener.DEFAULTS)
    a.call("POST", "/api/key", {"key": KEY})
    run_id = a.run(scenario="shortener", requirement="Build a shortener.", mode="real", confirm_real=True, model="test-model", limits={"max_lines": 1000})
    s = a.finish()
    assert s["run"]["status"] == "completed"
    first = json.loads((tmp_path / "runs" / run_id / "audit.jsonl").read_text(encoding="utf-8").splitlines()[0])
    assert first["config"]["mode"] == "real" and first["config"]["model"] == "test-model" and first["config"]["max_calls"] == 40
    assert KEY not in json.dumps(first) and any("real model test-model" in t["text"] for t in s["timeline"])

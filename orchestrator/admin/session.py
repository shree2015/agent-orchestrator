"""Admin backend: one active run at a time, human decisions made through the page, live snapshots.

A run executes the SAME `run_workflow` as the command line, in a background thread. The engine's approval hook and
the clarifying-question hook simply *wait* for the page to answer (a pending decision), so all human intervention
happens through the admin page. The API key lives only in memory (or in the environment); it is never written to
disk, logs, recordings or any response.
"""
from __future__ import annotations

import inspect
import io
import json
import os
import re
import threading
import time
import uuid
import zipfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from ..core.state import RunStatus
from ..governance.policies import GovernanceError
from ..llm import anthropic_client, contracts
from ..llm.base import LLMError
from ..llm.routing import RecordingLLM, RoutingLLM
from ..observability import metrics as metrics_mod
from ..observability.audit_log import AuditLog
from ..runner import run_workflow, write_outputs
from . import scenarios
from .service import ServiceError, ServiceManager, is_runnable, why_not_runnable
from .security import valid_api_key_shape

MAX_REQUIREMENT_CHARS = 20_000
MAX_SHOWN_CHARS = 12_000
MAX_ARTIFACT_BYTES = 400_000
RUN_ID = re.compile(r"[A-Za-z0-9._-]{1,80}")
MODEL = re.compile(r"[A-Za-z0-9._:-]{1,80}")


class AdminError(Exception):
    def __init__(self, status: int, message: str, code: str = "bad_request") -> None:
        super().__init__(message)
        self.status, self.code = status, code


@dataclass
class Pending:
    id: str
    kind: str                       # "approval" | "question"
    node: str
    payload: dict
    event: threading.Event = field(default_factory=threading.Event)
    result: Any = None


def _short(text: Any, n: int = 220) -> str:
    text = " ".join(str(text).split())
    return text if len(text) <= n else text[: n - 1] + "…"


def describe(e: dict) -> str | None:
    """One human sentence per audit event worth showing on the page (None = not shown)."""
    k = e["event"]
    if k == "run_started":
        return "Run started"
    if k == "wave_started":
        return "Running: " + ", ".join(e.get("nodes", []))
    if k == "node_committed":
        return f"{e['node']} done (by {e.get('agent')}){' - fallback agent' if e.get('fallback') else ''}"
    if k == "attempt_failed":
        return (f"{e['node']}: attempt {e['attempt']} ({e.get('stage')}) rejected: {_short(e.get('error'))}"
                + (" - retrying" if e.get("will_retry") else ""))
    if k == "fallback_engaged":
        return f"{e['node']}: retries exhausted, switching to fallback agent {e.get('fallback_agent')}"
    if k == "approval":
        return f"{e['node']}: {'approved' if e.get('approved') else 'REJECTED'} by a human"
    if k == "entry_gate" and not e.get("passed"):
        return f"{e['node']}: blocked at entry: {_short(e.get('reason'))}"
    if k == "node_failed":
        return f"{e['node']} FAILED: {_short(e.get('reason'))}"
    if k == "safe_stop":
        return f"SAFE-STOP: {_short(e.get('reason'))}"
    if k == "checkpoint":
        return "Checkpoint saved for " + ", ".join(e.get("nodes", []))
    if k == "replan":
        return f"Re-plan ({'automatic' if e.get('auto') else 'forced'}): redo {', '.join(e.get('invalidated', []))}"
    if k == "rollback":
        return "ROLLBACK: restored " + ", ".join(e.get("restored", []))
    if k == "change_committed":
        return "Change committed"
    if k in ("change_rejected", "revision_rejected"):
        return f"Change refused: {_short(e.get('why'))}"
    if k == "revision":
        return f"{e['node']} revised by {e.get('by')}: {_short(e.get('reason'))}"
    if k == "revision_noop":
        return f"{e['node']}: identical answer, nothing to redo"
    if k == "rework":
        if e.get("granted"):
            return (f"Review sent the work back (round {e.get('round')} of {e.get('of')}): {', '.join(e.get('redo', []))} will be redone. "
                    f"Findings: {_short(e.get('findings'))}")
        return f"Review rejected the work and the rework budget is spent ({e.get('of')}): {_short(e.get('findings'))}"
    if k == "run_config":
        c = e.get("config") or {}
        lim = ", ".join(f"{n.replace('_', ' ')} {v}" for n, v in (c.get("limits") or {}).items())
        real = c.get("mode") == "real" or c.get("llm") == "anthropic"
        return "Settings recorded: " + (f"real model {c.get('model')}" if real else "mock agents") + (f"; size limits: {lim}" if lim else "")
    if k == "policy_lint" and not e.get("passed", True):
        return "Workflow rejected by governance lint"
    if k == "llm_call":
        return f"model call: {e.get('role')} ({e.get('input_tokens')}→{e.get('output_tokens')} tokens, {e.get('latency_s')}s)"
    if k == "run_finished":
        return f"Run finished: {e.get('status')}"
    return None


class RunSession:
    def __init__(self, state: "AdminState", cfg: dict, api_key: str | None) -> None:
        self.state, self.cfg, self._api_key = state, cfg, api_key
        self.run_id = f"run-{time.strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
        self.run_dir = state.run_root / self.run_id
        self.wf = scenarios.SCENARIOS[cfg["scenario"]]["workflow"]
        self.engine = self.audit = self.llm = self.graph = None
        self.pending: dict[str, Pending] = {}
        self.stopped = False
        self.error: str | None = None
        self.last_status: RunStatus | None = None
        self.started_at, self.finished_at = time.time(), None
        self._lock = threading.Lock()
        self._thread: threading.Thread | None = None

    # ----------------------------------------------------------------------------- lifecycle
    @property
    def busy(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def begin(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True, name=f"admin-{self.run_id}")
        self._thread.start()

    def _run(self) -> None:
        cfg = self.cfg
        try:
            llm = self.wf.make_llm()
            if cfg["mode"] == "real":
                client = anthropic_client.AnthropicClient(model=cfg["model"], api_key=self._api_key, max_tokens=cfg["max_tokens"],
                                                          max_calls=cfg["max_calls"])
                llm = client if not cfg["roles"] else RoutingLLM(llm, {r: client for r in cfg["roles"]})
            self._api_key = None                                   # the client holds it now; do not keep a second copy
            if cfg["record"]:
                llm = RecordingLLM(llm, self.run_dir / "llm-records")
            self.llm = llm
            extra: dict = {"requirement": cfg["requirement"], **cfg["raised_limits"]}
            if "asker" in inspect.signature(self.wf.build).parameters:
                extra["asker"] = self.asker
            graph, agents = self.wf.build(llm, **extra)
            self.graph = graph
            res = run_workflow(graph, agents, self.approver, run_root=self.state.run_root, run_id=self.run_id,
                               assets=getattr(self.wf, "ASSETS", None), llm=llm, on_start=self._on_start, run_config=self.public_config())
            self.last_status = res["status"]
        except GovernanceError as exc:
            self.error = f"workflow rejected by governance lint: {exc}"
        except LLMError as exc:
            self.error = f"model setup failed: {exc}"
        except Exception as exc:                                    # never let a worker die silently
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.finished_at = time.time()

    def public_config(self) -> dict:
        """The settings recorded in the audit log and run.json: everything except secrets and the requirement text itself."""
        c = self.cfg
        return {"scenario": c["scenario"], "mode": c["mode"], "model": c.get("model"), "roles": c["roles"], "auto_approve": c["auto_approve"],
                "max_calls": c["max_calls"] if c["mode"] == "real" else None, "max_tokens": c["max_tokens"] if c["mode"] == "real" else None,
                "limits": c["limits"], "max_reworks": 2, "requirement_chars": len(c["requirement"])}

    def _on_start(self, engine, audit, run_dir) -> None:
        self.engine, self.audit = engine, audit
        meta = {"run_id": self.run_id, "scenario": self.cfg["scenario"], "mode": self.cfg["mode"], "model": self.cfg.get("model"),
                "roles": self.cfg["roles"], "auto_approve": self.cfg["auto_approve"], "limits": self.cfg["limits"], "requirement": self.cfg["requirement"],
                "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(self.started_at))}
        (run_dir / "run.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
        if self.stopped:
            engine.stop()

    # ----------------------------------------------------------------- human decisions (blocking hooks)
    def _wait(self, p: Pending) -> None:
        with self._lock:
            self.pending[p.id] = p
        p.event.wait()
        with self._lock:
            self.pending.pop(p.id, None)

    def approver(self, node, output) -> bool:
        if self.cfg["auto_approve"]:
            return True
        if self.stopped:
            return False
        content = output["content"] if isinstance(output, dict) and "content" in output else str(output)
        p = Pending(uuid.uuid4().hex[:10], "approval", node.id,
                    {"title": f"Approve '{node.id}'?", "description": node.description, "size": len(content),
                     "content": content[:MAX_SHOWN_CHARS], "truncated": len(content) > MAX_SHOWN_CHARS})
        self._wait(p)
        return bool(p.result) and not self.stopped

    def asker(self, item: dict):
        if self.stopped:
            return None
        if self.cfg["auto_approve"] and item.get("risk") != "high":
            return None                                   # accept the proposed default; high-risk always needs a person
        payload = {**item, "question_id": item.get("id")}
        payload.pop("id", None)                            # the pending item's own id must never be shadowed by the question's
        p = Pending(uuid.uuid4().hex[:10], "question", "clarifications", payload)
        self._wait(p)
        return None if self.stopped else p.result

    def resolve(self, pending_id: str, body: dict) -> None:
        with self._lock:
            p = self.pending.get(pending_id)
            if p is None or p.event.is_set():
                raise AdminError(404, "that decision is no longer waiting", "not_found")
        if p.kind == "approval":
            if not isinstance(body.get("approve"), bool):
                raise AdminError(400, "send {\"approve\": true} or {\"approve\": false}")
            p.result = body["approve"]
        elif body.get("skip") is True:
            if p.payload.get("risk") == "high":
                raise AdminError(400, "this question is high-risk: it needs an answer from you, it cannot be assumed")
            p.result = None
        else:
            answer, options = body.get("answer"), p.payload.get("options") or []
            if not isinstance(answer, (str, int)) or isinstance(answer, bool) or not str(answer).strip():
                raise AdminError(400, "send {\"answer\": ...}")
            match = next((o for o in options if str(o) == str(answer)), None)
            if options and match is None:
                raise AdminError(400, f"the answer must be one of {options}")
            p.result = match if options else str(answer).strip()
        with self._lock:
            self.pending.pop(p.id, None)           # gone from the page immediately, not when the worker wakes up
        p.event.set()

    def stop(self) -> None:
        """Safe-stop: nothing new starts, waiting decisions are released, work in flight finishes."""
        self.stopped = True
        if self.engine is not None:
            self.engine.stop("stopped by the operator")
        with self._lock:
            waiting = list(self.pending.values())
            self.pending.clear()
        for p in waiting:
            p.result = False if p.kind == "approval" else None
            p.event.set()

    # -------------------------------------------------------------------------------- revising an answer
    def revisable(self) -> list[dict]:
        if (not scenarios.SCENARIOS[self.cfg["scenario"]]["asks_questions"] or self.busy or self.engine is None
                or self.last_status is not RunStatus.COMPLETED or "clarifications" not in self.engine.context):
            return []
        qs = {q["id"]: q for q in json.loads(self.engine.context["requirements"]["content"]).get("ambiguities", []) if isinstance(q, dict)}
        out = []
        for d in json.loads(self.engine.context["clarifications"]["content"])["decisions"]:
            q = qs.get(d["id"], {})
            out.append({"id": d["id"], "question": q.get("question", d["id"]), "options": q.get("options", []),
                        "answer": d.get("answer"), "decided_by": d.get("decided_by")})
        return out

    def revise(self, decision_id: str, value: Any) -> dict:
        decisions = {d["id"]: d for d in self.revisable()}
        if self.busy:
            raise AdminError(409, "the run is still working")
        if decision_id not in decisions:
            raise AdminError(400, "there is no such decision to change (or the run did not complete)")
        d = decisions[decision_id]
        match = next((o for o in d["options"] if str(o) == str(value)), None)
        if d["options"] and match is None:
            raise AdminError(400, f"the answer must be one of {d['options']}")
        content = self.wf.answer_edit(decision_id, match if match is not None else value)(self.engine)
        rev = self.engine.revise("clarifications", content, f"human changed {decision_id} to {value} (admin page)", "human")
        if not rev.accepted:
            return {"accepted": False, "changed": False, "why": rev.why}
        if rev.changed:
            self.stopped = False
            self.finished_at = None
            self._thread = threading.Thread(target=self._rerun, daemon=True, name=f"admin-rerun-{self.run_id}")
            self._thread.start()
        else:
            write_outputs(self.run_dir, self.run_id, self.engine, self.audit, getattr(self.wf, "ASSETS", None))
        return {"accepted": True, "changed": rev.changed, "affected": sorted(rev.affected), "why": rev.why}

    def _rerun(self) -> None:
        try:
            self.last_status = self.engine.run()
            write_outputs(self.run_dir, self.run_id, self.engine, self.audit, getattr(self.wf, "ASSETS", None))
        except Exception as exc:
            self.error = f"{type(exc).__name__}: {exc}"
        finally:
            self.finished_at = time.time()

    # ------------------------------------------------------------------------------------------ snapshot
    @property
    def status(self) -> str:
        if self.error:
            return "error"
        if self.busy:
            return "waiting" if any(not p.event.is_set() for p in list(self.pending.values())) else ("running" if self.engine else "starting")
        if self.stopped and self.last_status is not RunStatus.COMPLETED:
            return "stopped"
        return (self.last_status.value.lower() if self.last_status else "starting")

    def snapshot(self) -> dict:
        events = list(self.audit.events) if self.audit else []
        nodes = []
        if self.engine is not None:
            state = dict(self.engine.state)
            for nid in self.engine.graph.topological_order():
                node = self.engine.graph.nodes[nid]
                err = next((e.get("error") for e in reversed(events) if e["event"] == "attempt_failed" and e.get("node") == nid), None)
                nodes.append({"id": nid, "state": state[nid].value, "attempts": self.engine.attempts.get(nid, 0),
                              "producer": self.engine.producer.get(nid), "depends_on": list(node.depends_on),
                              "description": node.description, "approval": node.requires_approval,
                              "last_error": _short(err) if err and state[nid].value not in ("DONE",) else None})
        timeline = [{"seq": e["seq"], "text": t} for e in events if (t := describe(e))][-150:]
        usage = getattr(self.llm, "usage", None) if self.llm is not None else None
        notes = []
        halt = self.engine.halt_reason if self.engine else None
        if halt and "[change_control/max-lines-changed]" in halt:
            notes.append("A size limit (change control) stopped this run. If that size is reasonable for what you asked for, raise the limit under "
                         "\"Size limits\" and generate again; the new value is recorded in the audit log. Otherwise ask for something smaller.")
        if self.engine is not None and not self.busy and scenarios.SCENARIOS[self.cfg["scenario"]].get("service") and self.last_status is RunStatus.COMPLETED:
            impl = self.run_dir / "artifacts" / "implementation.py"
            reason = why_not_runnable(impl.read_text(encoding="utf-8", errors="replace")) if impl.is_file() else None
            if reason:
                notes.append("This run finished, but the page cannot offer to start it as a service: " + reason + ". The code is in the results; "
                             "you can still run it yourself with `python implementation.py 8000`.")
        if self.engine is not None and self.engine.rejected and not self.busy:
            notes.append("The last rejected output of the stage that stopped this run is saved in the results as REJECTED-<stage>: open it to see exactly what was refused.")
        if self.cfg["mode"] == "mock" and self.cfg["requirement"].strip() != scenarios.default_requirement(self.cfg["scenario"]).strip():
            notes.append("Mock agents return stored artifacts and ignore the requirement text. Switch to Real to see your wording drive the output.")
        done = not self.busy and self.engine is not None
        arts = sorted(p.name for p in (self.run_dir / "artifacts").iterdir()) if done and (self.run_dir / "artifacts").exists() else []
        return {
            "run": {"id": self.run_id, "scenario": self.cfg["scenario"], "mode": self.cfg["mode"], "model": self.cfg.get("model"),
                    "status": self.status, "halt_reason": self.engine.halt_reason if self.engine else None, "error": self.error,
                    "auto_approve": self.cfg["auto_approve"], "busy": self.busy, "limits": self.cfg["limits"],
                    "runnable": (not self.busy) and self.state.runnable(self.run_id)},
            "nodes": nodes, "timeline": timeline, "metrics": metrics_mod.compute(events) if events else {},
            "llm_usage": usage, "notes": notes, "artifacts": arts, "can_revise": bool(self.revisable()),
            "decisions": self.revisable(),
            "pending": [{**p.payload, "id": p.id, "kind": p.kind, "node": p.node} for p in list(self.pending.values()) if not p.event.is_set()],
        }


class AdminState:
    def __init__(self, run_root: str | Path = "runs") -> None:
        self.run_root = Path(run_root).resolve()        # absolute, so it means the same thing in every process and folder
        self._key: str | None = None
        self.session: RunSession | None = None
        self.services = ServiceManager()
        self._lock = threading.Lock()

    def shutdown(self, wait_s: float = 5.0) -> None:
        """Stop any active run and release waiting decisions so the process can exit (Ctrl+C with a pending approval)."""
        self.services.stop()
        s = self.session
        if s is not None and s.busy:
            s.stop()
            if s._thread is not None:
                s._thread.join(wait_s)

    # ----------------------------------------------------------------------------------------------- key
    def key_status(self) -> str:
        return "session" if self._key else ("env" if os.environ.get("ANTHROPIC_API_KEY") else "none")

    def set_key(self, key: Any) -> None:
        if not isinstance(key, str) or not valid_api_key_shape(key.strip()):
            raise AdminError(400, "that does not look like an API key (20-300 visible characters, no spaces)")
        self._key = key.strip()

    def clear_key(self) -> None:
        self._key = None

    # ------------------------------------------------------------------------------------------- starting
    def _validate(self, p: dict) -> dict:
        if p.get("scenario") not in scenarios.SCENARIOS:
            raise AdminError(400, f"scenario must be one of {sorted(scenarios.SCENARIOS)}")
        req = p.get("requirement")
        if not isinstance(req, str) or not req.strip():
            raise AdminError(400, "write a requirement first")
        if len(req) > MAX_REQUIREMENT_CHARS:
            raise AdminError(400, f"the requirement is too long ({len(req)} characters; limit {MAX_REQUIREMENT_CHARS})")
        mode = p.get("mode", "mock")
        if mode not in ("mock", "real"):
            raise AdminError(400, "mode must be mock or real")
        cfg = {"scenario": p["scenario"], "requirement": req.strip(), "mode": mode, "model": None, "roles": [],
               "max_calls": 40, "max_tokens": 16000, "auto_approve": bool(p.get("auto_approve", False)),
               "record": bool(p.get("record", mode == "real"))}
        supported = getattr(scenarios.SCENARIOS[p["scenario"]]["workflow"], "SIZE_LIMITS", {})
        raw_limits = p.get("limits") or {}
        if not isinstance(raw_limits, dict):
            raise AdminError(400, "limits must be an object such as {\"max_lines\": 900}")
        limits = {}
        for key, value in raw_limits.items():
            if key not in supported:
                raise AdminError(400, f"this scenario does not accept the size limit {key!r}" + (f" (it accepts: {', '.join(supported)})" if supported else ""))
            if not isinstance(value, int) or isinstance(value, bool) or not 50 <= value <= 5000:
                raise AdminError(400, f"the size limit {key} must be a whole number of lines from 50 to 5000")
            limits[key] = value
        cfg["limits"] = {**supported, **limits}                   # the limits in force: the defaults, with the operator's changes on top
        cfg["raised_limits"] = limits
        if mode == "real":
            if not (self._key or os.environ.get("ANTHROPIC_API_KEY")):
                raise AdminError(400, "no API key: set ANTHROPIC_API_KEY in the terminal that started this page, or paste a key here (kept in memory only)")
            if p.get("confirm_real") is not True:
                raise AdminError(400, "confirm that your requirement and the generated code may be sent to the API and may cost money")
            model = p.get("model") or anthropic_client.DEFAULT_MODEL
            if not isinstance(model, str) or not MODEL.fullmatch(model):
                raise AdminError(400, "invalid model name")
            calls, tokens = p.get("max_calls", 40), p.get("max_tokens", 16000)
            if not isinstance(calls, int) or isinstance(calls, bool) or not 1 <= calls <= 200:
                raise AdminError(400, "call budget must be a whole number from 1 to 200")
            if not isinstance(tokens, int) or isinstance(tokens, bool) or not 1000 <= tokens <= 64000:
                raise AdminError(400, "output limit must be a whole number from 1000 to 64000")
            roles = p.get("roles") or []
            if not isinstance(roles, list) or any(r not in contracts.CONTRACTS for r in roles):
                raise AdminError(400, f"roles must be a list of: {sorted(contracts.CONTRACTS)}")
            cfg.update(model=model, max_calls=calls, max_tokens=tokens, roles=roles)
        return cfg

    def start(self, params: dict) -> RunSession:
        with self._lock:
            if self.session is not None and self.session.busy:
                raise AdminError(409, "a run is already in progress: wait for it, or stop it", "busy")
            cfg = self._validate(params)
            session = RunSession(self, cfg, self._key or os.environ.get("ANTHROPIC_API_KEY") if cfg["mode"] == "real" else None)
            self.session = session
        session.begin()
        return session

    # --------------------------------------------------------------------------------------------- history
    def _dir(self, run_id: str) -> Path:
        if not isinstance(run_id, str) or not RUN_ID.fullmatch(run_id):
            raise AdminError(400, "invalid run id")
        d = self.run_root / run_id
        if not d.is_dir():
            raise AdminError(404, "no such run", "not_found")
        return d

    # --------------------------------------------------------------------------- running a generated service
    def runnable(self, run_id: str) -> bool:
        try:
            impl = self._dir(run_id) / "artifacts" / "implementation.py"
            return impl.is_file() and is_runnable(impl.read_text(encoding="utf-8", errors="replace"))
        except AdminError:
            return False

    def start_service(self, params: dict) -> dict:
        run_id = params.get("run_id")
        d = self._dir(run_id)
        if self.session is not None and self.session.run_id == run_id and self.session.busy:
            raise AdminError(409, "that run is still working: wait for it to finish")
        if not self.runnable(run_id):
            raise AdminError(400, "this run did not produce a runnable service")
        mode = self._read_json(d / "run.json").get("mode", "unknown")
        if mode != "mock" and params.get("confirm_unreviewed") is not True:
            raise AdminError(400, "this code was not produced by the reviewed mock agents (mode: " + str(mode) + "). It will run on your computer "
                             "with your normal permissions: confirm that you want to start it")
        try:
            return self.services.start(d, run_id)
        except ServiceError as exc:
            raise AdminError(exc.status, str(exc)) from None

    def list_runs(self, limit: int = 50) -> list[dict]:
        if not self.run_root.is_dir():
            return []
        out = []
        for d in sorted((p for p in self.run_root.iterdir() if p.is_dir() and (p / "audit.jsonl").exists()), reverse=True)[:limit]:
            meta = self._read_json(d / "run.json")
            m = self._read_json(d / "metrics.json")
            out.append({"run_id": d.name, "scenario": meta.get("scenario", "(command line)"), "mode": meta.get("mode", "-"),
                        "started_at": meta.get("started_at", ""), "status": (m.get("run_status") or "incomplete").lower() if m else "incomplete",
                        "llm_calls": m.get("llm_calls", 0) if m else 0})
        return out

    @staticmethod
    def _read_json(path: Path) -> dict:
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return {}

    def run_detail(self, run_id: str) -> dict:
        d = self._dir(run_id)
        art = d / "artifacts"
        return {"run_id": run_id, "runnable": self.runnable(run_id), "mode": self._read_json(d / "run.json").get("mode", "unknown"),
                "meta": {k: v for k, v in self._read_json(d / "run.json").items() if k != "requirement"},
                "requirement": self._read_json(d / "run.json").get("requirement", ""),
                "metrics": self._read_json(d / "metrics.json"),
                "report": (d / "report.md").read_text(encoding="utf-8") if (d / "report.md").exists() else "",
                "artifacts": sorted(p.name for p in art.iterdir()) if art.is_dir() else []}

    def read_artifact(self, run_id: str, name: str) -> dict:
        d = self._dir(run_id) / "artifacts"
        names = {p.name: p for p in d.iterdir()} if d.is_dir() else {}
        if name not in names:                                    # the name must be an existing file: no path tricks possible
            raise AdminError(404, "no such artifact", "not_found")
        data = names[name].read_bytes()
        return {"name": name, "size": len(data), "truncated": len(data) > MAX_ARTIFACT_BYTES,
                "content": data[:MAX_ARTIFACT_BYTES].decode("utf-8", "replace")}

    def download(self, run_id: str) -> bytes:
        d = self._dir(run_id)
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
            for name in ("report.md", "metrics.json", "audit.jsonl", "run.json"):
                if (d / name).exists():
                    z.write(d / name, f"{run_id}/{name}")
            if (d / "artifacts").is_dir():
                for p in sorted((d / "artifacts").iterdir()):
                    z.write(p, f"{run_id}/artifacts/{p.name}")                # prompts (llm-records) are deliberately excluded
        return buf.getvalue()

    def audit_check(self, run_id: str) -> dict:
        d = self._dir(run_id)
        path = d / "audit.jsonl"
        if not path.exists():
            raise AdminError(404, "no audit log", "not_found")
        return {"intact": AuditLog.verify(path), "events": len(path.read_text(encoding="utf-8").splitlines())}

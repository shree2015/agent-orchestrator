"""Local admin server: a WSGI app (standard library) for the admin page and its JSON API.

Local-only by design: it binds to 127.0.0.1, and every request is checked against DNS-rebinding and cross-site attacks.
  * Host must be 127.0.0.1:<port> or localhost:<port>
  * state-changing requests (POST/DELETE) need the per-start token in `X-Admin-Token`, and an Origin (if sent) must be us
  * request bodies are capped; responses are `no-store` with `nosniff`; the page carries a hash-based CSP
"""
from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from socketserver import ThreadingMixIn
from wsgiref.simple_server import WSGIRequestHandler, WSGIServer, make_server

from ..llm import anthropic_client, contracts
from . import scenarios
from .security import csp_for, host_allowed, origin_allowed, token_ok
from .session import MAX_REQUIREMENT_CHARS, AdminError, AdminState

MAX_BODY = 64 * 1024
PAGE = Path(__file__).with_name("admin.html")
STATUS_TEXT = {200: "OK", 201: "Created", 400: "Bad Request", 403: "Forbidden", 404: "Not Found", 405: "Method Not Allowed",
               409: "Conflict", 413: "Payload Too Large", 500: "Internal Server Error"}
_NAME = r"[A-Za-z0-9._-]{1,80}"


def drain_request_body(environ) -> None:
    """Read and discard a request body nobody read (at most 1 MiB, in chunks).

    Replying and then closing a connection that still has unread incoming data makes some systems (notably Windows) reset it:
    the browser would then see "connection reset" instead of our answer (for example a 403 or the reply to Stop).
    """
    if environ.get("admin.body_consumed"):
        return
    try:
        length = int(environ.get("CONTENT_LENGTH") or 0)
    except ValueError:
        return
    remaining = min(length, 1 << 20)
    while remaining > 0:
        chunk = environ["wsgi.input"].read(min(remaining, 65536))
        if not chunk:
            break
        remaining -= len(chunk)
    environ["admin.body_consumed"] = True


def make_app(state: AdminState, allowed_hosts: set[str] | None = None):
    page = PAGE.read_text(encoding="utf-8")
    csp = csp_for(page)
    token = secrets.token_urlsafe(24)
    hosts = allowed_hosts if allowed_hosts is not None else set()
    base = [("Cache-Control", "no-store"), ("X-Content-Type-Options", "nosniff"), ("Referrer-Policy", "no-referrer")]

    def respond(start_response, status, body=None, headers=(), content_type="application/json"):
        payload = body if isinstance(body, bytes) else (b"" if body is None else json.dumps(body).encode("utf-8"))
        out = [*base, *headers, ("Content-Length", str(len(payload)))]
        if payload:
            out.append(("Content-Type", content_type))
        start_response(f"{status} {STATUS_TEXT.get(status, 'OK')}", out)
        return [payload]

    def read_json(environ) -> dict:
        try:
            length = int(environ.get("CONTENT_LENGTH") or 0)
        except ValueError:
            raise AdminError(400, "invalid Content-Length") from None
        if length > MAX_BODY:
            raise AdminError(413, "request body too large")
        raw = environ["wsgi.input"].read(length)
        environ["admin.body_consumed"] = True
        try:
            data = json.loads(raw or b"{}")
        except ValueError:
            raise AdminError(400, "body must be valid JSON") from None
        if not isinstance(data, dict):
            raise AdminError(400, "body must be a JSON object")
        return data

    def current() -> "object":
        s = state.session
        if s is None:
            raise AdminError(404, "there is no run yet", "not_found")
        return s

    def route(environ, start_response):
        method, path = environ["REQUEST_METHOD"], environ.get("PATH_INFO", "/")
        if path == "/" and method == "GET":
            return respond(start_response, 200, page.encode("utf-8"), [("Content-Security-Policy", csp)], "text/html; charset=utf-8")
        if path == "/api/config" and method == "GET":
            return respond(start_response, 200, {
                "token": token, "key_status": state.key_status(), "default_model": anthropic_client.DEFAULT_MODEL,
                "scenarios": scenarios.public_list(), "roles": sorted(contracts.CONTRACTS),
                "limits": {"requirement_chars": MAX_REQUIREMENT_CHARS, "max_calls": 200, "default_calls": 40}})
        if (m := re.fullmatch(r"/api/template/(\w+)", path)) and method == "GET":
            if m.group(1) not in scenarios.SCENARIOS:
                raise AdminError(404, "unknown scenario", "not_found")
            return respond(start_response, 200, {"requirement": scenarios.template_requirement(m.group(1))})
        if path == "/api/key":
            if method == "POST":
                state.set_key(read_json(environ).get("key"))
            elif method == "DELETE":
                state.clear_key()
            else:
                raise AdminError(405, "use POST or DELETE", "method_not_allowed")
            return respond(start_response, 200, {"key_status": state.key_status()})           # never echoes the key
        if path == "/api/service":
            if method == "GET":
                return respond(start_response, 200, state.services.status())
            if method == "POST":
                return respond(start_response, 200, state.start_service(read_json(environ)))
            if method == "DELETE":
                return respond(start_response, 200, state.services.stop())
            raise AdminError(405, "use GET, POST or DELETE", "method_not_allowed")
        if path == "/api/runs" and method == "POST":
            return respond(start_response, 201, {"run_id": state.start(read_json(environ)).run_id})
        if path == "/api/runs" and method == "GET":
            return respond(start_response, 200, {"runs": state.list_runs()})
        if path == "/api/runs/current" and method == "GET":
            s = state.session
            return respond(start_response, 200, s.snapshot() if s else {"run": None})
        if (m := re.fullmatch(rf"/api/runs/current/decisions/(\w+)", path)) and method == "POST":
            current().resolve(m.group(1), read_json(environ))
            return respond(start_response, 200, {"ok": True})
        if path == "/api/runs/current/stop" and method == "POST":
            s = current()
            if not s.busy:
                raise AdminError(409, "nothing is running")
            s.stop()
            return respond(start_response, 200, {"ok": True})
        if path == "/api/runs/current/revise" and method == "POST":
            body = read_json(environ)
            return respond(start_response, 200, current().revise(body.get("decision_id"), body.get("value")))
        if (m := re.fullmatch(rf"/api/runs/({_NAME})/artifacts/({_NAME})", path)) and method == "GET":
            return respond(start_response, 200, state.read_artifact(m.group(1), m.group(2)))
        if (m := re.fullmatch(rf"/api/runs/({_NAME})/download", path)) and method == "GET":
            return respond(start_response, 200, state.download(m.group(1)),
                           [("Content-Disposition", f'attachment; filename="{m.group(1)}.zip"')], "application/zip")
        if (m := re.fullmatch(rf"/api/runs/({_NAME})/audit-check", path)) and method == "GET":
            return respond(start_response, 200, state.audit_check(m.group(1)))
        if (m := re.fullmatch(rf"/api/runs/({_NAME})", path)) and method == "GET":
            return respond(start_response, 200, state.run_detail(m.group(1)))
        raise AdminError(404, "no such route", "not_found")

    def app(environ, start_response):
        method = environ["REQUEST_METHOD"]
        try:
            if not host_allowed(environ.get("HTTP_HOST"), hosts):
                raise AdminError(403, "unexpected Host header (this page only answers on localhost)", "forbidden")
            if method not in ("GET", "POST", "DELETE"):
                raise AdminError(405, "method not allowed", "method_not_allowed")
            if method != "GET":
                if not origin_allowed(environ.get("HTTP_ORIGIN"), hosts):
                    raise AdminError(403, "cross-site request refused", "forbidden")
                if not token_ok(environ.get("HTTP_X_ADMIN_TOKEN"), token):
                    raise AdminError(403, "missing or wrong admin token (reload the page)", "forbidden")
            return route(environ, start_response)
        except AdminError as exc:
            return respond(start_response, exc.status, {"error": {"code": exc.code, "message": str(exc)}})
        except Exception:                                                    # never leak internals to the browser
            return respond(start_response, 500, {"error": {"code": "internal_error", "message": "internal error"}})
        finally:
            drain_request_body(environ)                                      # whatever the outcome: leave nothing unread

    app.token, app.allowed_hosts, app.state = token, hosts, state
    return app


class _Server(ThreadingMixIn, WSGIServer):
    daemon_threads = True


class _Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def build_server(port: int = 8765, run_root: str | Path = "runs"):
    """Create (but do not start) the server, bound to loopback only. Returns (httpd, app, state)."""
    state = AdminState(run_root)
    app = make_app(state)
    httpd = make_server("127.0.0.1", port, app, server_class=_Server, handler_class=_Quiet)
    real = httpd.server_port
    app.allowed_hosts.update({f"127.0.0.1:{real}", f"localhost:{real}"})
    return httpd, app, state

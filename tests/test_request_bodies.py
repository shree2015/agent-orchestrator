"""Both servers must read a request body before replying, even when refusing.

Found on a Windows machine: replying to (and then closing) a connection that still holds unread incoming data makes Windows
RESET it, so the client saw "connection reset" instead of the 429 the rate limiter had produced. Linux usually delivers the
reply anyway, so the cause is tested directly (was the body consumed?) as well as with real sockets.
"""
import io
import json
import socket
import threading
import time
from wsgiref.simple_server import WSGIRequestHandler, make_server

from orchestrator.admin.server import MAX_BODY, build_server, make_app as make_admin_app
from orchestrator.admin.session import AdminState
from orchestrator.workflows import brownfield, shortener

CODES = {"greenfield service": shortener.GOOD_CODE, "brownfield baseline": brownfield.BASELINE_CODE, "brownfield fix": brownfield.FIXED_CODE}


def load(code):
    ns = {}
    exec(compile(code, "service", "exec"), ns)
    return ns


def wsgi_call(app, method, path, body, **headers):
    raw = body if isinstance(body, bytes) else json.dumps(body).encode()
    stream = io.BytesIO(raw)
    environ = {"REQUEST_METHOD": method, "PATH_INFO": path, "CONTENT_LENGTH": str(len(raw)), "wsgi.input": stream,
               "REMOTE_ADDR": "10.0.0.1", **headers}
    seen = {}
    app(environ, lambda status, hdrs: seen.update(status=int(status.split()[0])))
    return seen["status"], stream.tell(), len(raw)


# ---------------------------------------------------------------- the shortener service (all three versions)
def test_the_service_reads_the_whole_body_on_every_path_including_refusals():
    for name, code in CODES.items():
        ns = load(code)
        app = ns["make_app"](ns["ShortenerService"](clock=lambda: 1.0e9), ns["RateLimiter"](limit=1, window_s=60, clock=lambda: 0.0))
        body = {"url": "https://example.com/a"}
        status, consumed, sent = wsgi_call(app, "POST", "/api/shorten", body)
        assert (status, consumed) == (201, sent), name                                    # served
        status, consumed, sent = wsgi_call(app, "POST", "/api/shorten", body)
        assert (status, consumed) == (429, sent), f"{name}: a rate-limited request left its body unread"
        for method, path in (("POST", "/abcdefg"), ("POST", "/no/such/route"), ("PUT", "/api/shorten"), ("POST", "/healthz")):
            status, consumed, sent = wsgi_call(app, method, path, {"x": "y" * 50})
            assert consumed == sent and status in (404, 405, 200), f"{name}: {method} {path} left its body unread"
        status, consumed, sent = wsgi_call(app, "DELETE", "/api/links/nonexistent", {"x": 1})
        assert consumed == sent, name


def test_an_oversized_body_is_drained_fully_up_to_1_mib_and_invalid_json_is_fully_read():
    ns = load(shortener.GOOD_CODE)
    app = ns["make_app"](ns["ShortenerService"](clock=lambda: 1.0e9), ns["RateLimiter"](limit=100))
    status, consumed, sent = wsgi_call(app, "POST", "/api/shorten", b"x" * 9000)
    assert status == 413 and consumed == sent == 9000                                  # not just the first 4 KB: nothing left unread
    status, consumed, sent = wsgi_call(app, "POST", "/api/shorten", b"x" * (2 << 20))
    assert status == 413 and consumed == 1 << 20 and sent == 2 << 20                  # bounded: never reads an unbounded body
    status, consumed, sent = wsgi_call(app, "POST", "/api/shorten", b"{not json")
    assert status == 400 and consumed == sent


def test_a_bad_content_length_does_not_crash_the_drain():
    ns = load(shortener.GOOD_CODE)
    app = ns["make_app"](ns["ShortenerService"](clock=lambda: 1.0e9), ns["RateLimiter"](limit=100))
    env = {"REQUEST_METHOD": "POST", "PATH_INFO": "/no/such/route", "CONTENT_LENGTH": "banana", "wsgi.input": io.BytesIO(b"abc"), "REMOTE_ADDR": "x"}
    seen = {}
    app(env, lambda s, h: seen.update(s=s))
    assert seen["s"].startswith("404")


# ---------------------------------------------------------------------------------------- the admin server
def admin_call(app, method, path, body, token=True, host="127.0.0.1:8765"):
    extra = {"HTTP_HOST": host}
    if token:
        extra["HTTP_X_ADMIN_TOKEN"] = app.token
    return wsgi_call(app, method, path, body, **extra)


def test_the_admin_server_reads_the_whole_body_on_every_refusal_path_including_stop(tmp_path):
    app = make_admin_app(AdminState(tmp_path / "runs"), {"127.0.0.1:8765"})
    cases = [("stop with nothing running", ("POST", "/api/runs/current/stop", {}), 404),
             ("no token", ("POST", "/api/runs", {"scenario": "slugify", "requirement": "x"}, False), 403),
             ("wrong host", ("POST", "/api/runs", {"a": 1}, True, "evil.example.com"), 403),
             ("unknown route", ("POST", "/api/nothing", {"a": "b" * 100}), 404),
             ("wrong method", ("PUT", "/api/runs", {"a": 1}), 405)]
    for label, args, expected in cases:
        status, consumed, sent = admin_call(app, *args)
        assert status == expected and consumed == sent, f"{label}: status {status}, read {consumed} of {sent} bytes"
    status, consumed, sent = admin_call(app, "POST", "/api/runs", b"x" * (MAX_BODY + 500))
    assert status == 413 and consumed == sent                                           # nothing left unread
    status, consumed, sent = admin_call(app, "POST", "/api/runs", b"x" * (2 << 20))
    assert status == 413 and consumed == 1 << 20                                        # but bounded


# ------------------------------------------------------------------------------------------- real sockets
def raw_post(port, path, body, extra_headers=""):
    """What a browser does: send headers, then the body, then read the whole reply. Raises on a connection reset."""
    with socket.create_connection(("127.0.0.1", port), 5) as s:
        head = (f"POST {path} HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\n"
                f"Content-Length: {len(body)}\r\nConnection: close\r\n{extra_headers}\r\n").encode()
        s.sendall(head)
        time.sleep(0.003)                                   # the body arrives in a separate segment, as with http.client
        s.sendall(body)
        chunks = []
        while True:
            data = s.recv(65536)
            if not data:
                break
            chunks.append(data)
    return b"".join(chunks)


class Quiet(WSGIRequestHandler):
    def log_message(self, *args):
        pass


def test_a_burst_of_rate_limited_posts_is_answered_cleanly_over_real_sockets():
    ns = load(shortener.GOOD_CODE)
    app = ns["make_app"](ns["ShortenerService"](), ns["RateLimiter"](limit=3, window_s=60))
    server = make_server("127.0.0.1", 0, app, handler_class=Quiet)
    threading.Thread(target=server.serve_forever, daemon=True).start()
    try:
        statuses = []
        for i in range(40):                                  # the very burst that failed on Windows
            reply = raw_post(server.server_port, "/api/shorten", json.dumps({"url": f"https://example.com/burst{i}"}).encode())
            statuses.append(int(reply.split(b" ", 2)[1]))
        assert statuses[:3] == [201, 201, 201] and set(statuses[3:]) == {429}
    finally:
        server.shutdown()
        server.server_close()


def test_the_admin_server_answers_refusals_cleanly_over_real_sockets(tmp_path):
    httpd, app, state = build_server(0, tmp_path / "runs")
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        for i in range(25):
            reply = raw_post(httpd.server_port, "/api/runs", json.dumps({"scenario": "slugify", "n": i}).encode())          # no token
            assert reply.startswith(b"HTTP/1.") and b" 403 " in reply.split(b"\r\n", 1)[0] and b"admin token" in reply
        reply = raw_post(httpd.server_port, "/api/runs/current/stop", b"{}", f"X-Admin-Token: {app.token}\r\n")
        assert b" 404 " in reply.split(b"\r\n", 1)[0]
    finally:
        state.shutdown()
        httpd.shutdown()
        httpd.server_close()

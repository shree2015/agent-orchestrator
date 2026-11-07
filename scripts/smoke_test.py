"""Smoke test for a running URL shortener. Standard library only.

    python scripts/smoke_test.py                       # server on http://127.0.0.1:8000
    python scripts/smoke_test.py http://127.0.0.1:9000
    python scripts/smoke_test.py --rate-limit          # also trigger the rate limiter (then wait ~1 minute)

Each run makes about a dozen write requests; the server allows 30 per minute per client, so if you
run this several times in a row wait a minute (or restart the server) to avoid false 429 failures.
Exit code: 0 when every check passes, 1 otherwise.
"""
import argparse
import http.client
import json
import sys
import time
import uuid
from urllib.parse import urlsplit


class Client:
    def __init__(self, base: str) -> None:
        parts = urlsplit(base)
        self.host, self.port = parts.hostname, parts.port or 80
        self.failures = 0

    def call(self, method, path, body=None, headers=None):
        """Return (status, response headers, parsed JSON or None). Redirects are NOT followed."""
        conn = http.client.HTTPConnection(self.host, self.port, timeout=10)
        raw = body if isinstance(body, bytes) else (json.dumps(body).encode() if body is not None else None)
        conn.request(method, path, raw, headers or {})
        resp = conn.getresponse()
        data = resp.read()
        conn.close()
        try:
            parsed = json.loads(data) if data else None
        except ValueError:
            parsed = None
        return resp.status, dict(resp.getheaders()), parsed

    def check(self, name, ok, detail=""):
        self.failures += 0 if ok else 1
        print(f"  {'PASS' if ok else 'FAIL'}  {name}" + ("" if ok else f"   -> {detail}"))


def run(base: str, rate_limit: bool = False) -> int:
    c = Client(base)
    call, check = c.call, c.check
    run_id = uuid.uuid4().hex[:8]
    target = f"https://example.com/smoke/{run_id}"

    print(f"Smoke testing {base}")
    try:
        status, _, body = call("GET", "/healthz")
    except OSError as exc:
        print(f"Cannot reach {base}: {exc}\nIs the server running?")
        return 2
    check("health check", status == 200 and body == {"status": "ok"}, (status, body))

    print("Create and redirect")
    status, _, created = call("POST", "/api/shorten", {"url": target})
    check("shorten returns 201 with a 7-character code", status == 201 and len(created["code"]) == 7, (status, created))
    code = created["code"]
    check("short_url ends with the code", created["short_url"].endswith("/" + code), created)
    status, _, again = call("POST", "/api/shorten", {"url": target})
    check("same URL again returns 200 and the same code", status == 200 and again["code"] == code, (status, again))
    status, headers, _ = call("GET", "/" + code, headers={"Referer": "https://ref.example/"})
    check("redirect is 302 to the original URL", status == 302 and headers.get("Location") == target, (status, headers))

    print("Metadata and analytics")
    status, _, meta = call("GET", f"/api/links/{code}")
    check("metadata is available and not expired", status == 200 and meta["expired"] is False, (status, meta))
    status, _, stats = call("GET", f"/api/links/{code}/stats")
    check("stats count the click and the referrer", status == 200 and stats["total_clicks"] == 1
          and stats["top_referrers"] == [["https://ref.example/", 1]], (status, stats))

    print("Custom alias")
    alias = f"smoke-{run_id}"
    status, _, link = call("POST", "/api/shorten", {"url": target + "/a", "alias": alias})
    check("custom alias is accepted", status == 201 and link["code"] == alias, (status, link))
    status, _, err = call("POST", "/api/shorten", {"url": target + "/b", "alias": alias})
    check("duplicate alias is rejected with 409", status == 409 and err["error"]["code"] == "alias_taken", (status, err))

    print("Rejected input")
    for label, payload, want in [
        ("localhost URL", {"url": "http://localhost/admin"}, 400),
        ("loopback IP", {"url": "http://127.0.0.1/"}, 400),
        ("javascript: URL", {"url": "javascript:alert(1)"}, 400),
        ("alias too short", {"url": target, "alias": "ab"}, 400),
        ("reserved alias", {"url": target, "alias": "admin"}, 400),
        ("invalid JSON", b"{not json", 400),
        ("JSON array instead of object", [1, 2], 400),
        ("oversized body", b"x" * 5000, 413),
    ]:
        status, _, _ = call("POST", "/api/shorten", payload)
        check(f"{label} -> {want}", status == want, status)

    print("Expiry")
    status, _, _ = call("POST", "/api/shorten", {"url": target + "/ttl", "alias": f"ttl-{run_id}", "ttl_seconds": 2})
    check("link with a 2 second TTL is created", status == 201, status)
    check("it redirects while alive", call("GET", f"/ttl-{run_id}")[0] == 302)
    time.sleep(3)
    check("it returns 410 Gone after expiry", call("GET", f"/ttl-{run_id}")[0] == 410)

    print("Routing and delete")
    check("unknown code -> 404", call("GET", "/doesnotexist")[0] == 404)
    check("wrong method -> 405", call("GET", "/api/shorten")[0] == 405)
    check("delete -> 204", call("DELETE", f"/api/links/{code}")[0] == 204)
    check("deleted link -> 404", call("GET", "/" + code)[0] == 404)

    if rate_limit:
        print("Rate limit (burst of 40 writes)")
        results = [call("POST", "/api/shorten", {"url": f"{target}/burst{i}"}) for i in range(40)]
        limited = [h for s, h, _ in results if s == 429]
        check("some requests get 429", bool(limited), [s for s, _, _ in results])
        check("429 carries a Retry-After header", bool(limited) and int(limited[0]["Retry-After"]) >= 1)

    print(f"\n{'ALL CHECKS PASSED' if not c.failures else str(c.failures) + ' CHECK(S) FAILED'}")
    return 1 if c.failures else 0


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="Smoke test a running URL shortener")
    ap.add_argument("base", nargs="?", default="http://127.0.0.1:8000")
    ap.add_argument("--rate-limit", action="store_true", help="also send a burst to trigger HTTP 429")
    args = ap.parse_args(argv)
    return run(args.base, args.rate_limit)


if __name__ == "__main__":
    sys.exit(main())

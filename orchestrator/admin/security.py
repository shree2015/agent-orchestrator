"""Request-level protections for a LOCAL admin page, plus the page's Content-Security-Policy."""
from __future__ import annotations

import base64
import hashlib
import hmac
import re

_INLINE = {tag: re.compile(rf"<{tag}>(.*?)</{tag}>", re.S) for tag in ("script", "style")}


def csp_for(html: str) -> str:
    """Strict CSP that allows only this page's own inline script/style blocks (by hash) and same-origin requests."""
    def allowed(tag: str) -> str:
        digests = [base64.b64encode(hashlib.sha256(b.encode("utf-8")).digest()).decode() for b in _INLINE[tag].findall(html)]
        return " ".join(f"'sha256-{d}'" for d in digests) or "'none'"
    return (f"default-src 'none'; script-src {allowed('script')}; style-src {allowed('style')}; connect-src 'self'; "
            "img-src 'self' data:; base-uri 'none'; form-action 'none'; frame-ancestors 'none'")


def host_allowed(host: str | None, allowed_hosts: set[str]) -> bool:
    """Anti DNS-rebinding: only the loopback names on our own port are accepted as the Host."""
    return bool(host) and host.lower() in allowed_hosts


def origin_allowed(origin: str | None, allowed_hosts: set[str]) -> bool:
    """Browsers always send Origin on cross-origin POSTs. Absent is fine (curl); present must be us."""
    return origin is None or origin in {f"http://{h}" for h in allowed_hosts}


def token_ok(supplied: str | None, expected: str) -> bool:
    return bool(supplied) and hmac.compare_digest(supplied.encode("utf-8"), expected.encode("utf-8"))


_KEY_OK = re.compile(r"[\x21-\x7e]{20,300}")


def valid_api_key_shape(key: str) -> bool:
    """Reject obviously wrong input (spaces, newlines, too short) without pretending to validate the key itself."""
    return bool(_KEY_OK.fullmatch(key))

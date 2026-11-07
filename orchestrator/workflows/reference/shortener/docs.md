# URL shortener

Single-module, standard-library service. Run: `python shortener.py [port]` (default 8000, database `shortener.db`).
Built from three layers: `make_app` (WSGI HTTP adapter) -> `ShortenerService` (business rules) -> `LinkRepository` (SQLite).

## HTTP API
| Method & path | Success | Notes |
|---|---|---|
| `POST /api/shorten` body `{"url", "alias"?, "ttl_seconds"?}` | 201 new, 200 existing | Same permanent URL returns the same code. Rate limited. |
| `GET /{code}` | 302 + `Location` | Records a click (best effort). |
| `GET /api/links/{code}` | 200 | Metadata including `expired`. |
| `GET /api/links/{code}/stats` | 200 | `total_clicks`, `clicks_by_day`, `last_accessed`, `top_referrers`. |
| `DELETE /api/links/{code}` | 204 | Removes the link and its clicks. Rate limited. |
| `GET /healthz` | 200 | Liveness. |

Errors are JSON `{"error": {"code", "message"}}`:
| Exception | Status | Code |
|---|---|---|
| `ShortenerError` (base, e.g. bad TTL or body) | 400 | `bad_request` |
| `InvalidUrl` | 400 | `invalid_url` |
| `InvalidAlias` | 400 | `invalid_alias` |
| `NotFound` | 404 | `not_found` |
| `MethodNotAllowed` | 405 | `method_not_allowed` |
| `AliasTaken` | 409 | `alias_taken` |
| `Expired` | 410 | `expired` |
| `PayloadTooLarge` | 413 | `payload_too_large` |
| `RateLimited` | 429 + `Retry-After` | `rate_limited` |
| `Unavailable` | 503 | `unavailable` |

## Components
* `validate_url` accepts only absolute ASCII http(s) URLs without credentials, whitespace or private/loopback hosts.
* `validate_alias` enforces 3-32 characters of letters, digits, `_`, `-` and blocks reserved words.
* `RateLimiter` is a thread-safe fixed-window limiter keyed by client address.
* `LinkRepository` owns the SQL schema (links, clicks) and aggregates analytics.
* `ShortenerService` allocates codes, retries collisions, reuses permanent links, expires links and records clicks.
* `make_app` builds the WSGI app; `serve` runs it with the standard-library server (development only).

## Reliability and safety
Collision retry then 503; analytics failures never break redirects; expired aliases can be reclaimed; parameterised SQL; 500 responses never expose internals; request bodies capped at 4 KB.

## Limitations
No DNS resolution, so a public hostname that resolves to a private address is not blocked; rate limiter state is per process; `serve` is single-threaded; no authentication, so any client can delete any link (an owner token is the next step).

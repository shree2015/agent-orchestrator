# URL shortener - requirements in plain English

Edit this file freely, then run:
`python -m orchestrator.cli run shortener --llm anthropic --requirement-file examples/requirements/url-shortener.md --approve auto`

Everything below the line is what the agents receive. Keep the **API routes** and the **technical constraints**
unless you also expect to change the web page and the checks that depend on them.

---

## Purpose
A small web service that turns long web addresses into short links, sends people who open a short link to the original
address, and shows how often each short link is used.

## What people can do
1. **Shorten an address.** Give a long web address and get back a short code and the full short link.
2. **Choose their own alias** instead of a random code (3 to 32 letters, digits, "-" or "_"). Words the service itself
   needs ("api", "healthz", "static", "admin") cannot be used as aliases.
3. **Make a link expire** after a number of seconds they choose, up to one year. Without a choice the link never expires.
4. **Open a short link** and be redirected (HTTP 302) to the original address. An unknown code gives 404; an expired
   link gives 410 and must never redirect.
5. **See statistics** for a link: total clicks, clicks per day, when it was last opened, and its top referrers.
6. **Delete a link**, which also removes its statistics.
7. **Use a simple web page** at the root address ("/") to do all of the above without writing any code.

## Rules the service must follow
- Only `http` and `https` addresses are accepted. Reject addresses with a password in them, addresses that point at the
  local machine or a private network (for example localhost or 192.168.x.x), and addresses containing spaces or control
  characters. Addresses are at most 2048 characters and plain ASCII.
- Shortening the same address twice (no alias, no expiry) returns the same short link instead of a new one.
- A random code is 7 letters or digits. Two links must never get the same code, even when requests arrive at the same time.
  If a random code happens to be taken, try again a few times before giving up with a 503.
- A failure while recording a click must never stop the redirect.
- Creating and deleting links is rate limited to 30 requests per minute per client. Over the limit the service answers 429
  with a `Retry-After` header.
- Errors are always JSON: `{"error": {"code": "...", "message": "..."}}` with a sensible HTTP status.
- Never show internal details (stack traces, file paths) to the caller.
- Always read the whole request body before replying, even when refusing a request (for example with 429 or 405). Otherwise some clients, notably on Windows, see a "connection reset" instead of the answer.

## The API (fixed: the web page and the automated checks rely on these exact routes)
| Route | What it does | Success |
|---|---|---|
| `POST /api/shorten` with JSON `{"url", "alias"?, "ttl_seconds"?}` | create a link | 201 for a new link, 200 if an existing one is reused; body has `code`, `short_url`, `url`, `created_at`, `expires_at` |
| `GET /{code}` | redirect | 302 with a `Location` header |
| `GET /api/links/{code}` | link details, including whether it has expired | 200 |
| `GET /api/links/{code}/stats` | statistics: `total_clicks`, `clicks_by_day`, `last_accessed`, `top_referrers` | 200 |
| `DELETE /api/links/{code}` | delete a link and its statistics | 204 |
| `GET /healthz` | is the service alive | 200 `{"status": "ok"}` |
| `GET /` | the web page, served from a file called `ui.html` next to the program (404 if the file is absent) | 200 |

## The web page
One page with a form (address, optional alias, optional expiry) that shows the new short link with a copy button, and a
list of the links created in this browser with their click statistics, a refresh button and a delete button. It must work
without loading anything from the internet.

## Technical constraints
- Python 3.10 or newer, **standard library only** (use `sqlite3` for storage and `wsgiref` to serve). No third-party packages.
- **One Python module** that can be started with `python implementation.py 8000` (the number is the port; default 8000).
- Store everything in SQLite in a file called `shortener.db`; use parameterised SQL only.
- Keep the module under about 500 lines.

## How we will know it works
- A short link redirects to its target with HTTP 302.
- Clicks show up in the statistics, per day and per referrer.
- An expired link returns 410 and never redirects.
- Creating many links at once never produces two identical codes.
- If recording a click fails the redirect still works; a client over the rate limit gets 429 with `Retry-After`.
- The web page loads no outside resources and works with a strict Content-Security-Policy.

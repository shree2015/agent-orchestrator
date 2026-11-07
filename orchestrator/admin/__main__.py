"""Start the local admin page:  python -m orchestrator.admin [--port 8765] [--run-root runs] [--open]"""
from __future__ import annotations

import argparse
import webbrowser

from .server import build_server


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="python -m orchestrator.admin", description="Local admin page for the agent orchestrator")
    p.add_argument("--port", type=int, default=8765)
    p.add_argument("--run-root", default="runs", help="folder where runs are stored and listed")
    p.add_argument("--open", action="store_true", help="open the page in your browser")
    a = p.parse_args(argv)
    httpd, app, state = build_server(a.port, a.run_root)
    url = f"http://127.0.0.1:{httpd.server_port}/"
    key = {"env": "found in ANTHROPIC_API_KEY", "none": "not set (Mock mode works without one)", "session": "set"}[state.key_status()]
    print(f"Admin page: {url}\nRuns folder: {a.run_root}\nAPI key: {key}\nLocal only (127.0.0.1). Press Ctrl+C to stop.")
    if a.open:
        webbrowser.open(url)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping...")
    finally:
        state.shutdown()
        httpd.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

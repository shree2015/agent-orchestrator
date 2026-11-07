"""Run a generated service from a finished run, safely enough for a local demo.

What is enforced (not merely hoped for):
  * LOOPBACK ONLY, at the socket level: the child process is started through a tiny wrapper that rewrites every
    `socket.bind` to 127.0.0.1 (or ::1), so even generated code that asks for 0.0.0.0 can only listen on this machine.
  * A free port is chosen by the operating system; at most ONE service runs at a time.
  * The child gets a SCRUBBED environment: anything whose name looks like a key, token, secret or password is dropped,
    so the API key is never handed to generated code.
  * The child runs in its own folder (`<run>/service/`), so its database and log stay with the run, not in your project.
  * The security policies are re-run on the file immediately before it starts (it may have been edited since the run).
What is NOT enforced: outbound network access and file access are the operating system's normal permissions for you.
That is why runs that were not produced by the reviewed mock agents need an explicit confirmation.
"""
from __future__ import annotations

import os
import re
import signal
import socket
import subprocess
import sys
import threading
import time
from pathlib import Path

from ..governance.policies import security

_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL|AUTH", re.I)

# Runs inside the child: force every bind() onto loopback, then run the service file as a script.
WRAPPER = r'''
import runpy, socket, sys
_original_bind = socket.socket.bind
def _loopback_bind(self, address, *args, **kwargs):
    if isinstance(address, tuple) and address:
        host = "::1" if self.family == socket.AF_INET6 else "127.0.0.1"
        address = (host,) + tuple(address[1:])
    return _original_bind(self, address, *args, **kwargs)
socket.socket.bind = _loopback_bind
path = sys.argv[1]
sys.argv = [path] + sys.argv[2:]
runpy.run_path(path, run_name="__main__")
'''


# Why a whole process TREE must be stopped: on Windows a virtual environment's python.exe is a small launcher that starts the
# real interpreter as a CHILD. Terminating only the launcher leaves the real service running as an orphan, holding its port.
_NEW_GROUP = {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP} if os.name == "nt" else {"start_new_session": True}


def spawn(argv: list[str], cwd: Path, env: dict, stdout) -> subprocess.Popen:
    """Start a child in its own process group (POSIX session / Windows group) so the whole tree can be stopped together."""
    return subprocess.Popen(argv, cwd=cwd, env=env, stdout=stdout, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL, **_NEW_GROUP)


def _signal_tree(proc: subprocess.Popen, force: bool) -> None:
    if os.name == "nt":                                               # /T = the whole tree, /F = force
        subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=15)
        return
    try:
        os.killpg(proc.pid, signal.SIGKILL if force else signal.SIGTERM)   # the child leads its own group
    except (ProcessLookupError, PermissionError):
        pass


def terminate_tree(proc: subprocess.Popen, wait: float = 5.0) -> None:
    """Stop a child AND everything it started. Gentle first (POSIX), then forced."""
    if proc.poll() is None:
        _signal_tree(proc, force=False)
        try:
            proc.wait(wait)
        except subprocess.TimeoutExpired:
            _signal_tree(proc, force=True)
            proc.wait(wait)
    else:
        _signal_tree(proc, force=True)                                # the leader is gone: sweep up anything it left behind


def port_is_open(port: int) -> bool:
    try:
        socket.create_connection(("127.0.0.1", port), 0.2).close()
        return True
    except OSError:
        return False


def safe_env(env: dict) -> dict:
    """A copy of the environment without anything that looks like a credential."""
    clean = {k: v for k, v in env.items() if not _SECRET_NAME.search(k)}
    clean["PYTHONUNBUFFERED"] = "1"
    clean["PYTHONIOENCODING"] = "utf-8"
    return clean


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


_MAIN_GUARD = re.compile(r"""__name__\s*==\s*['"]__main__['"]""")
_SERVER_MARKERS = ("make_server", "serve_forever", "wsgiref", "http.server", "HTTPServer", "socketserver")


def why_not_runnable(code: str) -> str | None:
    """None when the file looks like a program that starts a web server; otherwise the reason it is not offered as a service.

    This looks at BEHAVIOUR (a `__main__` block, a server being started), never at the names a particular implementation chose:
    a real model structures its service its own way (the first live run used classes, not our reference's make_app/serve).
    """
    if not _MAIN_GUARD.search(code):
        return "it has no `if __name__ == \"__main__\":` block, so it cannot be started as a program"
    if not any(m in code for m in _SERVER_MARKERS):
        return "it does not appear to start a web server (no wsgiref or http.server use)"
    return None


def is_runnable(code: str) -> bool:
    """A run produced a runnable service when the implementation can be started as a script and starts a web server."""
    return why_not_runnable(code) is None


class ServiceError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class ServiceManager:
    """At most one generated service at a time."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self.info: dict | None = None
        self._last_exit: dict | None = None
        self._lock = threading.Lock()

    def status(self) -> dict:
        with self._lock:
            if self._proc is not None and self._proc.poll() is not None:        # it died by itself
                terminate_tree(self._proc)                                      # sweep up anything it left running
                self._last_exit = {"run_id": self.info["run_id"], "exit_code": self._proc.returncode,
                                   "log_tail": self._tail(self.info["log"])}
                self._proc, self.info = None, None
            if self._proc is None:
                return {"running": False, "last_exit": self._last_exit}
            return {"running": True, **{k: v for k, v in self.info.items() if k != "log"}}

    @staticmethod
    def _tail(log: Path, n: int = 1200) -> str:
        try:
            return log.read_text(encoding="utf-8", errors="replace")[-n:]
        except OSError:
            return ""

    def start(self, run_dir: Path, run_id: str) -> dict:
        run_dir = Path(run_dir).resolve()               # the child starts in ANOTHER folder: every path given to it must be absolute
        impl = run_dir / "artifacts" / "implementation.py"
        code = impl.read_text(encoding="utf-8")
        violations = security.default_set().evaluate(code)
        if violations:                                                  # defence in depth: re-check right before running
            raise ServiceError(400, "the security policies now reject this file, so it will not be started: "
                               + "; ".join(str(v) for v in violations[:3]))
        with self._lock:
            if self._proc is not None and self._proc.poll() is None:
                raise ServiceError(409, f"a service for {self.info['run_id']} is already running: stop it first")
            work = run_dir / "service"
            work.mkdir(exist_ok=True)
            log = work / "service.log"
            port = free_port()
            log.write_bytes(b"")
            # The requirement template says "python implementation.py 8000" (the port is positional). A model may read it another way
            # (--port N), so if the first start dies complaining about its arguments, try the other form once.
            proc = None
            for args in ([str(port)], ["--port", str(port)]):
                proc = self._launch(impl, work, port, args, log)
                early = self._wait_listening(proc, port)
                if early is None:
                    break
                terminate_tree(proc)
                text = self._tail(log, 4000).lower()
                if not ("unrecognized arguments" in text or "usage:" in text) or args[0] == "--port":
                    raise ServiceError(500, "the service exited while starting: " + self._tail(log).strip()[-400:])
            else:
                raise ServiceError(500, "the service exited while starting: " + self._tail(log).strip()[-400:])
            if proc is None or proc.poll() is not None:
                raise ServiceError(500, "the service did not start")
            self._proc = proc
            self._last_exit = None
            self.info = {"run_id": run_id, "port": port, "url": f"http://127.0.0.1:{port}/", "pid": proc.pid,
                         "started_at": time.strftime("%H:%M:%S"), "log": log}
        return self.status()

    def _launch(self, impl: Path, work: Path, port: int, args: list[str], log: Path) -> subprocess.Popen:
        env = safe_env(dict(os.environ))
        env["PORT"] = str(port)                                      # a hint for programs that read it from the environment
        with log.open("ab") as logfile:
            return spawn([sys.executable, "-c", WRAPPER, str(impl), *args], work, env, logfile)

    def _wait_listening(self, proc: subprocess.Popen, port: int) -> str | None:
        """None once the port is open; 'exited' if the process died first; raises if it never listens."""
        deadline = time.time() + 12
        while time.time() < deadline:
            if proc.poll() is not None:
                return "exited"
            if port_is_open(port):
                return None
            time.sleep(0.1)
        terminate_tree(proc)
        raise ServiceError(500, "the service did not start listening within 12 seconds")

    def stop(self) -> dict:
        with self._lock:
            proc = self._proc
        port = (self.info or {}).get("port")
        if proc is not None:
            terminate_tree(proc)
            deadline = time.time() + 3                                # report "stopped" only once the port really is closed
            while port and port_is_open(port) and time.time() < deadline:
                time.sleep(0.05)
        with self._lock:
            if self._proc is proc:                  # a deliberate stop is not an unexpected exit: leave no "died" record
                self._proc, self.info, self._last_exit = None, None, None
        return self.status()

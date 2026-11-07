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


def is_runnable(code: str) -> bool:
    """A run produced a runnable service when the implementation can serve HTTP when started as a script."""
    return "def serve(" in code and "def make_app(" in code and "__main__" in code


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
            logfile = log.open("wb")
            try:
                proc = spawn([sys.executable, "-c", WRAPPER, str(impl), str(port)], work, safe_env(dict(os.environ)), logfile)
            finally:
                logfile.close()
            deadline = time.time() + 12
            while time.time() < deadline:
                if proc.poll() is not None:
                    terminate_tree(proc)                              # a launcher may have died while its child lives on
                    raise ServiceError(500, "the service exited while starting: " + self._tail(log).strip()[-400:])
                if port_is_open(port):
                    break
                time.sleep(0.1)
            else:
                terminate_tree(proc)
                raise ServiceError(500, "the service did not start listening within 12 seconds")
            self._proc = proc
            self._last_exit = None
            self.info = {"run_id": run_id, "port": port, "url": f"http://127.0.0.1:{port}/", "pid": proc.pid,
                         "started_at": time.strftime("%H:%M:%S"), "log": log}
        return self.status()

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

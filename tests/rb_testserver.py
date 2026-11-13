"""One way for tests to run a real research_brain server: a truly free port, a readiness check that it is
*our* server, and guaranteed teardown.

    with run_server(vault, env={...}) as srv:      # srv.base, srv.port, srv.proc, srv.log_path
        ...

- The port: the test binds 127.0.0.1:0 itself and hands the listening socket to uvicorn (`--fd`), so no
  other process can take the port between "found a free one" and "the server bound it".
- Ready: /api/status answers *and* reports this vault (a stray server on a reused port can't pass).
- Teardown: SIGTERM, then SIGKILL after a few seconds; on Linux the server also gets SIGKILL if the test
  process dies (PR_SET_PDEATHSIG), so a killed or timed-out run leaves no orphan servers behind.
"""
from __future__ import annotations

import contextlib
import json
import os
import re
import signal
import socket
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

# Requests the server makes to the internet go to a dead proxy, so they fail fast instead of hanging.
OFFLINE_ENV = {
    "HTTP_PROXY": "http://127.0.0.1:9", "HTTPS_PROXY": "http://127.0.0.1:9",
    "http_proxy": "http://127.0.0.1:9", "https_proxy": "http://127.0.0.1:9",
    "NO_PROXY": "127.0.0.1,localhost", "no_proxy": "127.0.0.1,localhost",
}


def free_socket() -> socket.socket:
    """A listening socket on a free 127.0.0.1 port (kept open, so the port stays ours)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(("127.0.0.1", 0))
    s.listen(128)
    return s


def _die_with_parent():  # runs in the child between fork and exec
    if sys.platform.startswith("linux"):
        try:
            import ctypes
            ctypes.CDLL("libc.so.6", use_errno=True).prctl(1, signal.SIGKILL)  # PR_SET_PDEATHSIG
        except Exception:
            pass


class Server:
    def __init__(self, proc, port, log_path):
        self.proc, self.port, self.log_path = proc, port, log_path
        self.base = f"http://127.0.0.1:{port}"

    def log(self) -> str:
        try:
            return Path(self.log_path).read_text(errors="replace")
        except OSError:
            return ""

    def stop(self, grace: float = 8.0) -> None:
        if self.proc.poll() is not None:
            return
        self.proc.terminate()
        try:
            self.proc.wait(grace)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(5)


@contextlib.contextmanager
def run_server(vault, *, env: dict | None = None, app: str = "app.server:app", cwd=None, log_path=None,
               offline: bool = True, startup_timeout: float = 45.0, graceful_timeout: int | None = 3):
    vault = Path(vault)
    log_path = Path(log_path or (vault.parent / f"server-{vault.name}.log"))
    sock = free_socket()
    port = sock.getsockname()[1]
    full_env = dict(os.environ)
    if offline:
        full_env.update(OFFLINE_ENV)
    full_env.update({"RB_VAULT": str(vault), "RB_FAKE_CLAUDE": "1", "RB_EMBEDDER": "tfidf", "RB_PORT": str(port),
                     "RB_UI_RESTORE": "0", "RB_DATA_DIR": str(vault.parent / "rb-data")})
    full_env.update(env or {})
    log = open(log_path, "w", encoding="utf-8")
    if os.name == "nt":  # Windows can't hand a socket to a child: let it bind the (just freed) port itself
        sock.close()
        args = [sys.executable, "-m", "uvicorn", app, "--host", "127.0.0.1", "--port", str(port), "--log-level", "warning"]
    else:
        args = [sys.executable, "-m", "uvicorn", app, "--fd", str(sock.fileno()), "--log-level", "warning"]
    if graceful_timeout is not None:  # a backstop: open responses can't hold up teardown for longer
        args += ["--timeout-graceful-shutdown", str(graceful_timeout)]
    try:
        proc = subprocess.Popen(
            args,
            cwd=str(cwd or ROOT), env=full_env, stdout=log, stderr=subprocess.STDOUT,
            **({"pass_fds": (sock.fileno(),), "preexec_fn": _die_with_parent} if os.name == "posix" else {}))
    finally:
        sock.close()  # the child holds its own copy of the listening socket
    srv = Server(proc, port, log_path)
    try:
        deadline = time.monotonic() + startup_timeout
        want = str(vault.resolve())
        while True:
            try:
                with urllib.request.urlopen(srv.base + "/api/status", timeout=2) as r:
                    got = json.load(r).get("vault", "")
                if str(Path(got).resolve()) != want:
                    raise RuntimeError(f"port {port} is answered by another server (vault {got})")
                break
            except OSError:
                if proc.poll() is not None or time.monotonic() > deadline:
                    raise RuntimeError(f"research_brain server didn't start:\n{srv.log()}")
                time.sleep(0.2)
        yield srv
    finally:
        srv.stop()
        log.close()


def note_file(vault, nid: str):
    """The file of note `nid` in the test library's documents folder (app/layout.py: Papers/, Notes/, ...,
    named by title), or None. Read from the folder the server recorded in state/library.json."""
    import json
    vault = Path(vault)
    try:
        docs = Path(json.loads((vault / "state" / "library.json").read_text(encoding="utf-8"))["documents"])
    except (OSError, ValueError, KeyError):
        docs = vault
    for f in docs.rglob("*.md"):
        if any(part.startswith(".") for part in f.relative_to(docs).parts):
            continue  # .trash and other hidden folders
        try:
            head = f.read_text(encoding="utf-8")[:4000]
        except OSError:
            continue
        if re.search(rf"(?m)^id: {re.escape(nid)}\s*$", head):
            return f
    return None


"""The interface every AI backend implements, plus the shared helpers CLI adapters use.

research_brain never needs an API key. Each backend is something the user already has on this computer:
an agent CLI they are signed in to (Claude Code, Codex CLI, Gemini CLI, GitHub Copilot CLI, OpenCode,
Cursor agent, ...) or a model server running on this machine (Ollama, LM Studio, any OpenAI-compatible
server on localhost).

Rules every backend must follow
-------------------------------
1. No tools, ever. The model answers with text only: no shell, no file access, no web, no MCP servers,
   no browser. Research mode does not give the model tools either: the app runs the loop itself
   (app/agents/research.py), the model only replies with small JSON actions, and the APP executes them
   against the library.
2. CLIs run in an empty private working directory (private_workdir(): a fresh 0700 folder under a
   cache folder with no group/world-writable folder above it), so no AGENTS.md / GEMINI.md / CLAUDE.md
   or project config can be planted next to it.
3. Scrubbed environment (clean_env()): variables that load code into the CLI (NODE_OPTIONS, ...) and the
   app's own RB_AGENT_* variables are never passed on; telemetry/autoupdate switched off where known.
4. No user or project config that could add tools, MCP servers, hooks or instructions, wherever the CLI
   allows switching that off (flags, an empty config dir, env vars). Where it can't be switched off, say
   so in the adapter's docstring and in SECURITY.md.
5. A hard timeout on every call; on timeout, cancel (the consumer closing the generator) or error, the
   whole process group is killed (StreamProcess / run_process do this).
6. Stream-parse the CLI's output and abort (kill + raise ToolGuardError) on any tool-use / tool-call
   event, wherever the CLI exposes one.
7. Errors the user should see are raised as AgentError with a plain-language message.
8. Local servers: loopback URLs only (127.0.0.1, localhost, ::1), and never send a `tools` field.

Adapters live in app/agents/<name>.py and call app.agents.register(Backend()) at import time.
"""
from __future__ import annotations

import glob
import os
import queue
import shutil
import signal
import subprocess
import sys
import tempfile
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Iterable, Iterator


@dataclass
class Detection:
    available: bool          # installed and runnable
    version: str = ""
    path: str = ""
    signed_in: bool | None = None   # None = can't tell cheaply
    note: str = ""           # plain-language hint, e.g. "Run `gemini` once and sign in."

    def as_dict(self) -> dict:
        return asdict(self)


class AgentError(RuntimeError):
    """A failure with a user-facing message in str(e)."""


class ToolGuardError(AgentError):
    """The no-tools lockdown was violated (the model tried to use a tool); the process was killed."""


class Backend:
    id: str = ""             # "claude", "codex", "gemini", "copilot", "opencode", "cursor", "ollama", "openai-local"
    name: str = ""           # "Claude Code", "Codex CLI", ...
    kind: str = "cli"        # "cli" or "local-server"
    home: str = ""           # URL for install instructions
    cost_note: str = ""      # short factual line, e.g. "Uses your ChatGPT plan." Keep claims cautious.

    def detect(self) -> Detection:
        raise NotImplementedError

    def ask(self, prompt: str, *, system: str = "", timeout: float = 180) -> str:
        """The whole answer as text. Default: collect ask_stream()."""
        return "".join(self.ask_stream(prompt, system=system, timeout=timeout)).strip()

    def ask_stream(self, prompt: str, *, system: str = "", timeout: float = 180) -> Iterator[str]:
        """Yield text chunks as they arrive. Default: the whole answer from ask() once.
        (A backend must override at least one of ask / ask_stream.)"""
        yield self.ask(prompt, system=system, timeout=timeout)

    def models(self) -> list[str]:
        return []

    def info(self) -> dict:
        """JSON-able description for /api/agents (detection included; never raises)."""
        try:
            det = self.detect()
        except Exception as e:  # a broken adapter must not break the settings page
            det = Detection(False, note=f"Couldn't check: {e}")
        return {"id": self.id, "name": self.name, "kind": self.kind, "home": self.home,
                "cost_note": self.cost_note, "detection": det.as_dict()}


# ---------------------------------------------------------------- prompt helpers
def combine(prompt: str, system: str = "") -> str:
    """For CLIs without a system-prompt flag: the instructions first, then the request."""
    if not system:
        return prompt
    return f"{system.strip()}\n\n---\n\n{prompt}"


# ---------------------------------------------------------------- finding executables
def find_executable(names: Iterable[str], extra: Iterable[str] = (), env_var: str = "") -> str | None:
    """First runnable match: $env_var, then each name on PATH, then common install folders (+ `extra`,
    which may contain globs and ~)."""
    home = Path.home()
    names = list(names)
    cands: list[str] = []
    if env_var and os.environ.get(env_var, "").strip():
        cands.append(os.path.expanduser(os.environ[env_var].strip()))
    cands += [shutil.which(n) or "" for n in names]
    dirs = [home / ".local/bin", Path("/opt/homebrew/bin"), Path("/usr/local/bin"), home / ".homebrew/bin",
            home / ".npm-global/bin", home / ".bun/bin", home / ".volta/bin", home / "bin"]
    for n in names:
        cands += [str(d / n) for d in dirs]
        cands += sorted(glob.glob(str(home / f".nvm/versions/node/*/bin/{n}")), reverse=True)
    for e in extra:
        cands += sorted(glob.glob(os.path.expanduser(e)), reverse=True)
    for p in cands:
        if p and os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    return None


def version_of(path: str, args: Iterable[str] = ("--version",), timeout: float = 20) -> tuple[bool, str]:
    """(ran ok, first line of output) for `path --version`, in the private workdir with a clean env."""
    try:
        code, out, err = run_process([path, *args], timeout=timeout)
    except AgentError as e:
        return False, str(e)
    text = (out or err).strip()
    return code == 0, (text.splitlines()[0] if text else "")


# ---------------------------------------------------------------- private working directory
def private_base() -> Path | None:
    """The private cache folder (shared with app/claude_cli.py): only this user can write it, and no
    folder above it is group/world-writable. None when that can't be guaranteed."""
    from .. import claude_cli
    return claude_cli._private_base()


def private_workdir(prefix: str = "agent_") -> Path:
    """A fresh, empty 0700 folder to run a CLI in (agents read instruction files from the working
    directory and the folders above it, so it must not sit in a shared folder such as /tmp)."""
    base = private_base()
    if base is None:
        d = Path(tempfile.mkdtemp(prefix=f"research_brain_{prefix}"))
    else:
        d = Path(tempfile.mkdtemp(prefix=prefix, dir=base))
    try:
        d.chmod(0o700)
    except OSError:
        pass
    return d


# ---------------------------------------------------------------- environment
ENV_DROP = ("NODE_OPTIONS", "NODE_PATH", "ELECTRON_RUN_AS_NODE", "PYTHONSTARTUP", "PYTHONPATH",
            "LD_PRELOAD", "DYLD_INSERT_LIBRARIES", "BASH_ENV", "ENV")
ENV_DROP_PREFIXES = ("RB_AGENT_",)
ENV_FORCE = {"DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1",
             "NO_UPDATE_NOTIFIER": "1", "DO_NOT_TRACK": "1", "NO_COLOR": "1", "TERM": "dumb", "CI": "1"}
# API-key variables an adapter may drop so the CLI uses the user's own sign-in, never a paid key.
API_KEY_VARS = ("ANTHROPIC_API_KEY", "OPENAI_API_KEY", "GEMINI_API_KEY", "GOOGLE_API_KEY", "CURSOR_API_KEY",
                "GITHUB_TOKEN", "GH_TOKEN", "COPILOT_GITHUB_TOKEN", "OPENROUTER_API_KEY")


def clean_env(drop: Iterable[str] = (), force: dict | None = None, keep_api_keys: bool = True) -> dict:
    """os.environ minus ENV_DROP, RB_AGENT_* and `drop`, plus ENV_FORCE and `force`. With
    keep_api_keys=False the API_KEY_VARS are removed too."""
    gone = set(ENV_DROP) | set(drop) | (set() if keep_api_keys else set(API_KEY_VARS))
    env = {k: v for k, v in os.environ.items() if k not in gone and not k.startswith(ENV_DROP_PREFIXES)}
    env.update(ENV_FORCE)
    if force:
        env.update(force)
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


# ---------------------------------------------------------------- processes
def _popen(cmd: list[str], cwd: Path | str | None, env: dict | None) -> subprocess.Popen:
    try:
        return subprocess.Popen(cmd, stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace",
                                cwd=str(cwd) if cwd else None, env=env if env is not None else clean_env(),
                                bufsize=1, start_new_session=(os.name == "posix"))
    except OSError as e:
        raise AgentError(f"Couldn't start {os.path.basename(cmd[0]) if cmd else 'the agent'}: {e}")


def kill_group(proc: subprocess.Popen) -> None:
    """Kill the process and everything it started (it leads its own session/process group)."""
    if proc.poll() is not None and os.name != "posix":
        return
    try:
        if os.name == "posix":
            os.killpg(proc.pid, signal.SIGKILL)
        else:
            proc.kill()
    except (OSError, ProcessLookupError):
        try:
            proc.kill()
        except OSError:
            pass
    try:
        proc.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass


class StreamProcess:
    """Run a command and iterate its stdout line by line, with a hard timeout.

        with StreamProcess(cmd, stdin=text, timeout=180) as sp:
            for line in sp.lines(heartbeat=None):
                ...                      # raise to abort: the group is killed on exit
        sp.returncode, sp.stderr, sp.timed_out

    lines() yields "" every `heartbeat` seconds without output (when given). On timeout the process group
    is killed and lines() raises AgentError. Leaving the `with` block (normally, by exception, or because
    the consumer closed its generator) kills the group if it is still running. A temporary working
    directory is created (and removed afterwards) unless `cwd` is given."""

    def __init__(self, cmd: list[str], *, stdin: str = "", timeout: float = 180, cwd: Path | str | None = None,
                 env: dict | None = None, label: str = ""):
        self.cmd, self.stdin, self.timeout, self.env = cmd, stdin, float(timeout), env
        self.label = label or (os.path.basename(cmd[0]) if cmd else "agent")
        self._own_cwd = cwd is None
        self.cwd = Path(cwd) if cwd else None
        self.proc: subprocess.Popen | None = None
        self.returncode: int | None = None
        self.stderr = ""
        self.timed_out = False
        self._q: queue.Queue = queue.Queue()
        self._err: list[str] = []
        self._threads: list[threading.Thread] = []

    def __enter__(self) -> "StreamProcess":
        if self.cwd is None:
            self.cwd = private_workdir("run_")
        self.proc = _popen(self.cmd, self.cwd, self.env)
        p = self.proc

        def feed():
            try:
                if self.stdin:
                    p.stdin.write(self.stdin)
            except (BrokenPipeError, OSError, ValueError):
                pass
            finally:
                try:
                    p.stdin.close()
                except (BrokenPipeError, OSError, ValueError):
                    pass

        def pump():
            try:
                for line in p.stdout:
                    self._q.put(line)
            except (OSError, ValueError):
                pass
            finally:
                self._q.put(None)

        def drain():
            try:
                self._err.append(p.stderr.read())
            except (OSError, ValueError):
                pass

        self._threads = [threading.Thread(target=f, daemon=True) for f in (feed, pump, drain)]
        for t in self._threads:
            t.start()
        self._deadline = time.monotonic() + self.timeout
        return self

    def lines(self, heartbeat: float | None = None) -> Iterator[str]:
        while True:
            left = self._deadline - time.monotonic()
            if left <= 0:
                self.timed_out = True
                self.kill()
                raise AgentError(f"{self.label} took longer than {int(self.timeout)}s and was stopped. Try again.")
            wait = min(left, heartbeat) if heartbeat else min(left, 1.0)
            try:
                line = self._q.get(timeout=wait)
            except queue.Empty:
                if heartbeat:
                    yield ""
                continue
            if line is None:
                break
            yield line.rstrip("\r\n")
        self._finish()

    def _finish(self) -> None:
        p = self.proc
        try:
            p.wait(timeout=max(1.0, min(10.0, self._deadline - time.monotonic())))
        except subprocess.TimeoutExpired:
            self.kill()
        for t in self._threads[1:]:
            t.join(timeout=5)
        self.returncode = p.returncode
        self.stderr = "".join(self._err).strip()

    def kill(self) -> None:
        if self.proc is not None:
            kill_group(self.proc)

    def __exit__(self, *exc) -> None:
        if self.proc is not None and self.proc.poll() is None:
            self.kill()
        if self.proc is not None and self.returncode is None:
            self.returncode = self.proc.returncode
            self.stderr = self.stderr or "".join(self._err).strip()
        if self._own_cwd and self.cwd is not None:
            shutil.rmtree(self.cwd, ignore_errors=True)


def stream_lines(cmd: list[str], *, stdin: str = "", timeout: float = 180, cwd: Path | str | None = None,
                 env: dict | None = None, state: dict | None = None, heartbeat: float | None = None) -> Iterator[str]:
    """Generator form of StreamProcess: yields stdout lines; afterwards `state` holds returncode, stderr,
    timed_out. Closing the generator early kills the process group."""
    with StreamProcess(cmd, stdin=stdin, timeout=timeout, cwd=cwd, env=env) as sp:
        try:
            yield from sp.lines(heartbeat)
        finally:
            if state is not None:
                state.update(returncode=sp.returncode, stderr=sp.stderr, timed_out=sp.timed_out)
    if state is not None:
        state.update(returncode=sp.returncode, stderr=sp.stderr, timed_out=sp.timed_out)


def run_process(cmd: list[str], *, stdin: str = "", timeout: float = 60, cwd: Path | str | None = None,
                env: dict | None = None) -> tuple[int | None, str, str]:
    """Run to completion: (returncode, stdout, stderr). Raises AgentError on timeout (group killed)."""
    out: list[str] = []
    with StreamProcess(cmd, stdin=stdin, timeout=timeout, cwd=cwd, env=env) as sp:
        for line in sp.lines():
            out.append(line)
    return sp.returncode, "\n".join(out), sp.stderr


def is_windows() -> bool:
    return sys.platform == "win32"

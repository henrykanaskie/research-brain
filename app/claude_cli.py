"""Thin wrapper around the local `claude` CLI in print mode (`claude -p`).

Runs on your Claude subscription through the CLI's own login; no API key needed.
Each call: instruction as the -p argument, long context (paper text, notes) on stdin,
all tools disabled, custom system prompt, no saved session, run from an empty
directory so no CLAUDE.md or project settings get pulled in.

Tool guard: every call runs with --output-format stream-json, whose first event (`system`/`init`)
lists the tools the session really has. check_init() compares that list with what the call is
allowed (nothing for ask/ask_stream; only our mcp__rb__* tools for run_agent). Anything extra, or
no init event before output, and the process is killed and ToolGuardError raised. This matters
because some integrations (e.g. "claude-in-chrome", source "dynamic") attach to a session even
with --strict-mcp-config.
"""
from __future__ import annotations

import glob
import json
import os
import queue
import shutil
import subprocess
import sys
import tempfile
import threading
import time
from pathlib import Path

TIMEOUT = int(os.environ.get("RB_CLAUDE_TIMEOUT", "240"))
MODEL = os.environ.get("RB_CLAUDE_MODEL", "").strip()
MAX_PARALLEL = int(os.environ.get("RB_CLAUDE_PARALLEL", "2"))

_sema = threading.Semaphore(MAX_PARALLEL)


def _private_base() -> Path | None:
    """A cache folder only this user can write, with no folder above it that other users can write.
    Claude Code reads CLAUDE.md files from the working directory *and every folder above it*, so the
    working directory must not sit under a shared folder such as /tmp, where another local user could
    plant /tmp/CLAUDE.md (prompt injection) or project settings."""
    env = os.environ.get("RB_CLAUDE_WORKDIR", "").strip()
    home = Path.home()
    if env:
        base = Path(env).expanduser()
    elif sys.platform == "darwin":
        base = home / "Library" / "Caches" / "research_brain"
    else:
        base = Path(os.environ.get("XDG_CACHE_HOME", "").strip() or home / ".cache") / "research_brain"
    try:
        base.mkdir(parents=True, exist_ok=True, mode=0o700)
        base.chmod(0o700)
        uid = os.getuid() if hasattr(os, "getuid") else None
        for d in [base, *base.resolve().parents]:
            st = d.stat()
            if uid is not None and st.st_uid not in (uid, 0):
                return None
            if st.st_mode & 0o022 and d != base:  # group/world-writable folder above us
                return None
        return base
    except OSError:
        return None


def _make_workdir() -> Path:
    base = _private_base()
    if base is None:  # e.g. an unusual HOME: still private (mkdtemp is 0700), and --setting-sources "" applies
        return Path(tempfile.mkdtemp(prefix="research_brain_claude_"))
    return Path(tempfile.mkdtemp(prefix="claude_", dir=base))


_workdir = _make_workdir()
_strict_flags_ok: bool | None = None  # learned at first call: does this CLI accept our extra flags?


class ClaudeError(RuntimeError):
    pass


def _candidates() -> list[str]:
    from . import procs
    home = Path.home()
    paths = [os.environ.get("RB_CLAUDE_BIN", ""), shutil.which("claude") or ""]
    for nm in procs.exe_names("claude"):
        paths += [str(home / ".claude/local" / nm)] + [str(d / nm) for d in procs.install_dirs()]
        paths += sorted(glob.glob(str(home / f".nvm/versions/node/*/bin/{nm}")), reverse=True)
    return [p for p in paths if p]


def find_claude() -> str | None:
    from . import procs
    for p in _candidates():
        if procs.runnable(p):
            return p
    return None


def status() -> dict:
    if os.environ.get("RB_FAKE_CLAUDE"):
        return {"available": True, "path": "fake", "detail": "Fake Claude (testing mode)"}
    path = find_claude()
    if not path:
        return {"available": False, "path": None,
                "detail": "Couldn't find the `claude` CLI. Install Claude Code, run `claude` once to log in, "
                          "or set RB_CLAUDE_BIN to its full path."}
    try:
        out = subprocess.run([path, "--version"], capture_output=True, text=True, timeout=20)
        ver = (out.stdout or out.stderr).strip().splitlines()[0] if (out.stdout or out.stderr) else ""
        if out.returncode != 0:
            return {"available": False, "path": path, "detail": f"`claude --version` failed: {ver}"}
        return {"available": True, "path": path, "detail": f"Claude Code {ver}"}
    except (OSError, subprocess.TimeoutExpired) as e:
        return {"available": False, "path": path, "detail": f"Couldn't run claude: {e}"}


def _fake(instruction: str, context: str) -> str:
    head = instruction.strip().splitlines()[0][:120]
    return (f"**(fake Claude)** Responding to: _{head}_\n\n"
            f"- Context was {len(context)} characters.\n- Inline math check: $E = mc^2$\n\n"
            "$$\\nabla \\cdot \\mathbf{E} = \\frac{\\rho}{\\varepsilon_0}$$")


def ask(instruction: str, context: str = "", system: str = "", model: str | None = None) -> str:
    """Run one Claude call and return its text. Raises ClaudeError with a readable message.
    Collects ask_stream(), so it gets the same safety flags and the same init-event tool guard."""
    if os.environ.get("RB_FAKE_CLAUDE"):
        return _fake(instruction, context)
    return "".join(ask_stream(instruction, context, system, model=model)).strip()


def _error(last_err: str) -> ClaudeError:
    """Turn the CLI's stderr/output into a readable error (shared by ask and ask_stream)."""
    low = last_err.lower()
    if "login" in low or "auth" in low or "/login" in low:
        return ClaudeError("Claude CLI isn't logged in. Open Terminal, run `claude`, and log in with your Claude account. "
                           f"(CLI said: {last_err[:300]})")
    if "limit" in low and ("usage" in low or "rate" in low):
        return ClaudeError(f"Claude usage limit reached: {last_err[:300]}")
    return ClaudeError(f"Claude CLI error: {last_err[:500]}")


# Environment variables that could load code into the CLI or change what it may do; never passed on.
# (RB_AGENT_* too: the MCP bridge gets its URL and token from the generated --mcp-config only.)
ENV_DROP = ("NODE_OPTIONS", "NODE_PATH", "ELECTRON_RUN_AS_NODE")
# Set on every call: no claude.ai connectors (MCP servers from your account), no telemetry/autoupdate traffic.
ENV_FORCE = {"ENABLE_CLAUDEAI_MCP_SERVERS": "false", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1",
             "DISABLE_TELEMETRY": "1", "DISABLE_ERROR_REPORTING": "1", "DISABLE_AUTOUPDATER": "1"}


def _env() -> dict:
    env = {k: v for k, v in os.environ.items() if k not in ENV_DROP and not k.startswith("RB_AGENT_")}
    if os.environ.get("RB_USE_SUBSCRIPTION", "1") == "1":
        env.pop("ANTHROPIC_API_KEY", None)
    env.update(ENV_FORCE)
    env.setdefault("PATH", "/usr/bin:/bin")
    return env


# ---------------------------------------------------------------- tool guard
class ToolGuardError(ClaudeError):
    """The Claude session had a tool it must not have; the process was killed before it ran."""


# Integrations that can attach to a session on their own (source "dynamic"), denied by name on every
# call. --no-chrome turns the Chrome one off; the deny list is the second layer; check_init the third.
DYNAMIC_DENY = ("mcp__claude-in-chrome__*", "mcp__Claude_in_Chrome__*", "mcp__Claude_Browser__*",
                "mcp__computer-use__*", "mcp__ide__*")
NO_DYNAMIC = ["--no-chrome"]


def check_init(ev: dict, allowed: set[str] | frozenset[str] = frozenset(), server: str | None = None) -> None:
    """Raise ToolGuardError unless the init event's `tools` are all in `allowed` (and, when `server` is
    given, that MCP server is connected). `tools` missing or not a list counts as a failure."""
    tools = ev.get("tools")
    if not isinstance(tools, list):
        raise ToolGuardError("Stopped Claude: it didn't report which tools it had, so research_brain couldn't "
                             "check them. Update Claude Code (`claude update`) and try again.")
    extra = sorted({str(t) for t in tools} - set(allowed))
    if extra:
        shown = ", ".join(extra[:6]) + (f" and {len(extra) - 6} more" if len(extra) > 6 else "")
        raise ToolGuardError(f"Stopped Claude before it did anything: the session had tools research_brain "
                             f"doesn't allow ({shown}). Nothing was run. If this keeps happening, check for "
                             "Claude integrations that attach on their own (for example Claude in Chrome).")
    if server:
        st = {str(m.get("name")): m.get("status") for m in ev.get("mcp_servers") or [] if isinstance(m, dict)}
        if st.get(server) not in ("connected", None) or server not in st:
            raise ToolGuardError(f"The research_brain tool server didn't start (status: {st.get(server) or 'missing'}). "
                                 "Check the server log and try again.")


# ---------------------------------------------------------------- streaming
_stream_strict_ok: bool | None = None  # like _strict_flags_ok, for the stream-json command line
_UNKNOWN_FLAG = ("unknown option", "unknown argument", "unexpected argument")


def _fake_stream(instruction: str, context: str):
    text = _fake(instruction, context)
    delay = float(os.environ.get("RB_FAKE_STREAM_DELAY", "0.02"))
    step = max(1, len(text) // 5)
    for i in range(0, len(text), step):
        if delay:
            time.sleep(delay)
        yield text[i:i + step]


def parse_stream_line(line: str, state: dict) -> str:
    """One line of `claude -p --output-format stream-json --verbose --include-partial-messages` output.
    Returns the new text it contributes ("" for none) and updates `state`:
      emitted  all text yielded so far     result  final text from the `result` event
      error    an error message, if the run failed
    Event shapes (observed with Claude Code 2.1):
      {"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"..."}}}
      {"type":"assistant","message":{"model":"...","content":[{"type":"text","text":"full text"}]}}
      {"type":"result","subtype":"success","is_error":false,"result":"full text"}
    A failed run (bad model, auth, limits) sends an assistant message with model "<synthetic>" holding
    the error text, then a result with is_error true. Lines that aren't JSON are ignored."""
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, TypeError):
        return ""
    if not isinstance(ev, dict):
        return ""
    t = ev.get("type")
    emitted = state.setdefault("emitted", "")
    if t == "system" and ev.get("subtype") == "init":
        state["init"] = ev
        return ""
    if t in ("stream_event", "assistant", "user") and "init" not in state:
        state["no_init"] = True  # output before the tool list: the guard fails closed
    if t == "stream_event":
        e = ev.get("event") or {}
        if e.get("type") == "content_block_delta":
            d = e.get("delta") or {}
            if d.get("type") == "text_delta" and d.get("text"):
                state["partials"] = True
                state["emitted"] = emitted + d["text"]
                return d["text"]
        return ""
    if t == "assistant":
        msg = ev.get("message") or {}
        text = "".join(c.get("text", "") for c in (msg.get("content") or [])
                       if isinstance(c, dict) and c.get("type") == "text")
        if msg.get("model") == "<synthetic>":  # CLI-generated error text, not an answer
            state["error"] = text or state.get("error", "")
            return ""
        if state.get("partials"):
            return ""  # already streamed delta by delta
        state["emitted"] = emitted + text  # CLI without partial messages: one message at a time
        return text
    if t == "result":
        res = ev.get("result")
        if ev.get("is_error") or (ev.get("subtype") not in (None, "success")):
            state["error"] = (res if isinstance(res, str) and res else "") or state.get("error") \
                or str(ev.get("subtype") or "error")
            return ""
        if isinstance(res, str):
            state["result"] = res
            if res.startswith(emitted) and len(res) > len(emitted):  # anything the deltas missed
                state["emitted"] = res
                return res[len(emitted):]
        return ""
    return ""


def ask_stream(instruction: str, context: str = "", system: str = "", heartbeat: float | None = None,
               model: str | None = None):
    """Like ask(), but yields the answer's text as it arrives. Same safety flags (no tools, no MCP,
    no saved session, empty working directory), same timeout and error messages (raises ClaudeError).
    With `heartbeat`, yields "" whenever no text has arrived for that many seconds (lets an HTTP stream
    send keep-alives). If an older CLI rejects the extra flags, retries with only `--tools ""`;
    it never falls back to a call with tools enabled, and the init-event guard (no tools at all) applies
    to both command lines."""
    global _stream_strict_ok
    if os.environ.get("RB_FAKE_CLAUDE"):
        yield from _fake_stream(instruction, context)
        return
    path = find_claude()
    if not path:
        raise ClaudeError(status()["detail"])
    base = [path, "-p", instruction, "--output-format", "stream-json", "--verbose"]
    if model or MODEL:
        base += ["--model", model or MODEL]
    strict = ["--include-partial-messages", "--tools", "", "--disallowedTools", "mcp__*", *DYNAMIC_DENY,
              "--strict-mcp-config", *NO_DYNAMIC, "--permission-mode", "dontAsk", "--no-session-persistence",
              "--disable-slash-commands", "--setting-sources", ""]  # no skills, settings, hooks or CLAUDE.md
    if system:
        strict += ["--system-prompt", system]
    minimal = base + ["--tools", ""]
    attempts = [base + strict, minimal] if _stream_strict_ok is not False else [minimal]
    env = _env()
    with _sema:
        for i, cmd in enumerate(attempts):
            stdin = f"{system}\n\n{context}" if (cmd is minimal and system) else context
            state: dict = {}
            got_text = False
            for piece in _run_stream(cmd, stdin, env, state, heartbeat):
                if piece:
                    got_text = True
                yield piece
            if state.get("ok") and not state.get("error"):
                if i == 0 and len(attempts) == 2:
                    _stream_strict_ok = True
                if not got_text and not state.get("emitted"):
                    raise ClaudeError("Claude CLI error: the answer was empty")
                return
            last_err = state.get("error") or state.get("stderr") or f"exit code {state.get('code')}"
            unknown_flag = any(s in last_err.lower() for s in _UNKNOWN_FLAG)
            if i == 0 and len(attempts) == 2 and unknown_flag and not got_text:
                _stream_strict_ok = False
                continue
            raise _error(last_err)


def _run_stream(cmd: list[str], stdin: str, env: dict, state: dict, heartbeat: float | None):
    """Run one stream-json CLI process, yielding text deltas. Sets state ok/code/stderr/error."""
    from . import procs
    try:
        proc = subprocess.Popen(procs.resolve(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                text=True, encoding="utf-8", errors="replace", cwd=_workdir, env=env, bufsize=1,
                                **procs.popen_kwargs())
    except procs.UnsafeCommand as e:
        raise ClaudeError(str(e))
    except OSError as e:
        raise ClaudeError(f"Couldn't start claude: {e}")
    proc._rb_group = True
    lines: queue.Queue = queue.Queue()
    err_buf: list[str] = []
    timed_out = threading.Event()

    def feed():
        try:
            proc.stdin.write(stdin)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    def drain_err():
        try:
            err_buf.append(proc.stderr.read())
        except (OSError, ValueError):
            pass

    def kill():
        timed_out.set()
        proc.kill()

    workers = [threading.Thread(target=f, daemon=True) for f in (feed, pump, drain_err)]
    state["proc"] = proc
    for w in workers:
        w.start()
    timer = threading.Timer(TIMEOUT, kill)
    timer.daemon = True
    timer.start()
    try:
        while True:
            try:
                line = lines.get(timeout=heartbeat) if heartbeat else lines.get()
            except queue.Empty:
                yield ""
                continue
            if line is None:
                break
            piece = parse_stream_line(line, state)
            if "init" in state and not state.get("guarded"):
                state["guarded"] = True
                try:
                    check_init(state["init"], frozenset())
                except ToolGuardError:
                    _kill(proc)
                    raise
            if state.get("no_init"):
                _kill(proc)
                raise ToolGuardError("Stopped Claude: it produced output before reporting its tools, so "
                                     "research_brain couldn't check them. Update Claude Code and try again.")
            if piece:
                yield piece
        proc.wait(timeout=10)
        for w in workers[1:]:
            w.join(timeout=5)
        if timed_out.is_set():
            raise ClaudeError(f"Claude took longer than {TIMEOUT}s. Try again, or set RB_CLAUDE_TIMEOUT higher.")
        state["code"] = proc.returncode
        state["stderr"] = "".join(err_buf).strip()
        state["ok"] = proc.returncode == 0
        if proc.returncode != 0 and not state.get("error") and state.get("emitted"):
            state["error"] = state["stderr"] or f"exit code {proc.returncode}"
    finally:
        timer.cancel()
        if proc.poll() is None:  # consumer stopped early (client went away): don't leave claude running
            proc.kill()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                pass


def _kill(proc) -> None:
    """Kill a CLI process and, when it leads its own process group, its children (the MCP server)."""
    from . import procs
    if os.name == "posix" and getattr(proc, "_rb_group", False):
        try:
            os.killpg(proc.pid, 9)
        except (OSError, ProcessLookupError):
            pass
    procs.kill_tree(proc)


# ---------------------------------------------------------------- agent (Research mode)
# run_agent() is the only call that gives Claude tools, and only ours: one MCP server ("rb", our
# app/agent_mcp.py) whose tools read the library (plus `remember`, which appends to findings.md).
#   --tools ""                 no built-in tools (no Bash, files, web, browser)
#   --mcp-config <ours> --strict-mcp-config   only our MCP server is loaded
#   --no-chrome                Claude in Chrome off (it attached itself in testing despite strict mode)
#   --disallowedTools ...      every other MCP server's tools denied by name
#   --allowedTools <ours>      our tools pre-approved
#   --permission-mode dontAsk  anything not pre-approved is denied, never prompted (verified with
#                              Claude Code 2.1: an unlisted tool call returns "Permission ... denied")
#   --disable-slash-commands --setting-sources ""   no skills, no user/project settings or hooks
#   --no-session-persistence, --max-turns N, empty working directory
# and then check_init() on the init event: any tool outside `allowed` kills the run before turn 1.
AGENT_SERVER = "rb"
AGENT_PREFIX = f"mcp__{AGENT_SERVER}__"
AGENT_MODEL = os.environ.get("RB_AGENT_MODEL", "").strip()


def agent_command(path: str, instruction: str, mcp_config: str, allowed: list[str], system: str,
                  max_turns: int, model: str | None = None, effort: str | None = None) -> list[str]:
    cmd = [path, "-p", instruction, "--output-format", "stream-json", "--verbose", "--include-partial-messages",
           "--tools", "", "--mcp-config", mcp_config, "--strict-mcp-config", *NO_DYNAMIC,
           "--disallowedTools", *DYNAMIC_DENY,
           "--allowedTools", *allowed,
           "--permission-mode", "dontAsk", "--disable-slash-commands", "--setting-sources", "",
           "--no-session-persistence", "--max-turns", str(max_turns), "--system-prompt", system]
    if model:
        cmd += ["--model", model]
    if effort:
        cmd += ["--effort", effort]
    return cmd


def run_agent(instruction: str, context: str, system: str, mcp_config: dict, allowed: list[str],
              max_turns: int = 8, budget: float = 45.0, model: str | None = None, effort: str | None = None,
              grace: float = 25.0):
    """Run Claude as an agent with only `allowed` tools (all must start with mcp__rb__). Yields events:
      {"type": "init", "tools": [...], "server_ok": bool}
      {"type": "tool_use", "id", "name", "input"}            a tool call Claude made
      {"type": "tool_result", "id", "is_error", "text"}       what the tool returned (or the denial)
      {"type": "delta", "text"}                               answer text as it streams
      {"type": "reset"}                                       text so far was a preamble before a tool call
      {"type": "end", "reason": "done"|"max_turns"|"budget", "text", "turns", "tool_calls"}
    Raises ToolGuardError (process killed) if the session has any other tool, ClaudeError otherwise.
    `budget` is wall-clock seconds; when it runs out while Claude is still calling tools the run is
    stopped (reason "budget"); a final answer already being written gets `grace` more seconds."""
    bad = [t for t in allowed if not t.startswith(AGENT_PREFIX) or "*" in t]
    if bad:
        raise ToolGuardError(f"refusing to allow non-research_brain tools: {bad}")
    path = find_claude()
    if not path:
        raise ClaudeError(status()["detail"])
    allowed_set = frozenset(allowed)
    cfg_dir = Path(tempfile.mkdtemp(prefix="rb_agent_", dir=_workdir))
    cfg = cfg_dir / "mcp.json"
    cfg.write_text(json.dumps(mcp_config), encoding="utf-8", newline="\n")
    try:
        cfg.chmod(0o600)
    except OSError:
        pass
    cmd = agent_command(path, instruction, str(cfg), list(allowed), system, max_turns,
                        model or AGENT_MODEL or MODEL or None, effort)
    env = _env()
    with _sema:
        try:
            from . import procs
            proc = subprocess.Popen(procs.resolve(cmd), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                    stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
                                    cwd=cfg_dir, env=env, bufsize=1, **procs.popen_kwargs())
        except (OSError, ValueError) as e:  # ValueError: procs.UnsafeCommand
            shutil.rmtree(cfg_dir, ignore_errors=True)
            raise ClaudeError(f"Couldn't start claude: {e}")
        proc._rb_group = True
        try:
            yield from _agent_events(proc, context, allowed_set, budget, grace)
        finally:
            if proc.poll() is None:
                _kill(proc)
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass
            shutil.rmtree(cfg_dir, ignore_errors=True)


def _agent_events(proc, context: str, allowed: frozenset, budget: float, grace: float):
    lines: queue.Queue = queue.Queue()
    err_buf: list[str] = []

    def feed():
        try:
            proc.stdin.write(context)
        except (BrokenPipeError, OSError, ValueError):
            pass
        finally:
            try:
                proc.stdin.close()
            except (BrokenPipeError, OSError, ValueError):
                pass

    def pump():
        try:
            for line in proc.stdout:
                lines.put(line)
        except (OSError, ValueError):
            pass
        finally:
            lines.put(None)

    def drain_err():
        try:
            err_buf.append(proc.stderr.read())
        except (OSError, ValueError):
            pass

    for f in (feed, pump, drain_err):
        threading.Thread(target=f, daemon=True).start()
    t0 = time.monotonic()
    hard_stop = t0 + TIMEOUT
    inited = False
    tool_calls = 0
    turns = 0
    msg_text = ""          # text streamed in the current assistant message
    msg_has_tool = False   # current message started a tool_use block
    last_text = ""         # text of the last finished assistant message
    final_text = ""
    error = ""
    reason = "done"
    while True:
        now = time.monotonic()
        writing_answer = bool(msg_text) and not msg_has_tool
        deadline = min(hard_stop, t0 + budget + (grace if writing_answer else 0))
        if now >= deadline:
            reason = "budget"
            _kill(proc)
            break
        try:
            line = lines.get(timeout=min(1.0, max(0.05, deadline - now)))
        except queue.Empty:
            yield {"type": "tick"}
            continue
        if line is None:
            break
        try:
            ev = json.loads(line)
        except (json.JSONDecodeError, TypeError):
            continue
        if not isinstance(ev, dict):
            continue
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            try:
                check_init(ev, allowed)
            except ToolGuardError:
                _kill(proc)
                raise
            inited = True
            tools = list(ev.get("tools") or [])
            st = {str(m.get("name")): m.get("status") for m in ev.get("mcp_servers") or [] if isinstance(m, dict)}
            yield {"type": "init", "tools": tools, "server_ok": st.get(AGENT_SERVER) == "connected" and bool(tools)}
            continue
        if t in ("stream_event", "assistant", "user") and not inited:
            _kill(proc)
            raise ToolGuardError("Stopped Claude: it produced output before reporting its tools, so research_brain "
                                 "couldn't check them. Update Claude Code (`claude update`) and try again.")
        if t == "stream_event":
            e = ev.get("event") or {}
            et = e.get("type")
            if et == "message_start":
                msg_text, msg_has_tool = "", False
            elif et == "content_block_start" and (e.get("content_block") or {}).get("type") == "tool_use":
                if msg_text and not msg_has_tool:
                    yield {"type": "reset"}
                msg_has_tool = True
            elif et == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    msg_text += d["text"]
                    if not msg_has_tool:
                        yield {"type": "delta", "text": d["text"]}
            continue
        if t == "assistant":
            msg = ev.get("message") or {}
            if msg.get("model") == "<synthetic>":
                error = "".join(c.get("text", "") for c in msg.get("content") or [] if isinstance(c, dict)) or error
                continue
            for c in msg.get("content") or []:
                if not isinstance(c, dict):
                    continue
                if c.get("type") == "tool_use":
                    name = str(c.get("name") or "")
                    if name not in allowed:  # can't happen after check_init; fail closed anyway
                        _kill(proc)
                        raise ToolGuardError(f"Stopped Claude: it tried to call {name}, which research_brain doesn't allow.")
                    tool_calls += 1
                    yield {"type": "tool_use", "id": c.get("id"), "name": name, "input": c.get("input") or {}}
                elif c.get("type") == "text" and c.get("text"):
                    last_text = c["text"]
            continue
        if t == "user":
            turns += 1
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    content = c.get("content")
                    if isinstance(content, list):
                        content = "\n".join(str(x.get("text", "")) for x in content if isinstance(x, dict))
                    yield {"type": "tool_result", "id": c.get("tool_use_id"), "is_error": bool(c.get("is_error")),
                           "text": str(content or "")}
            continue
        if t == "result":
            sub = ev.get("subtype")
            if sub == "error_max_turns" or ev.get("terminal_reason") == "max_turns":
                reason = "max_turns"
            elif ev.get("is_error") or sub not in (None, "success"):
                error = (ev.get("result") if isinstance(ev.get("result"), str) else "") or error or str(sub)
            elif isinstance(ev.get("result"), str):
                final_text = ev["result"]
            turns = int(ev.get("num_turns") or turns)
    try:
        proc.wait(timeout=10)
    except subprocess.TimeoutExpired:
        _kill(proc)
    if not inited and reason != "budget":
        err = "".join(err_buf).strip()
        raise _error(error or err or f"exit code {proc.returncode}")
    if error and reason == "done" and not final_text:
        raise _error(error)
    yield {"type": "end", "reason": reason, "text": final_text or (last_text if reason == "done" else ""),
           "turns": turns, "tool_calls": tool_calls}

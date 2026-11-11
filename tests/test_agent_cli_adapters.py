"""Locked-down agent-CLI adapters (app/agents/{codex,gemini,copilot,opencode,qwen,cursor}.py).

Three layers:
  1. FAKE executables (a small Python script per CLI on a temp path) that print each CLI's real output
     format and record how they were started: exact argv (lockdown flags present, no auto-approve flag),
     scrubbed environment, private working folder, stdin, streaming parse, timeout kill of the whole
     process group, tool event -> ToolGuardError (process killed), sign-in detection.
  2. --help checks against the REAL CLIs when installed (skipped otherwise): every flag the lockdown relies
     on must still exist, so a CLI release that drops or renames one is caught.
  3. Live checks against the REAL CLIs when installed (skipped otherwise, or with RB_TEST_REAL_CLIS=0): the
     adapter's exact command line, pointed at a local mock model server (each CLI's own "custom endpoint"
     setting, injected by the test only), with a planted user config (hooks, MCP servers, instruction
     files). The test reads the request the CLI sent: no tools offered, our system prompt, no hook ran,
     no MCP server started.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app import agents  # noqa: E402
from app.agents import base  # noqa: E402

IDS = ("codex", "gemini", "copilot", "opencode", "qwen", "cursor")
BIN = {"codex": "codex", "gemini": "gemini", "copilot": "copilot", "opencode": "opencode", "qwen": "qwen",
       "cursor": "cursor-agent"}
FORBIDDEN = {"--yolo", "-y", "--dangerously-bypass-approvals-and-sandbox", "--approve-for-me", "--allow-all",
             "--allow-all-tools", "--allow-all-paths", "--allow-all-urls", "--allow-tool", "--auto", "--force", "-f",
             "--approve-mcps", "--full-auto", "--dangerously-bypass-hook-trust", "--assisted-approval", "--autopilot",
             "--enable-all-github-mcp-tools", "--add-dir", "--include-directories", "--raw-output", "--sandbox=disabled"}
FORBIDDEN_VALUES = {"yolo", "auto_edit", "auto-edit", "danger-full-access", "workspace-write", "full-auto"}

# What each adapter's command line must contain (flag, value-or-None).
REQUIRED = {
    "codex": [("exec", None), ("--json", None), ("--strict-config", None), ("--ephemeral", None),
              ("--ignore-user-config", None), ("--ignore-rules", None), ("--skip-git-repo-check", None),
              ("--sandbox", "read-only"), ("-c", 'approval_policy="never"'), ("-c", 'web_search="disabled"'),
              ("-c", "project_doc_max_bytes=0"), ("-c", "mcp_servers={}"), ("-c", "skills.bundled.enabled=false"),
              ("-c", 'history.persistence="none"'), ("--disable", "shell_tool"), ("--disable", "unified_exec"),
              ("--disable", "view_image"), ("--disable", "multi_agent"), ("--disable", "apps"),
              ("--disable", "plugins"), ("--disable", "hooks"), ("--disable", "browser_use"),
              ("--disable", "computer_use"), ("--disable", "image_generation")],
    "gemini": [("--skip-trust", None), ("--approval-mode", "default"), ("--extensions", "none"),
               ("--allowed-mcp-server-names", "__rb_no_mcp__"), ("--output-format", "stream-json"),
               ("--admin-policy", None), ("-p", "")],
    "copilot": [("--output-format", "json"), ("--stream", "on"), ("--available-tools", "__rb_no_tools__"),
                ("--no-custom-instructions", None), ("--disable-builtin-mcps", None), ("--no-ask-user", None),
                ("--disallow-temp-dir", None), ("--disable-client-telemetry", None), ("--no-auto-update", None),
                ("--trust-folder", None), ("--deny-tool", "shell"), ("--deny-tool", "write"), ("--deny-tool", "read"),
                ("--deny-tool", "url"), ("--deny-tool", "memory"), ("--log-level", "none")],
    "opencode": [("run", None), ("--pure", None), ("--agent", "rb"), ("--format", "json")],
    "qwen": [("--output-format", "stream-json"), ("--include-partial-messages", None), ("--safe-mode", None),
             ("--chat-recording", "false"), ("--exclude-tools", None), ("--max-tool-calls", "0"),
             ("--max-session-turns", "1"), ("--approval-mode", "default"),
             ("--allowed-mcp-server-names", "__rb_no_mcp__"), ("--extensions", "none"), ("--system-prompt", None)],
    "cursor": [("--print", None), ("--output-format", "stream-json"), ("--stream-partial-output", None),
               ("--mode", "ask"), ("--sandbox", "enabled"), ("--trust", None), ("--workspace", None)],
}

# Variables that must never reach the CLI (set in the parent for every fake run).
POISON = {"NODE_OPTIONS": "--require /tmp/evil.js", "RB_AGENT_TOKEN": "secret-token", "LD_PRELOAD": "/tmp/x.so",
          "BASH_ENV": "/tmp/evil.sh"}
POISON_PER = {
    "codex": {"OPENAI_API_KEY": "sk-paid", "CODEX_API_KEY": "sk-paid2"},
    "gemini": {"GEMINI_CLI_SYSTEM_SETTINGS_PATH": "/tmp/evil.json", "GEMINI_SANDBOX": "false",
               "GEMINI_SYSTEM_MD": "/tmp/evil.md"},
    "copilot": {"COPILOT_ALLOW_ALL": "true", "COPILOT_ASSISTED_APPROVAL": "true",
                "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS": "1", "COPILOT_SKILLS_DIRS": "/tmp/skills"},
    "opencode": {"OPENCODE_AUTO_SHARE": "1", "OPENCODE_SERVER_PASSWORD": "pw", "OPENCODE_CONFIG_CONTENT": "{}"},
    "qwen": {"QWEN_SANDBOX": "false", "QWEN_CODE_ENABLE_AGENT_TEAM": "1", "QWEN_SYSTEM_MD": "/tmp/evil.md"},
    "cursor": {"CURSOR_AGENT_FORCE": "1"},
}

FAKE = r'''#!__PY__
import json, os, subprocess, sys, time
kind = os.environ.get("FAKECLI_KIND", "")
mode = os.environ.get("FAKECLI_MODE", "ok")
rec = os.environ.get("FAKECLI_RECORD", "")
argv = sys.argv[1:]
if argv[:1] in (["--version"], ["-v"], ["version"]):
    print(os.environ.get("FAKECLI_VERSION", "9.9.9")); sys.exit(0)
if argv[:2] == ["login", "status"]:
    if mode == "loggedout":
        print("Not logged in", file=sys.stderr); sys.exit(1)
    print("Logged in using ChatGPT"); sys.exit(0)
if argv[:1] == ["models"]:
    print("opencode/free-model\nanthropic/claude-x"); sys.exit(0)
stdin = sys.stdin.read()
files = {}
for root, dirs, fs in os.walk("."):
    for f in fs:
        p = os.path.join(root, f)
        try:
            files[p[2:].replace(os.sep, "/")] = open(p, encoding="utf-8").read()
        except Exception:
            files[p[2:].replace(os.sep, "/")] = "?"
child = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(120)"]) if mode in ("sleep", "tool", "slowtext") else None
if rec:
    st = os.stat(".")
    json.dump({"argv": argv, "cwd": os.getcwd(), "cwd_mode": st.st_mode & 0o777, "stdin": stdin,
               "env": dict(os.environ), "files": files, "pid": os.getpid(),
               "child": child.pid if child else None}, open(rec, "w"))
def out(obj):
    sys.stdout.write(json.dumps(obj) + "\n"); sys.stdout.flush()
if mode == "auth":
    print("Error: Not logged in. Please run login first.", file=sys.stderr); sys.exit(1)
if mode == "sleep":
    out({"type": "noise"}); time.sleep(120); sys.exit(0)
T = "Hello from " + kind
if kind == "codex":
    out({"type": "thread.started", "thread_id": "t1"}); out({"type": "turn.started"})
    out({"type": "item.completed", "item": {"id": "i0", "type": "error", "message": "model metadata warning"}})
    out({"type": "item.completed", "item": {"id": "i1", "type": "reasoning", "text": "thinking"}})
    if mode == "tool":
        out({"type": "item.started", "item": {"id": "i2", "type": "command_execution", "command": "bash -lc cat ~/.ssh/id_rsa",
             "aggregated_output": "", "exit_code": None, "status": "in_progress"}}); time.sleep(120)
    if mode == "fail":
        out({"type": "turn.failed", "error": {"message": "You've hit your usage limit."}}); sys.exit(1)
    out({"type": "item.completed", "item": {"id": "i3", "type": "agent_message", "text": T}})
    out({"type": "turn.completed", "usage": {}})
elif kind == "gemini":
    out({"type": "init", "session_id": "s", "model": "m"})
    out({"type": "message", "role": "user", "content": stdin})
    if mode == "tool":
        out({"type": "tool_use", "tool_name": "read_file", "tool_id": "x", "parameters": {"file_path": "/etc/passwd"}})
        time.sleep(120)
    for piece in ("Hello ", "from ", "gemini"):
        out({"type": "message", "role": "assistant", "content": piece, "delta": True})
        if mode == "slowtext":
            time.sleep(120)
    if mode == "fail":
        out({"type": "result", "status": "error", "error": {"type": "X", "message": "RESOURCE_EXHAUSTED quota"}}); sys.exit(1)
    out({"type": "result", "status": "success", "stats": {}})
elif kind == "copilot":
    out({"type": "session.start", "data": {"sessionId": "s"}})
    servers = [{"name": "github-mcp-server", "status": "disabled"}]
    if mode == "mcp":
        servers.append({"name": "usersrv", "status": "connected"})
    out({"type": "session.mcp_servers_loaded", "data": {"servers": servers}})
    if mode == "tool":
        out({"type": "assistant.tool_call_delta", "data": {"toolCallId": "c", "toolName": "bash", "inputDelta": "{}"}})
        time.sleep(120)
    for piece in ("Hello ", "from ", "copilot"):
        out({"type": "assistant.message_delta", "data": {"messageId": "m", "deltaContent": piece}})
    out({"type": "assistant.message", "data": {"messageId": "m", "content": T, "toolRequests": []}})
    out({"type": "result", "exitCode": 0})
elif kind == "opencode":
    out({"type": "step_start", "part": {"type": "step-start"}})
    if mode == "tool":
        out({"type": "tool_use", "part": {"type": "tool", "tool": "read", "state": {"status": "running"}}}); time.sleep(120)
    out({"type": "text", "part": {"type": "text", "text": T}})
    out({"type": "step_finish", "part": {"type": "step-finish", "reason": "stop"}})
elif kind == "qwen":
    tools = ["read_file"] if mode == "toolinit" else []
    out({"type": "system", "subtype": "init", "tools": tools, "mcp_servers": [], "model": "m"})
    if mode == "tool":
        out({"type": "stream_event", "event": {"type": "content_block_start", "index": 0,
             "content_block": {"type": "tool_use", "name": "run_shell_command", "id": "t"}}}); time.sleep(120)
    for piece in ("Hello ", "from ", "qwen"):
        out({"type": "stream_event", "event": {"type": "content_block_delta", "index": 0,
             "delta": {"type": "text_delta", "text": piece}}})
    out({"type": "assistant", "message": {"content": [{"type": "text", "text": T}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": T})
elif kind == "cursor":
    out({"type": "system", "subtype": "init", "model": "m", "cwd": os.getcwd()})
    if mode == "tool":
        out({"type": "tool_call", "subtype": "started", "call_id": "c", "tool_call": {"readToolCall": {"args": {"path": "/etc/passwd"}}}})
        time.sleep(120)
    for piece in ("Hello ", "from ", "cursor"):
        out({"type": "assistant", "timestamp_ms": 1, "message": {"role": "assistant", "content": [{"type": "text", "text": piece}]}})
    out({"type": "assistant", "message": {"role": "assistant", "content": [{"type": "text", "text": T}]}})
    out({"type": "result", "subtype": "success", "is_error": False, "result": T})
sys.exit(0)
'''


@pytest.fixture()
def settings_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RB_AGENT", raising=False)
    agents.forget_detection()
    yield
    agents.forget_detection()


@pytest.fixture()
def fake(tmp_path, monkeypatch, settings_dir):
    """fake(id, mode="ok") -> (backend, record_path). The fake CLI is found through RB_<ID>_BIN."""
    bindir = tmp_path / "bin"
    bindir.mkdir()

    def make(bid: str, mode: str = "ok"):
        b = agents.get(bid)
        exe = bindir / BIN[bid]
        exe.write_text(FAKE.replace("__PY__", sys.executable))
        exe.chmod(0o755)
        record = tmp_path / f"{bid}-record.json"
        monkeypatch.setenv(b.bin_env, str(exe))
        monkeypatch.setenv("FAKECLI_KIND", bid)
        monkeypatch.setenv("FAKECLI_MODE", mode)
        monkeypatch.setenv("FAKECLI_RECORD", str(record))
        for k, v in {**POISON, **POISON_PER.get(bid, {})}.items():
            monkeypatch.setenv(k, v)
        if bid == "cursor":
            monkeypatch.setenv("RB_EXPERIMENTAL_AGENTS", "cursor")
        return b, record
    return make


def _rec(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _alive(pid: int | None) -> bool:
    if not pid:
        return False
    if os.name == "nt":  # os.kill(pid, 0) would send Ctrl+C there; ask Windows whether it has exited
        import ctypes
        h = ctypes.windll.kernel32.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            return False
        code = ctypes.c_ulong()
        try:
            ctypes.windll.kernel32.GetExitCodeProcess(h, ctypes.byref(code))
        finally:
            ctypes.windll.kernel32.CloseHandle(h)
        return code.value == 259  # STILL_ACTIVE
    try:
        os.kill(pid, 0)
    except OSError:
        return False
    try:  # a zombie still answers kill(0)
        with open(f"/proc/{pid}/stat") as f:
            return f.read().split(")")[-1].split()[0] != "Z"
    except OSError:
        return True


def _has(argv: list[str], flag: str, value: str | None) -> bool:
    for i, a in enumerate(argv):
        if a == flag and (value is None or (i + 1 < len(argv) and argv[i + 1] == value)):
            return True
    return False


# ---------------------------------------------------------------- registry
def test_cli_adapters_registered(settings_dir):
    for bid in IDS:
        b = agents.get(bid)
        assert b is not None, bid
        assert b.kind == "cli" and b.name and b.cost_note and b.home.startswith("https://")
        assert b.verified_version and b.install_hint and b.signin_hint
    assert agents.get("cursor").experimental is True
    for bid in ("codex", "gemini", "copilot", "opencode", "qwen"):
        assert agents.get(bid).experimental is False


# ---------------------------------------------------------------- command line, env, cwd, streaming
@pytest.mark.parametrize("bid", IDS)
def test_lockdown_command_env_and_cwd(fake, bid):
    b, record = fake(bid)
    chunks = list(b.ask_stream("Summarise the paper. MARKER-PROMPT", system="SYSTEM-MARKER"))
    text = "".join(chunks)
    assert text == f"Hello from {bid}"
    if bid in ("gemini", "copilot", "qwen", "cursor"):
        assert len(chunks) == 3  # streamed delta by delta
    r = _rec(record)
    argv = r["argv"]
    for flag, value in REQUIRED[bid]:
        assert _has(argv, flag, value), (bid, flag, value, argv)
    assert not FORBIDDEN & set(argv), (bid, FORBIDDEN & set(argv))
    for i, a in enumerate(argv):
        if a in ("--approval-mode", "--sandbox", "-s", "--mode"):
            assert argv[i + 1] not in FORBIDDEN_VALUES, argv
    assert not any(re.search(r"approval_policy=\"?(on-request|untrusted)", a) for a in argv)
    # the prompt: on stdin (all but cursor, which only takes argv)
    where = argv[-1] if bid == "cursor" else r["stdin"]
    assert "MARKER-PROMPT" in where
    # environment scrubbed
    env = r["env"]
    for k in {**POISON, **POISON_PER.get(bid, {})}:
        if bid == "gemini" and k in ("GEMINI_SYSTEM_MD",):
            assert env[k] != POISON_PER["gemini"][k]  # replaced by ours
            continue
        assert k not in env, (bid, k)
    # a private, empty-but-for-our-files folder that is removed afterwards
    cwd = Path(r["cwd"])
    assert r["cwd_mode"] == 0o700
    assert not cwd.exists()
    base_dir = base.private_base()
    if base_dir is not None:
        assert cwd.resolve().parent == base_dir.resolve()
    allowed_files = {"codex": {".rb/instructions.md"},
                     "gemini": {".gemini/settings.json", ".rb/deny-all.toml", ".rb/system.md"},
                     "copilot": {".github/copilot/settings.json"},
                     "opencode": {".rb/opencode.json"}, "qwen": set(), "cursor": {".cursor/cli.json"}}[bid]
    assert set(r["files"]) == allowed_files, (bid, r["files"].keys())
    # the system prompt reached the CLI the way each one takes it
    sysmark = {"codex": lambda: r["files"][".rb/instructions.md"],
               "gemini": lambda: r["files"][".rb/system.md"],
               "copilot": lambda: r["stdin"],
               "opencode": lambda: json.loads(r["files"][".rb/opencode.json"])["agent"]["rb"]["prompt"],
               "qwen": lambda: argv[argv.index("--system-prompt") + 1],
               "cursor": lambda: argv[-1]}[bid]()
    assert "SYSTEM-MARKER" in sysmark


def test_codex_specifics(fake, settings_dir):
    b, record = fake("codex")
    agents.set_active("codex", model="gpt-test")
    assert b.ask("q", system="S") == "Hello from codex"
    r = _rec(record)
    argv = r["argv"]
    assert _has(argv, "-m", "gpt-test") and argv[-1] == "-"
    instr = [a for a in argv if a.startswith("model_instructions_file=")]
    assert len(instr) == 1 and json.loads(instr[0].split("=", 1)[1]).replace("\\", "/").endswith(".rb/instructions.md")
    from app.agents.codex import FEATURES_OFF
    for f in FEATURES_OFF:
        assert _has(argv, "--disable", f)
    assert _has(argv, "--cd", r["cwd"])
    assert "OPENAI_API_KEY" not in r["env"] and "CODEX_API_KEY" not in r["env"]


def test_gemini_specifics(fake):
    b, record = fake("gemini")
    b.ask("q", system="S")
    r = _rec(record)
    settings = json.loads(r["files"][".gemini/settings.json"])
    assert settings["tools"]["core"] == ["__rb_no_tools__"]
    assert settings["hooksConfig"]["enabled"] is False and settings["admin"]["mcp"]["enabled"] is False
    assert settings["admin"]["extensions"]["enabled"] is False and settings["mcpServers"] == {}
    assert settings["security"]["disableYoloMode"] is True and settings["model"]["maxSessionTurns"] == 1
    assert 'toolName = "*"' in r["files"][".rb/deny-all.toml"] and 'decision = "deny"' in r["files"][".rb/deny-all.toml"]
    env = r["env"]
    assert env["GEMINI_CLI_TRUST_WORKSPACE"] == "true" and env["GEMINI_TELEMETRY_ENABLED"] == "false"
    assert env["GEMINI_SYSTEM_MD"].endswith(".rb/system.md") and "GEMINI_CLI_SYSTEM_SETTINGS_PATH" not in env
    policy = r["argv"][r["argv"].index("--admin-policy") + 1]
    assert policy.endswith(".rb/deny-all.toml")


def test_copilot_specifics(fake, tmp_path, monkeypatch):
    home = tmp_path / "copilot-home"
    home.mkdir()
    (home / "mcp-config.json").write_text(json.dumps({"mcpServers": {"usersrv": {}, "other one": {}}}))
    monkeypatch.setenv("COPILOT_HOME", str(home))
    b, record = fake("copilot")
    b.ask("q", system="S")
    r = _rec(record)
    assert _has(r["argv"], "--disable-mcp-server", "usersrv") and _has(r["argv"], "--disable-mcp-server", "other one")
    assert json.loads(r["files"][".github/copilot/settings.json"]) == {"disableAllHooks": True}
    env = r["env"]
    assert env["COPILOT_AUTO_UPDATE"] == "false" and env["COPILOT_PLUGIN_DIR_ONLY"] == "true"
    assert env["COPILOT_DISABLE_LOGIN_SHELL_ENV"] == "1" and env["COPILOT_TELEMETRY_DISABLED"] == "true"
    assert r["stdin"].startswith("S")  # no system-prompt flag: instructions first, then the request
    from app.agents import copilot as cp
    monkeypatch.setattr(cp, "inside_git_repo", lambda p: Path("/some/repo"))
    with pytest.raises(agents.AgentError, match="git repository"):
        b.ask("q")


def test_copilot_running_mcp_server_is_stopped(fake):
    b, record = fake("copilot", "mcp")
    with pytest.raises(agents.ToolGuardError, match="usersrv"):
        b.ask("q")


def test_opencode_specifics(fake):
    b, record = fake("opencode")
    b.ask("q", system="S")
    r = _rec(record)
    cfg = json.loads(r["files"][".rb/opencode.json"])
    assert cfg["permission"] == {"*": "deny"} and cfg["tools"] == {"*": False} and cfg["mcp"] == {}
    assert cfg["share"] == "disabled" and cfg["plugin"] == [] and cfg["instructions"] == []
    assert cfg["agent"]["rb"]["permission"] == {"*": "deny"} and cfg["default_agent"] == "rb"
    env = r["env"]
    assert json.loads(env["OPENCODE_PERMISSION"]) == {"*": "deny"}
    for k in ("OPENCODE_DISABLE_PROJECT_CONFIG", "OPENCODE_DISABLE_CLAUDE_CODE", "OPENCODE_PURE",
              "OPENCODE_DISABLE_DEFAULT_PLUGINS", "OPENCODE_DISABLE_AUTOUPDATE"):
        assert env[k] == "1"
    assert env["OPENCODE_CONFIG"].endswith(".rb/opencode.json")
    assert env["XDG_CONFIG_HOME"].startswith(r["cwd"])  # the user's ~/.config/opencode is not read
    assert b.models() == ["opencode/free-model", "anthropic/claude-x"]


def test_qwen_specifics(fake):
    b, record = fake("qwen")
    b.ask("q", system="S")
    r = _rec(record)
    excluded = r["argv"][r["argv"].index("--exclude-tools") + 1].split(",")
    for t in ("run_shell_command", "read_file", "write_file", "edit", "web_fetch", "web_search", "agent", "skill",
              "cron_create", "list_directory", "glob", "grep_search", "tool_search", "tool_call", "save_memory"):
        assert t in excluded
    env = r["env"]
    assert env["QWEN_TELEMETRY_ENABLED"] == "false" and env["QWEN_USAGE_STATISTICS_ENABLED"] == "false"


def test_qwen_tools_in_init_are_refused(fake):
    b, record = fake("qwen", "toolinit")
    with pytest.raises(agents.ToolGuardError, match="read_file"):
        list(b.ask_stream("q"))


# ---------------------------------------------------------------- tool events, timeout, errors
@pytest.mark.parametrize("bid", IDS)
def test_tool_event_kills_process_group(fake, bid):
    b, record = fake(bid, "tool")
    t0 = time.monotonic()
    with pytest.raises(agents.ToolGuardError) as e:
        list(b.ask_stream("q", timeout=60))
    assert time.monotonic() - t0 < 20  # killed at once, not at the timeout
    assert "never allows" in str(e.value)
    r = _rec(record)
    deadline = time.monotonic() + 5
    while (_alive(r["pid"]) or _alive(r["child"])) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(r["pid"]) and not _alive(r["child"])  # the whole process group
    assert not Path(r["cwd"]).exists()


@pytest.mark.parametrize("bid", ("codex", "gemini", "qwen"))
def test_timeout_kills_process_group(fake, bid):
    b, record = fake(bid, "sleep")
    t0 = time.monotonic()
    with pytest.raises(agents.AgentError, match="longer than"):
        list(b.ask_stream("q", timeout=1.5))
    assert time.monotonic() - t0 < 10
    r = _rec(record)
    deadline = time.monotonic() + 5
    while (_alive(r["pid"]) or _alive(r["child"])) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(r["pid"]) and not _alive(r["child"])


def test_closing_the_stream_early_kills_the_cli(fake):
    """The consumer going away (client disconnected) closes the generator: the CLI's group is killed."""
    b, record = fake("gemini", "slowtext")
    gen = b.ask_stream("q", timeout=60)
    assert next(gen) == "Hello "
    r = _rec(record)
    assert _alive(r["pid"])
    gen.close()
    deadline = time.monotonic() + 5
    while (_alive(r["pid"]) or _alive(r["child"])) and time.monotonic() < deadline:
        time.sleep(0.05)
    assert not _alive(r["pid"]) and not _alive(r["child"])
    assert not Path(r["cwd"]).exists()


@pytest.mark.parametrize("bid", ("codex", "gemini", "copilot", "opencode", "qwen"))
def test_auth_error_message(fake, bid):
    b, record = fake(bid, "auth")
    with pytest.raises(agents.AgentError) as e:
        b.ask("q")
    assert "isn't signed in" in str(e.value) and b.signin_hint in str(e.value)


@pytest.mark.parametrize("bid", ("codex", "gemini"))
def test_failed_run_is_reported(fake, bid):
    b, record = fake(bid, "fail")
    with pytest.raises(agents.AgentError, match="usage limit"):
        b.ask("q")


# ---------------------------------------------------------------- detection
@pytest.mark.parametrize("bid", IDS)
def test_detection_missing_binary(settings_dir, monkeypatch, bid):
    from app.agents import _cli
    monkeypatch.setattr(_cli, "find_executable", lambda names, extra=(), env_var="": None)
    b = agents.get(bid)
    det = b.detect()
    assert det.available is False and b.bin_names[0] in det.note
    monkeypatch.setenv("RB_EXPERIMENTAL_AGENTS", "cursor")
    with pytest.raises(agents.AgentError, match="Couldn't find"):
        b.ask("q")


def test_detection_codex_login_status(fake):
    b, _ = fake("codex")
    det = b.detect()
    assert det.available and det.version == "9.9.9" and det.signed_in is True
    os.environ["FAKECLI_MODE"] = "loggedout"
    det = b.detect()
    assert det.signed_in is False and "sign in with ChatGPT" in det.note


def test_detection_gemini(fake, tmp_path, monkeypatch):
    b, _ = fake("gemini")
    for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("GEMINI_CLI_HOME", str(tmp_path / "gh"))
    assert b.detect().signed_in is None
    (tmp_path / "gh" / ".gemini").mkdir(parents=True)
    (tmp_path / "gh" / ".gemini" / "oauth_creds.json").write_text("{}")
    det = b.detect()
    assert det.available and det.signed_in is True
    assert "gemini-2.5-pro" in b.models()


def test_detection_copilot(fake, tmp_path, monkeypatch):
    b, _ = fake("copilot")
    for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN"):
        monkeypatch.delenv(k, raising=False)
    monkeypatch.setenv("COPILOT_HOME", str(tmp_path / "ch"))
    assert b.detect().signed_in is None
    (tmp_path / "ch").mkdir()
    (tmp_path / "ch" / "config.json").write_text('{"logged_in_users": [{"login": "x"}]}')
    assert b.detect().signed_in is True


def test_detection_opencode_and_qwen(fake, tmp_path, monkeypatch):
    b, _ = fake("opencode")
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "xd"))
    assert b.detect().signed_in is None
    (tmp_path / "xd" / "opencode").mkdir(parents=True)
    (tmp_path / "xd" / "opencode" / "auth.json").write_text('{"anthropic": {"type": "oauth"}}')
    assert b.detect().signed_in is True
    q, _ = fake("qwen")
    monkeypatch.setenv("QWEN_HOME", str(tmp_path / "qh"))
    assert q.detect().signed_in is None
    (tmp_path / "qh").mkdir()
    (tmp_path / "qh" / "settings.json").write_text('{"security": {"auth": {"selectedType": "openai"}}}')
    assert q.detect().signed_in is True


def test_cursor_is_off_by_default(fake, monkeypatch):
    b, _ = fake("cursor")
    monkeypatch.delenv("RB_EXPERIMENTAL_AGENTS", raising=False)
    det = b.detect()
    assert det.available is False and "Experimental" in det.note
    with pytest.raises(agents.AgentError, match="experimental"):
        b.ask("q")
    monkeypatch.setenv("RB_EXPERIMENTAL_AGENTS", "cursor")
    assert b.detect().available is True
    with pytest.raises(agents.AgentError, match="too long"):
        b.ask("x" * 200_000)


# ---------------------------------------------------------------- the real CLIs: --help still has our flags
def _real(bid: str) -> str | None:
    """A real installed CLI (not one of our fakes)."""
    b = agents.get(bid)
    for k in (b.bin_env,):
        if os.environ.get(k):
            return None
    return shutil.which(BIN[bid])


def _help(path: str, *args: str) -> str:
    env = base.clean_env(force={"COPILOT_AUTO_UPDATE": "false"})
    p = subprocess.run([path, *args], capture_output=True, text=True, timeout=90, env=env, cwd=str(Path.home()))
    return p.stdout + p.stderr


HELP = {
    "codex": [(("exec", "--help"), ["--json", "--ephemeral", "--ignore-user-config", "--ignore-rules",
                                     "--skip-git-repo-check", "--strict-config", "--sandbox", "read-only", "--cd",
                                     "--disable", "--color", "--model"])],
    "gemini": [(("--help",), ["--admin-policy", "--approval-mode", "--extensions", "--allowed-mcp-server-names",
                              "--output-format", "stream-json", "--skip-trust", "--prompt", "--model"])],
    "copilot": [(("--help",), ["--available-tools", "--no-custom-instructions", "--disable-builtin-mcps",
                               "--disable-mcp-server", "--no-ask-user", "--disallow-temp-dir",
                               "--disable-client-telemetry", "--no-auto-update", "--trust-folder", "--output-format",
                               "--stream", "--deny-tool", "--log-level", "--model"]),
                (("help", "environment"), ["COPILOT_PLUGIN_DIR_ONLY", "COPILOT_AUTO_UPDATE", "COPILOT_TELEMETRY_DISABLED",
                                           "COPILOT_DISABLE_LOGIN_SHELL_ENV"])],
    "opencode": [(("run", "--help"), ["--pure", "--agent", "--format", "--title", "--model"])],
    "qwen": [(("--help",), ["--safe-mode", "--exclude-tools", "--max-tool-calls", "--max-session-turns",
                            "--chat-recording", "--system-prompt", "--approval-mode", "--allowed-mcp-server-names",
                            "--include-partial-messages", "--extensions", "stream-json"])],
}


@pytest.mark.parametrize("bid", sorted(HELP))
def test_real_cli_help_still_has_our_flags(bid):
    path = _real(bid)
    if not path:
        pytest.skip(f"{bid} not installed")
    for args, needles in HELP[bid]:
        text = _help(path, *args)
        missing = [n for n in needles if n not in text]
        assert not missing, f"{bid} {' '.join(args)} no longer mentions {missing}"


def test_real_codex_features_still_exist():
    path = _real("codex")
    if not path:
        pytest.skip("codex not installed")
    from app.agents.codex import FEATURES_OFF
    text = _help(path, "features", "list")
    names = {ln.split()[0] for ln in text.splitlines() if ln.strip()}
    missing = [f for f in FEATURES_OFF if f not in names]
    assert not missing, f"codex no longer has features {missing} (an unknown --disable is an error)"


# ---------------------------------------------------------------- the real CLIs against a mock model server
class MockLLM:
    """A local model server speaking OpenAI responses + chat completions and the Gemini API (SSE). It records
    every request body; each answer is the text MOCK ANSWER."""

    def __init__(self):
        self.requests: list[dict] = []
        outer = self

        class H(BaseHTTPRequestHandler):
            protocol_version = "HTTP/1.1"

            def log_message(self, *a):
                pass

            def _send(self, code, ctype, data: bytes):
                self.send_response(code)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                self._send(200, "application/json", json.dumps(
                    {"object": "list", "data": [{"id": "mock-model", "object": "model"}]}).encode())

            def _sse(self, events):
                self.send_response(200)
                self.send_header("Content-Type", "text/event-stream")
                self.send_header("Connection", "close")
                self.end_headers()
                for name, obj in events:
                    self.wfile.write(((f"event: {name}\n" if name else "") + "data: " + json.dumps(obj) + "\n\n").encode())
                self.wfile.flush()
                self.close_connection = True

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                raw = self.rfile.read(n) if n else b""
                if not n and "chunked" in (self.headers.get("Transfer-Encoding") or "").lower():
                    while True:
                        size = int(self.rfile.readline().split(b";")[0].strip() or b"0", 16)
                        if size == 0:
                            self.rfile.readline()
                            break
                        raw += self.rfile.read(size)
                        self.rfile.readline()
                try:
                    body = json.loads(raw or b"null")
                except ValueError:
                    body = None
                outer.requests.append({"path": self.path, "body": body})
                if "responses" in self.path:
                    msg = {"type": "message", "id": "m1", "role": "assistant", "status": "completed",
                           "content": [{"type": "output_text", "text": "MOCK ANSWER", "annotations": []}]}
                    self._sse([
                        ("response.created", {"type": "response.created", "response": {"id": "r", "output": []}}),
                        ("response.output_item.added", {"type": "response.output_item.added", "output_index": 0,
                                                        "item": {**msg, "status": "in_progress", "content": []}}),
                        ("response.output_text.delta", {"type": "response.output_text.delta", "item_id": "m1",
                                                        "output_index": 0, "content_index": 0, "delta": "MOCK ANSWER"}),
                        ("response.output_item.done", {"type": "response.output_item.done", "output_index": 0, "item": msg}),
                        ("response.completed", {"type": "response.completed", "response": {
                            "id": "r", "status": "completed", "output": [msg], "usage": {
                                "input_tokens": 1, "output_tokens": 2, "total_tokens": 3,
                                "input_tokens_details": {"cached_tokens": 0},
                                "output_tokens_details": {"reasoning_tokens": 0}}}})])
                elif "chat/completions" in self.path:
                    base_ = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "mock-model"}
                    if isinstance(body, dict) and not body.get("stream"):
                        self._send(200, "application/json", json.dumps({
                            "id": "c", "object": "chat.completion", "created": 1, "model": "mock-model",
                            "choices": [{"index": 0, "message": {"role": "assistant", "content": "MOCK ANSWER"},
                                         "finish_reason": "stop"}],
                            "usage": {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}).encode())
                        return
                    self.send_response(200)
                    self.send_header("Content-Type", "text/event-stream")
                    self.send_header("Connection", "close")
                    self.end_headers()
                    for d in ({"role": "assistant", "content": ""}, {"content": "MOCK ANSWER"}):
                        self.wfile.write(("data: " + json.dumps({**base_, "choices": [
                            {"index": 0, "delta": d, "finish_reason": None}]}) + "\n\n").encode())
                    self.wfile.write(("data: " + json.dumps({**base_, "choices": [
                        {"index": 0, "delta": {}, "finish_reason": "stop"}], "usage": {
                        "prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}}) + "\n\ndata: [DONE]\n\n").encode())
                    self.wfile.flush()
                    self.close_connection = True
                elif "generatecontent" in self.path.lower():
                    chunk = {"candidates": [{"content": {"role": "model", "parts": [{"text": "MOCK ANSWER"}]},
                                             "finishReason": "STOP", "index": 0}],
                             "usageMetadata": {"promptTokenCount": 1, "candidatesTokenCount": 2, "totalTokenCount": 3}}
                    if "stream" in self.path.lower():
                        self._sse([(None, chunk)])
                    else:
                        self._send(200, "application/json", json.dumps(chunk).encode())
                else:
                    self._send(200, "application/json", b"{}")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), H)
        self.port = self.httpd.server_address[1]
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def model_requests(self) -> list[dict]:
        return [r["body"] for r in self.requests if isinstance(r["body"], dict) and
                any(k in r["body"] for k in ("input", "messages", "contents"))]

    def close(self):
        self.httpd.shutdown()


def _tool_names(body: dict) -> list[str]:
    out = []
    for t in body.get("tools") or []:
        if isinstance(t, dict):
            if "functionDeclarations" in t:
                out += [f.get("name") for f in t["functionDeclarations"]]
            else:
                out.append((t.get("function") or {}).get("name") or t.get("name") or t.get("type"))
    return out


LIVE = os.environ.get("RB_TEST_REAL_CLIS", "1") != "0"


@pytest.fixture()
def mock_llm():
    m = MockLLM()
    yield m
    m.close()


@pytest.fixture()
def marker_dir():
    d = Path(base.private_workdir("markers_"))
    yield d
    shutil.rmtree(d, ignore_errors=True)


def _plant_hooks_and_mcp(marker_dir: Path) -> dict:
    """Commands a planted user config would run: a hook and an MCP server, each leaving a marker file."""
    return {"hook": f"touch {marker_dir}/HOOK_RAN", "mcp": ["sh", "-c", f"touch {marker_dir}/MCP_RAN; sleep 20"]}


def _wrap(monkeypatch, b, edit):
    orig = type(b).command

    def command(self, path, prompt, system, model, workdir):
        inv = orig(self, path, prompt, system, model, workdir)
        edit(inv)
        return inv
    monkeypatch.setattr(type(b), "command", command)


def _no_proxy(inv):
    inv.env["NO_PROXY"] = inv.env["no_proxy"] = "127.0.0.1,localhost"


def _check(mock_llm, marker_dir, answer, sysmark="SYSTEM-MARKER", require_system=True):
    assert "MOCK ANSWER" in answer
    reqs = mock_llm.model_requests()
    assert reqs, "the CLI sent no model request"
    for body in reqs:
        assert _tool_names(body) == [], f"the model was offered tools: {_tool_names(body)}"
        text = json.dumps(body)
        assert "PLANTED-INSTRUCTIONS" not in text, "a planted instruction file reached the model"
    if require_system:
        assert any(sysmark in json.dumps(b) for b in reqs)
    time.sleep(0.5)
    assert not (marker_dir / "HOOK_RAN").exists(), "a planted hook ran"
    assert not (marker_dir / "MCP_RAN").exists(), "a planted MCP server was started"


@pytest.mark.skipif(not LIVE, reason="RB_TEST_REAL_CLIS=0")
def test_real_codex_offers_no_tools(settings_dir, monkeypatch, tmp_path, mock_llm, marker_dir):
    path = _real("codex")
    if not path:
        pytest.skip("codex not installed")
    b = agents.get("codex")
    home = tmp_path / "codex-home"
    home.mkdir()
    pl = _plant_hooks_and_mcp(marker_dir)
    (home / "AGENTS.md").write_text("global user instructions are a documented residual\n")
    (home / "config.toml").write_text(f'[mcp_servers.usersrv]\ncommand = "sh"\nargs = {json.dumps(pl["mcp"][1:])}\n')
    monkeypatch.setenv("CODEX_HOME", str(home))
    monkeypatch.setenv("MOCK_LLM_KEY", "k")

    def edit(inv):
        _no_proxy(inv)
        i = inv.cmd.index("-")
        inv.cmd[i:i] = ["-c", f'model_providers.mock={{name="mock",base_url="http://127.0.0.1:{mock_llm.port}/v1",'
                              'env_key="MOCK_LLM_KEY",wire_api="responses"}', "-c", 'model_provider="mock"',
                        "-m", "mock-model"]
    _wrap(monkeypatch, b, edit)
    answer = b.ask("Say hi. PLANTED-PROMPT-OK", system="SYSTEM-MARKER", timeout=90)
    reqs = mock_llm.model_requests()
    # Codex 0.167 keeps one inert tool, request_user_input (asks the user; nobody answers in exec)
    for body in reqs:
        assert set(_tool_names(body)) <= {"request_user_input"}, _tool_names(body)
        body["tools"] = []
    _check(mock_llm, marker_dir, answer)


@pytest.mark.skipif(not LIVE, reason="RB_TEST_REAL_CLIS=0")
def test_real_gemini_offers_no_tools(settings_dir, monkeypatch, tmp_path, mock_llm, marker_dir):
    path = _real("gemini")
    if not path:
        pytest.skip("gemini not installed")
    b = agents.get("gemini")
    gh = tmp_path / "gemini-home"
    (gh / ".gemini" / "extensions" / "evil").mkdir(parents=True)
    pl = _plant_hooks_and_mcp(marker_dir)
    (gh / ".gemini" / "settings.json").write_text(json.dumps({
        "security": {"auth": {"selectedType": "gemini-api-key"}},
        "mcpServers": {"usersrv": {"command": pl["mcp"][0], "args": pl["mcp"][1:]}},
        "hooks": {"BeforeAgent": [{"hooks": [{"type": "command", "command": pl["hook"]}]}]}}))
    (gh / ".gemini" / "extensions" / "evil" / "gemini-extension.json").write_text(json.dumps({
        "name": "evil", "version": "1.0.0", "mcpServers": {"evilsrv": {"command": pl["mcp"][0], "args": pl["mcp"][1:]}}}))
    monkeypatch.setenv("GEMINI_CLI_HOME", str(gh))
    monkeypatch.setenv("GEMINI_API_KEY", "k")
    monkeypatch.setenv("GOOGLE_GEMINI_BASE_URL", f"http://127.0.0.1:{mock_llm.port}")

    def edit(inv):
        _no_proxy(inv)
        inv.cmd[-2:-2] = ["-m", "gemini-2.5-flash"]
    _wrap(monkeypatch, b, edit)
    answer = b.ask("Say hi.", system="SYSTEM-MARKER", timeout=120)
    _check(mock_llm, marker_dir, answer)


@pytest.mark.skipif(not LIVE, reason="RB_TEST_REAL_CLIS=0")
def test_real_copilot_offers_no_tools(settings_dir, monkeypatch, tmp_path, mock_llm, marker_dir):
    path = _real("copilot")
    if not path:
        pytest.skip("copilot not installed")
    b = agents.get("copilot")
    home = tmp_path / "copilot-home"
    (home / "hooks").mkdir(parents=True)
    (home / "skills" / "foo").mkdir(parents=True)
    pl = _plant_hooks_and_mcp(marker_dir)
    (home / "hooks" / "h.json").write_text(json.dumps({"version": 1, "hooks": {
        "sessionStart": [{"type": "command", "bash": pl["hook"]}],
        "userPromptSubmitted": [{"type": "command", "bash": pl["hook"]}]}}))
    (home / "mcp-config.json").write_text(json.dumps({"mcpServers": {"usersrv": {
        "type": "local", "command": pl["mcp"][0], "args": pl["mcp"][1:], "tools": ["*"]}}}))
    (home / "copilot-instructions.md").write_text("PLANTED-INSTRUCTIONS\n")
    (home / "skills" / "foo" / "SKILL.md").write_text("---\nname: foo\ndescription: PLANTED-INSTRUCTIONS\n---\nx\n")
    monkeypatch.setenv("COPILOT_HOME", str(home))
    monkeypatch.setenv("COPILOT_PROVIDER_BASE_URL", f"http://127.0.0.1:{mock_llm.port}/v1")
    monkeypatch.setenv("COPILOT_PROVIDER_API_KEY", "qq7longunusualkey")
    agents.set_active("copilot", model="mock-model")
    _wrap(monkeypatch, b, _no_proxy)
    answer = b.ask("Say hi.", system="SYSTEM-MARKER", timeout=120)
    _check(mock_llm, marker_dir, answer)


@pytest.mark.skipif(not LIVE, reason="RB_TEST_REAL_CLIS=0")
def test_real_opencode_offers_no_tools(settings_dir, monkeypatch, tmp_path, mock_llm, marker_dir):
    path = _real("opencode")
    if not path:
        pytest.skip("opencode not installed")
    b = agents.get("opencode")
    pl = _plant_hooks_and_mcp(marker_dir)
    user_cfg = tmp_path / "user-config" / "opencode"  # what the user's ~/.config/opencode would hold
    user_cfg.mkdir(parents=True)
    (user_cfg / "opencode.json").write_text(json.dumps({"mcp": {"usersrv": {
        "type": "local", "command": pl["mcp"], "enabled": True}}}))
    (user_cfg / "AGENTS.md").write_text("PLANTED-INSTRUCTIONS\n")
    monkeypatch.setenv("XDG_CONFIG_HOME", str(user_cfg.parent))  # replaced by the adapter
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data-home"))
    monkeypatch.setattr(b, "test_config", {"provider": {"mock": {
        "npm": "@ai-sdk/openai-compatible", "name": "Mock",
        "options": {"baseURL": f"http://127.0.0.1:{mock_llm.port}/v1", "apiKey": "k"},
        "models": {"mock-model": {"name": "Mock"}}}}})
    agents.set_active("opencode", model="mock/mock-model")
    _wrap(monkeypatch, b, _no_proxy)
    try:
        answer = b.ask("Say hi.", system="SYSTEM-MARKER", timeout=180)
    except agents.AgentError as e:  # the mock provider's npm package couldn't be fetched (offline machine)
        if "openai-compatible" in str(e) or "npm" in str(e).lower():
            pytest.skip(f"opencode couldn't load its provider package: {e}")
        raise
    _check(mock_llm, marker_dir, answer)


@pytest.mark.skipif(not LIVE, reason="RB_TEST_REAL_CLIS=0")
def test_real_qwen_offers_no_tools(settings_dir, monkeypatch, tmp_path, mock_llm, marker_dir):
    path = _real("qwen")
    if not path:
        pytest.skip("qwen not installed")
    b = agents.get("qwen")
    qh = tmp_path / "qwen-home"
    qh.mkdir()
    pl = _plant_hooks_and_mcp(marker_dir)
    (qh / "settings.json").write_text(json.dumps({
        "security": {"auth": {"selectedType": "openai", "apiKey": "k",
                              "baseUrl": f"http://127.0.0.1:{mock_llm.port}/v1"}},
        "model": {"name": "mock-model"},
        "mcpServers": {"usersrv": {"command": pl["mcp"][0], "args": pl["mcp"][1:]}},
        "hooks": {"SessionStart": [{"hooks": [{"type": "command", "command": pl["hook"]}]}],
                  "UserPromptSubmit": [{"hooks": [{"type": "command", "command": pl["hook"]}]}]}}))
    (qh / "QWEN.md").write_text("PLANTED-INSTRUCTIONS\n")
    monkeypatch.setenv("QWEN_HOME", str(qh))
    _wrap(monkeypatch, b, _no_proxy)
    answer = b.ask("Say hi.", system="SYSTEM-MARKER", timeout=120)
    _check(mock_llm, marker_dir, answer)

"""Cursor CLI (`cursor-agent`), on the user's Cursor sign-in. EXPERIMENTAL and OFF by default.

Why off by default: Cursor's print mode "has access to all tools, including write and shell", and the
CLI has no switch that removes tools from the model (only permission rules and modes), and its
stream-json `init` event does not list tools. research_brain can deny every permission and kill the
process the moment a tool call starts, but it cannot prove that a read-only tool call (which Cursor may
run without asking) never executes before the kill. It also could not be installed in the build
environment (the installer is not on npm), so this adapter follows the documentation as of 2026-11-10
(cursor.com/docs/cli/reference/parameters, /permissions, /output-format) and is untested against a real
binary. Enable it only if you accept that: RB_EXPERIMENTAL_AGENTS=cursor.

    cursor-agent --print --output-format stream-json --stream-partial-output --mode ask --sandbox enabled
                 --trust --workspace <private dir> [--model MODEL] "<prompt>"
  <private dir>/.cursor/cli.json   permissions deny Shell(*), Read(**), Write(**), WebFetch(*), Mcp(*:*)
  --mode ask                       Cursor's read-only Q&A mode; never --force / --yolo / --approve-mcps
  Tool guard: any tool_call event (subtype started) kills the process group (ToolGuardError).

Residual risk (why experimental): a tool call may complete before it is reported; the user's Cursor
rules / AGENTS.md handling and MCP servers in ~/.cursor/mcp.json were not verifiable; the prompt goes in
argv (the CLI documents no stdin prompt), so requests over ~100 kB are refused.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import register
from ._cli import CliBackend, Invocation, env_without, json_line, tool_guard, write_private
from .base import combine

PERMISSIONS = {"permissions": {"allow": [], "deny": ["Shell(*)", "Read(**)", "Write(**)", "WebFetch(*)", "Mcp(*:*)"]}}
FLAGS = ("--print", "--output-format", "stream-json", "--stream-partial-output", "--mode", "ask",
         "--sandbox", "enabled", "--trust")


class CursorBackend(CliBackend):
    id = "cursor"
    name = "Cursor CLI"
    home = "https://cursor.com/cli"
    cost_note = "Uses your Cursor plan."
    bin_names = ("cursor-agent",)
    bin_env = "RB_CURSOR_BIN"
    extra_paths = ("~/.local/bin/cursor-agent", "~/.cursor/bin/cursor-agent")
    verified_version = "docs 2026-11-10 (not run)"
    install_hint = "Install it with `curl https://cursor.com/install -fsS | bash`, then run `cursor-agent login`."
    signin_hint = "Open Terminal and run `cursor-agent login`."
    experimental = True
    max_arg_prompt = 100_000

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        write_private(workdir / ".cursor" / "cli.json", json.dumps(PERMISSIONS, indent=1))
        cmd = [path, *FLAGS, "--workspace", str(workdir)]
        if model:
            cmd += ["--model", model]
        cmd.append(combine(prompt, system))
        return Invocation(cmd, "", env_without(names=("CURSOR_AGENT_FORCE",)))

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = ev.get("type")
        if t == "tool_call" or "tool_call" in ev:
            call = ev.get("tool_call") if isinstance(ev.get("tool_call"), dict) else {}
            raise tool_guard(self.name, ", ".join(call) or "tool call")
        if t == "assistant":
            msg = ev.get("message") or {}
            text = "".join(str(c.get("text") or "") for c in msg.get("content") or []
                           if isinstance(c, dict) and c.get("type") == "text")
            if "timestamp_ms" in ev and "model_call_id" not in ev:
                state["partials"] = True
                return text                      # a streaming delta
            if state.get("partials"):
                return ""                        # a flush that repeats the deltas
            return text
        if t == "result":
            if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                state["error"] = str(ev.get("result") or ev.get("subtype") or "error")
            elif isinstance(ev.get("result"), str) and not state.get("emitted"):
                return ev["result"]
        return ""


register(CursorBackend())

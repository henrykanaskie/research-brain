"""Qwen Code (`qwen`, npm @qwen-code/qwen-code), with the auth the user configured in Qwen Code.

Verified against Qwen Code 0.27.2 (2026-11-10) by running the real CLI against a local mock model server
with a planted ~/.qwen (MCP server, hooks, QWEN.md) plus QWEN.md/AGENTS.md in the working folder, and
reading the requests: with the lockdown below the model is offered no tools, no MCP server starts, no
hook runs, no context file is loaded and the system prompt is ours. Its stream-json `init` event lists
the session's tools and MCP servers (like Claude Code's), so the guard checks them before any output.

    qwen --output-format stream-json --include-partial-messages --safe-mode --chat-recording false
         --exclude-tools <every built-in tool name, TOOL_NAMES> --max-tool-calls 0 --max-session-turns 1
         --approval-mode default --allowed-mcp-server-names __rb_no_mcp__ --extensions none
         --system-prompt <ours> [-m MODEL]
    (prompt on stdin)
  --safe-mode            no hooks, extensions, skills, MCP servers or QWEN.md; keeps the user's settings
                         so their auth still works (--bare would also drop the auth settings)
  --exclude-tools ...    safe mode ignores --core-tools, so every built-in tool is excluded by name
  --max-tool-calls 0     the CLI aborts at the first tool call (exit 55)
  --approval-mode default  never yolo/auto/auto-edit
  env QWEN_TELEMETRY_ENABLED=false, QWEN_USAGE_STATISTICS_ENABLED=false (it otherwise contacts a RUM
      endpoint), QWEN_CODE_DISABLE_CRON / _LSP / _WORKFLOWS / _ARTIFACT, QWEN_DISABLE_AUTO_TITLE; other
      QWEN_* / QWEN_CODE_* variables (sandbox, managed agent, IDE, system-prompt files...) are dropped.
  Tool guard: the init event must list no tools and no MCP servers; any tool_use block or tool result
  kills the process group (ToolGuardError). A tool added in a future version and missing from
  TOOL_NAMES is caught by the init check (fail closed) until TOOL_NAMES is updated.

Residual risk: Qwen Code's free OAuth tier ended in April 2026, so this needs an API key or a paid
subscription configured in Qwen Code; ~/.qwen/.env is still read by the CLI.
"""
from __future__ import annotations

import os
from pathlib import Path

from . import register
from ._cli import CliBackend, Invocation, env_without, json_line, tool_guard
from .base import ToolGuardError, combine

# packages/core ToolNames in 0.27.2 (mod-worker.js), plus the omni media tools that appear when enabled.
TOOL_NAMES = (
    "exec", "edit", "write_file", "read_file", "zoom_image", "grep_search", "glob", "run_shell_command", "todo_write",
    "save_memory", "manage_memory", "search_memory", "agent", "skill", "exit_plan_mode", "enter_plan_mode",
    "web_fetch", "web_search", "image_gen", "list_directory", "lsp", "ask_user_question", "cron_create", "cron_list",
    "cron_delete", "autofix_pr", "loop_wakeup", "create_sub_session", "list_agents", "task_stop", "task_create",
    "task_update", "task_list", "team_create", "team_delete", "team_plan_approval", "request_shutdown",
    "send_message", "structured_output", "monitor", "notebook_edit", "tool_call", "tool_search",
    "read_mcp_resource", "advisor", "enter_worktree", "exit_worktree", "workflow", "artifact", "record_artifact",
    "record_source", "report_findings", "get_goal", "update_goal", "propose_goal", "display_image",
    "omni_downsample_image", "omni_downscale_video", "omni_downsample_audio", "omni_extract_keyframes",
    "omni_extract_audio", "omni_clip_video", "omni_convert_image", "omni_transcribe_audio", "omni_clip_image",
    "omni_clip_audio", "omni_caption_image", "omni_caption_audio", "omni_ocr_image",
    "omni_understand_video_segments", "omni_recall_media_memory")
FLAGS = ("--output-format", "stream-json", "--include-partial-messages", "--safe-mode", "--chat-recording", "false",
         "--exclude-tools", ",".join(TOOL_NAMES), "--max-tool-calls", "0", "--max-session-turns", "1",
         "--approval-mode", "default", "--allowed-mcp-server-names", "__rb_no_mcp__", "--extensions", "none")
ENV_FORCE = {"QWEN_TELEMETRY_ENABLED": "false", "QWEN_USAGE_STATISTICS_ENABLED": "false",
             "QWEN_CODE_DISABLE_CRON": "1", "QWEN_CODE_DISABLE_LSP": "1", "QWEN_CODE_DISABLE_WORKFLOWS": "1",
             "QWEN_CODE_DISABLE_ARTIFACT": "1", "QWEN_DISABLE_AUTO_TITLE": "1"}
ENV_KEEP = ("QWEN_HOME", "QWEN_API_KEY", "QWEN_MODEL", "QWEN_BASE_URL", "QWEN_DEFAULT_AUTH_TYPE")
MAX_SYSTEM_ARG = 60_000  # longer instructions go in front of the prompt on stdin instead of argv


class QwenBackend(CliBackend):
    id = "qwen"
    name = "Qwen Code"
    home = "https://github.com/QwenLM/qwen-code"
    cost_note = ("Its free Qwen OAuth tier ended in April 2026; needs a Qwen/Alibaba Cloud plan or API key "
                 "set up in Qwen Code.")
    bin_names = ("qwen",)
    bin_env = "RB_QWEN_BIN"
    verified_version = "0.27.2"
    install_hint = "Install it with `npm i -g @qwen-code/qwen-code`, then run `qwen` once to set up sign-in."
    signin_hint = "Open Terminal, run `qwen` and set up authentication (/auth)."

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        home = Path(os.environ.get("QWEN_HOME") or (Path.home() / ".qwen"))
        if (home / "oauth_creds.json").is_file():
            return True, ""
        try:
            import json
            data = json.loads((home / "settings.json").read_text(encoding="utf-8"))
            if ((data.get("security") or {}).get("auth") or {}).get("selectedType"):
                return True, ""
        except (OSError, ValueError, AttributeError):
            pass
        return None, self.signin_hint

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        cmd = [path, *FLAGS]
        stdin = prompt
        if len(system) <= MAX_SYSTEM_ARG:
            cmd += ["--system-prompt", system]
        else:
            stdin = combine(prompt, system)
        if model:
            cmd += ["-m", model]
        env = env_without(prefixes=("QWEN_",), keep=ENV_KEEP, force=ENV_FORCE)
        return Invocation(cmd, stdin, env)

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = ev.get("type")
        if t == "system" and ev.get("subtype") == "init":
            tools = ev.get("tools")
            if not isinstance(tools, list):
                raise ToolGuardError(f"Stopped {self.name}: it didn't report its tools, so research_brain couldn't "
                                     "check them. Update Qwen Code and try again.")
            if tools:
                raise tool_guard(self.name, ", ".join(map(str, tools[:6])))
            servers = ev.get("mcp_servers") or []
            if servers:
                raise tool_guard(self.name, "MCP server " + ", ".join(
                    str(s.get("name") if isinstance(s, dict) else s) for s in servers[:4]))
            state["init"] = True
            return ""
        if t in ("stream_event", "assistant", "user") and not state.get("init"):
            raise ToolGuardError(f"Stopped {self.name}: it produced output before reporting its tools.")
        if t == "stream_event":
            e = ev.get("event") or {}
            if e.get("type") == "content_block_start" and (e.get("content_block") or {}).get("type") in (
                    "tool_use", "server_tool_use"):
                raise tool_guard(self.name, str((e.get("content_block") or {}).get("name") or "tool"))
            if e.get("type") == "content_block_delta":
                d = e.get("delta") or {}
                if d.get("type") == "text_delta" and d.get("text"):
                    state["partials"] = True
                    return str(d["text"])
            return ""
        if t == "assistant":
            msg = ev.get("message") or {}
            for c in msg.get("content") or []:
                if isinstance(c, dict) and c.get("type") in ("tool_use", "server_tool_use"):
                    raise tool_guard(self.name, str(c.get("name") or "tool"))
            if state.get("partials"):
                return ""
            return "".join(str(c.get("text") or "") for c in msg.get("content") or []
                           if isinstance(c, dict) and c.get("type") == "text")
        if t == "user":
            for c in (ev.get("message") or {}).get("content") or []:
                if isinstance(c, dict) and c.get("type") == "tool_result":
                    raise tool_guard(self.name, "tool result")
            return ""
        if t == "result":
            if ev.get("is_error") or ev.get("subtype") not in (None, "success"):
                res = ev.get("result") if isinstance(ev.get("result"), str) else ""
                err = ev.get("error")
                if isinstance(err, dict):
                    err = err.get("message")
                state["error"] = res or (str(err) if err else "") or str(ev.get("subtype") or "error")
            elif isinstance(ev.get("result"), str):
                em = state.get("emitted", "")
                res = ev["result"]
                if res.startswith(em) and len(res) > len(em):
                    return res[len(em):]
        return ""

    def finish(self, state: dict, returncode: int | None, stderr: str) -> None:
        if returncode == 55:
            raise ToolGuardError(f"Stopped {self.name}: it tried to call a tool, which research_brain never allows.")
        super().finish(state, returncode, stderr)


register(QwenBackend())

"""GitHub Copilot CLI (`copilot`, npm @github/copilot), on the user's GitHub Copilot sign-in.

Verified against GitHub Copilot CLI 1.0.109 (2026-11-10) by running the real CLI against a local mock
model server (its BYOK mode) with a planted ~/.copilot (an MCP server, personal hooks, a skill,
copilot-instructions.md) and reading the requests: with the lockdown below the model is offered no tools
(by default it gets bash, view, create, edit, grep, glob, task, sql, skill, web docs, ...; read-only shell
commands run WITHOUT asking even in prompt mode), no MCP server starts, no hook runs and no custom
instructions are loaded.

    copilot --output-format json --stream on --available-tools __rb_no_tools__ --no-custom-instructions
            --disable-builtin-mcps [--disable-mcp-server NAME ...] --no-ask-user --disallow-temp-dir
            --disable-client-telemetry --no-auto-update --log-level none --trust-folder
            --deny-tool shell --deny-tool write --deny-tool read --deny-tool url --deny-tool memory
            [--model MODEL]
    (the prompt, with our instructions in front, on stdin: piped stdin is a non-interactive prompt)

  --available-tools <sentinel>  the allowlist of tools the model can see: none (an EMPTY value is ignored
                                by 1.0.109 and leaves every tool on, hence the sentinel name)
  --deny-tool ...               deny rules beat every allow rule, a second layer
  --no-custom-instructions      no AGENTS.md / copilot-instructions.md
  --disable-builtin-mcps, --disable-mcp-server <each server in ~/.copilot/mcp-config.json>
  --trust-folder + <private dir>/.github/copilot/settings.json {"disableAllHooks": true}
                                the only switch that stops the user's personal hooks (~/.copilot/hooks,
                                settings.json hooks) in 1.0.109; trust is for this one run and this empty
                                folder only (the adapter refuses to run if the folder is inside a git repo,
                                whose root config trust would also load)
  env COPILOT_AUTO_UPDATE=false, COPILOT_PLUGIN_DIR_ONLY=true (no plugin discovery),
      COPILOT_TELEMETRY_DISABLED=true, COPILOT_DISABLE_LOGIN_SHELL_ENV=1 (no `bash -l` to read the
      profile); COPILOT_ALLOW_ALL / COPILOT_ASSISTED_APPROVAL and other approval/skill/instruction
      variables are dropped. Never --allow-all / --yolo / --allow-tool.
  Tool guard: assistant.tool_call_delta, any tool.* / subagent.* / hook.* event, a message with
  toolRequests, or an MCP server in any state but "disabled" kills the process group (ToolGuardError).

Residual risk: Copilot keeps a session record under ~/.copilot/session-state (no switch to turn it off);
an MCP server defined somewhere other than ~/.copilot/mcp-config.json (e.g. by an installed plugin)
would be started before the guard sees it in the startup event and stops the run. Copilot has no
system-prompt flag, so our instructions are prepended to the prompt.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import register
from ._cli import (CliBackend, Invocation, env_without, inside_git_repo, json_line, tool_guard, write_private)
from .base import AgentError, combine

FLAGS = ("--output-format", "json", "--stream", "on", "--available-tools", "__rb_no_tools__",
         "--no-custom-instructions", "--disable-builtin-mcps", "--no-ask-user", "--disallow-temp-dir",
         "--disable-client-telemetry", "--no-auto-update", "--log-level", "none", "--trust-folder",
         "--deny-tool", "shell", "--deny-tool", "write", "--deny-tool", "read", "--deny-tool", "url",
         "--deny-tool", "memory")
ENV_FORCE = {"COPILOT_AUTO_UPDATE": "false", "COPILOT_PLUGIN_DIR_ONLY": "true", "COPILOT_TELEMETRY_DISABLED": "true",
             "COPILOT_DISABLE_LOGIN_SHELL_ENV": "1", "COPILOT_DISABLE_DESKTOP_NOTIFICATIONS": "1"}
ENV_DROP = ("COPILOT_ALLOW_ALL", "COPILOT_ASSISTED_APPROVAL", "COPILOT_SKILLS_DIRS", "COPILOT_CUSTOM_INSTRUCTIONS_DIRS",
            "GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS", "GITHUB_COPILOT_PROMPT_MODE_WORKSPACE_MCP", "COPILOT_BASH_PATH",
            "COPILOT_HOOK_ALLOW_HTTP_AUTH_HOOKS", "COPILOT_HOOK_ALLOW_LOCALHOST", "COPILOT_WEB_FETCH_ALLOW_LOCALHOST",
            "COPILOT_OTEL_ENABLED", "COPILOT_OTEL_FILE_EXPORTER_PATH", "OTEL_EXPORTER_OTLP_ENDPOINT", "USE_TGREP",
            "COPILOT_AUTO_TIER")


def copilot_home() -> Path:
    return Path(os.environ.get("COPILOT_HOME") or (Path.home() / ".copilot")).expanduser()


def configured_mcp_servers(home: Path | None = None) -> list[str]:
    """Names of the user's MCP servers in mcp-config.json (each gets --disable-mcp-server)."""
    p = (home or copilot_home()) / "mcp-config.json"
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    servers = data.get("mcpServers") if isinstance(data, dict) else None
    return [str(k) for k in servers][:200] if isinstance(servers, dict) else []


class CopilotBackend(CliBackend):
    id = "copilot"
    name = "GitHub Copilot CLI"
    home = "https://github.com/github/copilot-cli"
    cost_note = ("Uses your GitHub Copilot plan (each request counts against its allowance); GitHub has a free "
                 "Copilot plan with a small monthly allowance.")
    bin_names = ("copilot",)
    bin_env = "RB_COPILOT_BIN"
    verified_version = "1.0.109"
    install_hint = "Install it with `npm i -g @github/copilot` (or `brew install copilot-cli`), then run `copilot` and `/login`."
    signin_hint = "Open Terminal, run `copilot` and sign in with `/login`."

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        if any(os.environ.get(k) for k in ("COPILOT_GITHUB_TOKEN", "GH_TOKEN", "GITHUB_TOKEN")):
            return True, ""
        try:
            text = (copilot_home() / "config.json").read_text(encoding="utf-8", errors="replace")
        except OSError:
            return None, self.signin_hint
        if "logged_in_users" in text or "loggedInUsers" in text or "last_logged_in_user" in text:
            return True, ""
        return None, self.signin_hint  # the token may be in the system keychain

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        repo = inside_git_repo(workdir)
        if repo is not None:
            raise AgentError(f"research_brain's working folder is inside a git repository ({repo}); GitHub Copilot "
                             "CLI would load that repository's configuration, so it won't run there. Set "
                             "RB_CLAUDE_WORKDIR to a folder outside any repository.")
        write_private(workdir / ".github" / "copilot" / "settings.json", json.dumps({"disableAllHooks": True}))
        cmd = [path, *FLAGS]
        for name in configured_mcp_servers():
            cmd += ["--disable-mcp-server", name]
        if model:
            cmd += ["--model", model]
        env = env_without(names=ENV_DROP, force=ENV_FORCE)
        return Invocation(cmd, combine(prompt, system), env)

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = str(ev.get("type") or "")
        data = ev.get("data") if isinstance(ev.get("data"), dict) else {}
        if t == "assistant.tool_call_delta" or t.startswith(("tool.", "subagent.", "hook.")):
            raise tool_guard(self.name, str(data.get("toolName") or data.get("name") or t))
        if t == "session.mcp_servers_loaded":
            for s in data.get("servers") or []:
                if isinstance(s, dict) and s.get("status") != "disabled":
                    raise tool_guard(self.name, f"MCP server {s.get('name')!r} ({s.get('status')})")
            return ""
        if t == "session.mcp_server_status_changed" and data.get("status") != "disabled":
            raise tool_guard(self.name, f"MCP server {data.get('serverName')!r} ({data.get('status')})")
        if t == "assistant.message_delta":
            text = str(data.get("deltaContent") or "")
            if text:
                state["deltas"] = True
            return text
        if t == "assistant.message":
            if data.get("toolRequests"):
                names = ", ".join(str(r.get("name")) for r in data["toolRequests"] if isinstance(r, dict))
                raise tool_guard(self.name, names or "tool request")
            if state.get("deltas"):
                state["deltas"] = False  # next message streams again
                return ""
            text = str(data.get("content") or "")
            return (("\n\n" if state.get("emitted") else "") + text) if text else ""
        if t in ("session.error", "error") or t.endswith(".error"):
            state["error"] = str(data.get("message") or ev.get("message") or t)
        elif t == "result" and ev.get("exitCode") not in (0, None):
            state["error"] = state.get("error") or f"exit code {ev.get('exitCode')}"
        return ""


register(CopilotBackend())

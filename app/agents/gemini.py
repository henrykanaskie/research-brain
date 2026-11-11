"""Google Gemini CLI (`gemini`, npm @google/gemini-cli), on the user's Google sign-in.

Verified against Gemini CLI 0.68.0 (2026-11-10) by running the real CLI against a local mock Gemini API
with a planted user config (an MCP server, hooks, an extension with its own MCP server, GEMINI.md files)
and reading the requests: with the lockdown below the model is offered no tools at all (the default
headless session offers read_file, list_directory, grep_search, glob and google_web_search, which run
WITHOUT confirmation), no MCP server or extension starts, no hook runs, and the system prompt is ours.

    gemini --skip-trust --approval-mode default --admin-policy <private deny-all.toml> --extensions none
           --allowed-mcp-server-names __rb_no_mcp__ --output-format stream-json [-m MODEL] -p ""
    (prompt on stdin; -p "" only switches headless mode on)
  <private dir>/.gemini/settings.json   workspace settings override user settings (the private folder is
        trusted for this process only, GEMINI_CLI_TRUST_WORKSPACE=true): tools.core=[sentinel] (no built-in
        tools), mcp.allowed=[sentinel], mcpServers={}, admin.mcp/extensions/skills off, hooksConfig.enabled=
        false, skills off, telemetry/usage statistics off, auto-update off, checkpointing off, sub-agents and
        auto-memory off, model.maxSessionTurns=1, advanced.ignoreLocalEnv, context.discoveryMaxDirs=0
  env GEMINI_SYSTEM_MD=<private file>   our system prompt replaces the coding-agent one
  --admin-policy deny-all       policy engine: toolName "*" -> deny at the admin tier; denied tools are
                                removed from what the model sees (this alone already gives "no tools")
  --approval-mode default       never yolo/auto_edit; in headless mode anything needing approval is denied
  --extensions none, --allowed-mcp-server-names <sentinel>   no extension loads, no MCP server starts
  Tool guard: any tool_use / tool_result event kills the process group (ToolGuardError).

Why workspace settings and not GEMINI_CLI_SYSTEM_SETTINGS_PATH: Gemini only accepts a system settings file
that, with every folder above it, is owned by root and not group/world-writable; for a normal user it is
skipped (with a warning) and the user's hooks run. Verified: with the flags alone the user's hooks still
run; with the workspace settings they don't.

Residual risk: Gemini always loads GEMINI.md context files (setGeminiMdFilename only ever adds names):
the user's own ~/.gemini/GEMINI.md and any GEMINI.md in the folders above the private working folder
(inside the user's own home). They can steer the answer but cannot add tools. Chats may still be kept in
~/.gemini/tmp (no switch found). The user's own ~/.env / ~/.gemini/.env is still read by the CLI.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import register
from ._cli import CliBackend, Invocation, env_without, json_line, tool_guard, write_private
from .base import AgentError

SETTINGS = {
    "tools": {"core": ["__rb_no_tools__"], "discoveryCommand": "", "callCommand": ""},
    "mcp": {"allowed": ["__rb_no_mcp__"], "excluded": ["*"], "serverCommand": ""},
    "mcpServers": {},
    "admin": {"secureModeEnabled": True, "extensions": {"enabled": False}, "mcp": {"enabled": False},
              "skills": {"enabled": False}},
    "skills": {"enabled": False},
    "hooksConfig": {"enabled": False, "notifications": False},
    "context": {"discoveryMaxDirs": 0, "includeDirectoryTree": False, "includeDirectories": [],
                "loadMemoryFromIncludeDirectories": False},
    "privacy": {"usageStatisticsEnabled": False},
    "telemetry": {"enabled": False, "logPrompts": False},
    "general": {"enableAutoUpdate": False, "enableAutoUpdateNotification": False,
                "checkpointing": {"enabled": False}, "defaultApprovalMode": "default", "plan": {"enabled": False}},
    "security": {"disableYoloMode": True, "disableAlwaysAllow": True, "enablePermanentToolApproval": False,
                 "blockGitExtensions": True, "allowedExtensions": []},
    "experimental": {"enableAgents": False, "autoMemory": False, "taskTracker": False,
                     "extensionManagement": False, "directWebFetch": False, "worktrees": False},
    "useWriteTodos": False,
    "ide": {"enabled": False},
    "model": {"maxSessionTurns": 1},
    "advanced": {"autoConfigureMemory": False, "ignoreLocalEnv": True},
}
DENY_ALL = '[[rule]]\ntoolName = "*"\ndecision = "deny"\npriority = 999\n'
FLAGS = ("--skip-trust", "--approval-mode", "default", "--extensions", "none",
         "--allowed-mcp-server-names", "__rb_no_mcp__", "--output-format", "stream-json")
MODELS = ["gemini-2.5-pro", "gemini-2.5-flash", "gemini-3-pro-preview", "gemini-3-flash-preview"]


class GeminiBackend(CliBackend):
    id = "gemini"
    name = "Gemini CLI"
    home = "https://github.com/google-gemini/gemini-cli"
    cost_note = ("Free with a personal Google account, within a daily request limit (about 1,000 a day per "
                 "Google's docs at the time of writing; limits change).")
    bin_names = ("gemini",)
    bin_env = "RB_GEMINI_BIN"
    verified_version = "0.68.0"
    install_hint = "Install it with `npm i -g @google/gemini-cli`, then run `gemini` once to sign in with Google."
    signin_hint = "Open Terminal, run `gemini` once and choose “Sign in with Google”."

    lists_models_cheaply = True

    def models(self) -> list[str]:
        return list(MODELS)

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        if any(os.environ.get(k) for k in ("GEMINI_API_KEY", "GOOGLE_API_KEY", "GOOGLE_GENAI_USE_VERTEXAI")):
            return True, ""
        home = Path(os.environ.get("GEMINI_CLI_HOME") or Path.home()) / ".gemini"
        if (home / "oauth_creds.json").is_file():
            return True, ""
        return None, self.signin_hint  # credentials may live in the system keychain

    def _env(self, system_md: Path) -> dict:
        return env_without(
            prefixes=("GEMINI_CLI_IDE", "GEMINI_TELEMETRY_"),
            names=("GEMINI_CLI_SYSTEM_SETTINGS_PATH", "GEMINI_CLI_SYSTEM_DEFAULTS_PATH", "GEMINI_SYSTEM_MD",
                   "GEMINI_WRITE_SYSTEM_MD", "GEMINI_SANDBOX", "SEATBELT_PROFILE", "GEMINI_CLI_TRUST_WORKSPACE",
                   "GEMINI_CLI_TRUSTED_FOLDERS_PATH", "GEMINI_YOLO", "GEMINI_CLI_SURFACE"),
            force={"GEMINI_CLI_TRUST_WORKSPACE": "true",  # trusts only the private folder we run in
                   "GEMINI_SYSTEM_MD": str(system_md), "GEMINI_TELEMETRY_ENABLED": "false"})

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        write_private(workdir / ".gemini" / "settings.json", json.dumps(SETTINGS, indent=1))
        rb = workdir / ".rb"
        policy = write_private(rb / "deny-all.toml", DENY_ALL)
        system_md = write_private(rb / "system.md", system)
        cmd = [path, *FLAGS, "--admin-policy", str(policy)]
        if model:
            cmd += ["-m", model]
        cmd += ["-p", ""]
        return Invocation(cmd, prompt, self._env(system_md))

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = ev.get("type")
        if t in ("tool_use", "tool_result"):
            raise tool_guard(self.name, str(ev.get("tool_name") or ev.get("name") or t))
        if t == "message" and ev.get("role") == "assistant":
            text = str(ev.get("content") or "")
            if ev.get("delta"):
                return text
            # a whole message (older CLIs): only what wasn't streamed already
            em = state.get("emitted", "")
            return text[len(em):] if text.startswith(em) else text
        if t == "error" and ev.get("severity") == "error":
            state["error"] = str(ev.get("message") or "error")
        elif t == "result" and ev.get("status") not in (None, "success"):
            err = ev.get("error") or {}
            state["error"] = str(err.get("message") if isinstance(err, dict) else err) or "the run failed"
        return ""

    def finish(self, state: dict, returncode: int | None, stderr: str) -> None:
        if returncode == 53:
            raise AgentError("Gemini CLI stopped: it tried to take more than one turn (research_brain allows one).")
        super().finish(state, returncode, stderr)


register(GeminiBackend())

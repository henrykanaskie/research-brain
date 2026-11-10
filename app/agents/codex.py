"""OpenAI Codex CLI (`codex`, npm @openai/codex), on the user's ChatGPT sign-in.

Verified against codex-cli 0.167.1 (2026-11-10), by pointing the real CLI at a local mock model server and
reading the request it sent: with the flags below the model is offered no shell, no file, no web, no MCP,
no image, no sub-agent and no skill tools (the one remaining tool, `request_user_input`, only asks the
user a question and is inert in `exec`), and its coding-agent system prompt is replaced by ours.

    codex exec --json --strict-config --ephemeral --ignore-user-config --ignore-rules --skip-git-repo-check
               --sandbox read-only --cd <private dir> --color never [-m MODEL]
               -c approval_policy="never" -c web_search="disabled" -c project_doc_max_bytes=0 -c mcp_servers={}
               -c skills.bundled.enabled=false -c skills.include_instructions=false
               -c model_instructions_file=<private file> -c history.persistence="none" -c analytics.enabled=false
               -c feedback.enabled=false -c check_for_update_on_startup=false
               --disable shell_tool --disable unified_exec ... (every tool feature, see FEATURES_OFF)  -
    (prompt on stdin)

  --strict-config               an unknown -c key is an error, so a renamed/removed setting fails closed
                                instead of being ignored (an unknown --disable feature is an error too)
  --ignore-user-config          ~/.codex/config.toml (its MCP servers, profiles, hooks, notify) not loaded;
                                sign-in still comes from CODEX_HOME
  --ignore-rules                no execpolicy .rules files
  -s read-only + approval never  if a command tool ever reappeared it could not write and never escalates
  --ephemeral, history none     no session files / history.jsonl
  Tool guard: every --json item that is not agent_message / reasoning / error (command_execution,
  file_change, mcp_tool_call, web_search, ...) kills the process group (ToolGuardError).

Residual risk: Codex still adds the user's own global ~/.codex/AGENTS.md to the prompt (there is no switch
for it in 0.167); it can steer the answer but cannot add tools. OPENAI_API_KEY / CODEX_API_KEY are not
passed on, so a call never bills an API key instead of the ChatGPT plan.
"""
from __future__ import annotations

import json
from pathlib import Path

from . import register
from ._cli import CliBackend, Invocation, env_without, json_line, tool_guard, write_private

FEATURES_OFF = ("shell_tool", "unified_exec", "view_image", "multi_agent", "goals", "apps", "plugins", "hooks",
                "memories", "image_generation", "browser_use", "computer_use", "in_app_browser", "tool_suggest",
                "sleep_tool", "skill_search", "shell_snapshot", "remote_plugin", "code_mode_host")
CONFIG = ('approval_policy="never"', 'web_search="disabled"', "project_doc_max_bytes=0", "mcp_servers={}",
          "skills.bundled.enabled=false", "skills.include_instructions=false", 'history.persistence="none"',
          "analytics.enabled=false", "feedback.enabled=false", "check_for_update_on_startup=false")
FLAGS = ("--json", "--strict-config", "--ephemeral", "--ignore-user-config", "--ignore-rules",
         "--skip-git-repo-check", "--sandbox", "read-only", "--color", "never")
TEXT_ITEMS = {"agent_message"}
QUIET_ITEMS = {"reasoning", "error"}   # error items are warnings (e.g. unknown model metadata)


class CodexBackend(CliBackend):
    id = "codex"
    name = "Codex CLI"
    home = "https://developers.openai.com/codex/cli"
    cost_note = "Uses your ChatGPT plan (sign in with ChatGPT); counts against that plan's Codex limits."
    bin_names = ("codex",)
    bin_env = "RB_CODEX_BIN"
    verified_version = "0.167.1"
    install_hint = "Install it with `npm i -g @openai/codex` (or `brew install codex`), then run `codex` once to sign in."
    signin_hint = "Open Terminal, run `codex` and sign in with ChatGPT."

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        from .base import run_process
        try:
            code, out, err = run_process([path, "login", "status"], timeout=15, env=self._env())
        except Exception:
            return None, self.signin_hint
        text = f"{out}\n{err}".lower()
        if code == 0 and "logged in" in text and "not logged in" not in text:
            return True, ""
        if "not logged in" in text:
            return False, self.signin_hint
        return None, self.signin_hint

    def _env(self) -> dict:
        # no API keys (the ChatGPT sign-in is used), nothing that points Codex at other config
        return env_without(prefixes=("CODEX_",), keep=("CODEX_HOME",), keep_api_keys=False,
                           names=("CODEX_API_KEY", "OPENAI_API_KEY", "OPENAI_BASE_URL"))

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        instr = write_private(workdir / ".rb" / "instructions.md", system)
        cmd = [path, "exec", *FLAGS, "--cd", str(workdir)]
        if model:
            cmd += ["-m", model]
        for c in (*CONFIG, f"model_instructions_file={json.dumps(str(instr))}"):
            cmd += ["-c", c]
        for f in FEATURES_OFF:
            cmd += ["--disable", f]
        cmd.append("-")  # the prompt comes on stdin (no argv length limit)
        return Invocation(cmd, prompt, self._env())

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = ev.get("type")
        if t in ("item.started", "item.updated", "item.completed"):
            item = ev.get("item") or {}
            it = item.get("type")
            if it not in TEXT_ITEMS and it not in QUIET_ITEMS:
                raise tool_guard(self.name, str(it or "unknown item"))
            if t == "item.completed" and it == "agent_message":
                text = str(item.get("text") or "")
                if not text:
                    return ""
                sep = "\n\n" if state.get("emitted") else ""
                return sep + text
            return ""
        if t == "turn.failed":
            state["error"] = str((ev.get("error") or {}).get("message") or "the turn failed")
        elif t == "error":  # may be a retried stream error; fatal only if the turn never completes
            state["last_error"] = str(ev.get("message") or "error")
        elif t == "turn.completed":
            state["completed"] = True
        return ""

    def finish(self, state: dict, returncode: int | None, stderr: str) -> None:
        if not state.get("completed") and not state.get("error") and state.get("last_error"):
            state["error"] = state["last_error"]
        super().finish(state, returncode, stderr)


register(CodexBackend())

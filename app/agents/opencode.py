"""OpenCode (`opencode`, npm opencode-ai), with whatever provider the user signed in to (`opencode auth
login`) or OpenCode's own free models.

Verified against opencode 1.19.5 (2026-11-10) by running the real CLI against a local mock model server
with a planted user config (an MCP server, a global AGENTS.md) and a project AGENTS.md, and reading the
requests: with the lockdown below the model is offered no tools ("Available tools: ." when it tries one;
by default it gets bash, edit, read, write, grep, glob, webfetch, task, skill, todowrite), no MCP server
starts, no instruction file is loaded and the system prompt is ours. `opencode run` opens no listening
port (checked).

    opencode run --pure --agent rb --format json --title research_brain [-m provider/model]
    (prompt on stdin)
  env XDG_CONFIG_HOME=<private dir>/cfg      the user's ~/.config/opencode (opencode.json with its MCP
                                             servers, plugins, agents, commands, AGENTS.md) is not read;
                                             sign-ins (auth.json in the data dir) still are
      OPENCODE_CONFIG=<private dir>/.rb/opencode.json   our config: permission "*" deny, tools "*" off,
                                             mcp {}, instructions [], plugin [], share disabled,
                                             autoupdate/lsp/formatter/snapshot off, and a primary agent
                                             "rb" whose prompt is our system prompt, permission deny-all
      OPENCODE_CONFIG_DIR=<private dir>/cfg, OPENCODE_PERMISSION={"*":"deny"},
      OPENCODE_DISABLE_PROJECT_CONFIG, OPENCODE_DISABLE_CLAUDE_CODE (no ~/.claude/CLAUDE.md or skills),
      OPENCODE_DISABLE_DEFAULT_PLUGINS, OPENCODE_PURE, OPENCODE_DISABLE_AUTOUPDATE,
      OPENCODE_DISABLE_LSP_DOWNLOAD, OPENCODE_DISABLE_EXTERNAL_SKILLS; every other OPENCODE_* variable
      (e.g. OPENCODE_AUTO_SHARE, OPENCODE_SERVER_PASSWORD) is dropped. Never --auto.
  Tool guard: any tool_use event kills the process group (ToolGuardError).

Residual risk: OpenCode stores the session in its local database (no switch to turn that off). Custom
providers defined only in the user's opencode.json are not available (the user config is not read);
providers signed in with `opencode auth login` and API keys in the environment still work.
"""
from __future__ import annotations

import json
import os
from pathlib import Path

from . import register
from ._cli import CliBackend, Invocation, env_without, json_line, tool_guard, write_private
from .base import run_process

AGENT = "rb"
FLAGS = ("run", "--pure", "--agent", AGENT, "--format", "json", "--title", "research_brain")
ENV_FORCE = {"OPENCODE_PERMISSION": json.dumps({"*": "deny"}), "OPENCODE_DISABLE_PROJECT_CONFIG": "1",
             "OPENCODE_DISABLE_CLAUDE_CODE": "1", "OPENCODE_DISABLE_CLAUDE_CODE_PROMPT": "1",
             "OPENCODE_DISABLE_CLAUDE_CODE_SKILLS": "1", "OPENCODE_DISABLE_DEFAULT_PLUGINS": "1",
             "OPENCODE_PURE": "1", "OPENCODE_DISABLE_AUTOUPDATE": "1", "OPENCODE_DISABLE_LSP_DOWNLOAD": "1",
             "OPENCODE_DISABLE_EXTERNAL_SKILLS": "1"}


def lockdown_config(system: str, extra: dict | None = None) -> dict:
    cfg = {
        "$schema": "https://opencode.ai/config.json",
        "permission": {"*": "deny"},
        "tools": {"*": False},
        "mcp": {},
        "instructions": [],
        "plugin": [],
        "share": "disabled",
        "autoupdate": False,
        "lsp": False,
        "formatter": False,
        "snapshot": False,
        "agent": {AGENT: {"mode": "primary", "description": "research_brain (no tools)", "prompt": system,
                          "permission": {"*": "deny"}, "tools": {"*": False}}},
        "default_agent": AGENT,
    }
    if extra:  # tests only (a mock provider); never set by the app
        cfg.update(extra)
    return cfg


def data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (Path.home() / ".local" / "share")) / "opencode"


class OpenCodeBackend(CliBackend):
    id = "opencode"
    name = "OpenCode"
    home = "https://opencode.ai"
    cost_note = ("Free models are offered through OpenCode (which ones changes), or it uses a provider you "
                 "sign in to with `opencode auth login`.")
    bin_names = ("opencode",)
    bin_env = "RB_OPENCODE_BIN"
    extra_paths = ("~/.opencode/bin/opencode",)
    verified_version = "1.19.5"
    install_hint = "Install it with `npm i -g opencode-ai` (or `brew install sst/tap/opencode`)."
    signin_hint = "Free models need no sign-in; for others run `opencode auth login` in Terminal."
    test_config: dict | None = None  # tests only

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        try:
            data = json.loads((data_home() / "auth.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None, self.signin_hint
        return (True, "") if isinstance(data, dict) and data else (None, self.signin_hint)

    def _env(self, workdir: Path, config: Path) -> dict:
        cfg_home = workdir / "cfg"
        (cfg_home / "opencode").mkdir(parents=True, exist_ok=True, mode=0o700)
        return env_without(prefixes=("OPENCODE_",), force={
            **ENV_FORCE, "OPENCODE_CONFIG": str(config), "OPENCODE_CONFIG_DIR": str(cfg_home / "opencode"),
            "XDG_CONFIG_HOME": str(cfg_home)})

    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        config = write_private(workdir / ".rb" / "opencode.json",
                               json.dumps(lockdown_config(system, self.test_config), indent=1))
        cmd = [path, *FLAGS]
        if model:
            cmd += ["-m", model]
        return Invocation(cmd, prompt, self._env(workdir, config))

    def models(self) -> list[str]:
        path = self.find()
        if not path:
            return []
        from .base import private_workdir
        import shutil
        wd = private_workdir("opencode_models_")
        try:
            config = write_private(wd / ".rb" / "opencode.json", json.dumps(lockdown_config("")))
            code, out, _ = run_process([path, "models"], timeout=30, cwd=wd, env=self._env(wd, config))
        except Exception:
            return []
        finally:
            shutil.rmtree(wd, ignore_errors=True)
        if code != 0:
            return []
        return [ln.strip() for ln in out.splitlines() if "/" in ln and " " not in ln.strip()][:200]

    def parse(self, line: str, state: dict) -> str:
        ev = json_line(line)
        if ev is None:
            return ""
        t = ev.get("type")
        part = ev.get("part") if isinstance(ev.get("part"), dict) else {}
        if t == "tool_use" or part.get("type") == "tool":
            raise tool_guard(self.name, str(part.get("tool") or "tool"))
        if t == "text":
            text = str(part.get("text") or "")
            return (("\n\n" if state.get("emitted") else "") + text) if text else ""
        if t == "error":
            err = ev.get("error") or {}
            msg = ""
            if isinstance(err, dict):
                d = err.get("data") if isinstance(err.get("data"), dict) else {}
                msg = str(d.get("message") or err.get("message") or err.get("name") or "")
            state["error"] = msg or str(err) or "error"
        return ""


register(OpenCodeBackend())

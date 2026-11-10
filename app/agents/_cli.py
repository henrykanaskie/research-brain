"""Shared machinery for the locked-down agent-CLI adapters (codex, gemini, copilot, opencode, qwen, cursor).

Not an adapter itself (the leading underscore keeps the registry from treating it as one). Each adapter
subclasses CliBackend and supplies:

  bin_names / bin_env / extra_paths   how to find the executable (bin_env, e.g. RB_CODEX_BIN, wins)
  command(path, prompt, system, model, workdir) -> Invocation(cmd, stdin, env)
                                      the strictest non-interactive command line, the stdin text and
                                      the scrubbed environment; may write lockdown files into `workdir`
  parse(line, state) -> str           one stdout line -> new answer text; raises ToolGuardError on any
                                      tool event (the process group is then killed by StreamProcess)
  signed_in(path) -> (bool|None, note)  a cheap sign-in check that never calls a model

The common ask_stream() runs every call in a fresh private 0700 working directory (base.private_workdir:
nothing in it but the lockdown files the adapter writes), with a hard timeout, kills the whole process
group on timeout / tool event / early close, and turns failures into plain-language AgentErrors.
"""
from __future__ import annotations

import json
import os
import re
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterator

from . import model_for
from .base import (AgentError, Backend, Detection, StreamProcess, ToolGuardError, clean_env, find_executable,
                   private_workdir, version_of)

# The instructions used when the caller gives no system prompt. Every adapter replaces the CLI's own
# "you are a coding agent with these tools" prompt wherever the CLI allows it.
DEFAULT_SYSTEM = ("You are a careful research assistant inside research_brain. You have no tools: you cannot "
                  "run commands, read or write files, or browse. Answer in plain text (Markdown is fine) from "
                  "the material in the message.")

_AUTH_HINTS = ("not logged in", "not signed in", "login required", "please log in", "please login", "unauthorized",
               "unauthenticated", "authenticate", "authentication", "sign in", "log in", "401", "credentials")
_LIMIT_HINTS = ("rate limit", "quota", "usage limit", "too many requests", "429", "resource_exhausted",
                "exceeded", "limit reached")


@dataclass
class Invocation:
    cmd: list[str]
    stdin: str = ""
    env: dict = field(default_factory=dict)


def env_without(prefixes: tuple[str, ...] = (), names: tuple[str, ...] = (), keep: tuple[str, ...] = (),
                force: dict | None = None, keep_api_keys: bool = True) -> dict:
    """clean_env() minus every variable starting with one of `prefixes` (except those in `keep`) and the
    `names`, plus `force`."""
    env = clean_env(keep_api_keys=keep_api_keys)
    for k in list(env):
        if k in keep:
            continue
        if k in names or (prefixes and k.startswith(prefixes)):
            env.pop(k, None)
    if force:
        env.update(force)
    return env


def write_private(path: Path, text: str) -> Path:
    """Write a lockdown file readable only by this user (0600) inside the private workdir."""
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    fd = os.open(str(path), os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(text)
    return path


def inside_git_repo(path: Path) -> Path | None:
    """The folder holding a `.git` at or above `path`, if any (some CLIs read the repository root's
    config and instruction files)."""
    for d in [path, *path.resolve().parents]:
        if (d / ".git").exists():
            return d
    return None


def json_line(line: str) -> dict | None:
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        ev = json.loads(line)
    except (json.JSONDecodeError, ValueError):
        return None
    return ev if isinstance(ev, dict) else None


def tool_guard(name: str, what: str) -> ToolGuardError:
    return ToolGuardError(f"Stopped {name}: it tried to use a tool ({what}), which research_brain never allows. "
                          "The process was killed at once. If this keeps happening, update research_brain or "
                          "choose another AI agent in Settings.")


SAFE_NAME = re.compile(r"^[A-Za-z0-9_.@:/ -]{1,120}$")


class CliBackend(Backend):
    kind = "cli"
    bin_names: tuple[str, ...] = ()
    bin_env: str = ""
    extra_paths: tuple[str, ...] = ()
    version_args: tuple[str, ...] = ("--version",)
    verified_version: str = ""        # the CLI version the lockdown was verified against (docs/agents.md)
    install_hint: str = ""            # e.g. "Install it with `npm i -g @openai/codex`."
    signin_hint: str = ""             # e.g. "Run `codex` once and sign in with ChatGPT."
    experimental: bool = False        # off unless named in RB_EXPERIMENTAL_AGENTS
    max_arg_prompt: int = 0           # >0: the prompt goes in argv and may be at most this many characters

    # ------------------------------------------------------------ to override
    def command(self, path: str, prompt: str, system: str, model: str | None, workdir: Path) -> Invocation:
        raise NotImplementedError

    def parse(self, line: str, state: dict) -> str:
        raise NotImplementedError

    def signed_in(self, path: str) -> tuple[bool | None, str]:
        return None, self.signin_hint

    def finish(self, state: dict, returncode: int | None, stderr: str) -> None:
        """Called after the process ended normally; raise AgentError for a failed run."""
        err = state.get("error") or ""
        if returncode not in (0, None) or err:
            raise self.error(err or stderr or f"exit code {returncode}")
        if not state.get("emitted", "").strip():
            raise AgentError(f"{self.name} returned an empty answer. Try again."
                             + (f" ({stderr[-300:]})" if stderr else ""))

    # ------------------------------------------------------------ common
    def enabled(self) -> bool:
        if not self.experimental:
            return True
        want = {s.strip().lower() for s in os.environ.get("RB_EXPERIMENTAL_AGENTS", "").split(",")}
        return self.id in want or "all" in want

    def find(self) -> str | None:
        return find_executable(self.bin_names, self.extra_paths, self.bin_env)

    def model(self) -> str | None:
        return model_for(self.id) or os.environ.get(f"RB_{self.id.upper().replace('-', '_')}_MODEL", "").strip() or None

    def detect(self) -> Detection:
        path = self.find()
        if not path:
            return Detection(False, note=f"Couldn't find `{self.bin_names[0]}`. {self.install_hint}".strip())
        ok, ver = version_of(path, self.version_args)
        if not ok:
            return Detection(False, path=path, note=f"`{os.path.basename(path)} --version` failed: {ver[:200]}")
        if not self.enabled():
            return Detection(False, version=ver, path=path, note=(
                f"Experimental and off by default: research_brain can't fully remove {self.name}'s tools. "
                f"Set RB_EXPERIMENTAL_AGENTS={self.id} to try it anyway (see docs/agents.md)."))
        try:
            signed, note = self.signed_in(path)
        except Exception:  # a detection helper must never break the settings page
            signed, note = None, self.signin_hint
        return Detection(True, version=ver, path=path, signed_in=signed, note=note)

    def error(self, msg: str) -> AgentError:
        msg = (msg or "").strip()
        low = msg.lower()
        if any(h in low for h in _LIMIT_HINTS):
            return AgentError(f"{self.name} usage limit reached: {msg[:300]}")
        if any(h in low for h in _AUTH_HINTS):
            return AgentError(f"{self.name} isn't signed in. {self.signin_hint} (It said: {msg[:300]})")
        return AgentError(f"{self.name} error: {msg[:500]}")

    def ask_stream(self, prompt: str, *, system: str = "", timeout: float = 180) -> Iterator[str]:
        if not self.enabled():
            raise AgentError(f"{self.name} is experimental and off by default. Set RB_EXPERIMENTAL_AGENTS={self.id} "
                             "to enable it (see docs/agents.md for what that means).")
        path = self.find()
        if not path:
            raise AgentError(f"Couldn't find `{self.bin_names[0]}`. {self.install_hint}".strip())
        if self.max_arg_prompt and len(prompt) + len(system) > self.max_arg_prompt:
            raise AgentError(f"This request is too long for {self.name} (it only takes the prompt as a command-line "
                             "argument). Choose another AI agent for long papers.")
        workdir = private_workdir(f"{self.id}_")
        try:
            inv = self.command(path, prompt, system or DEFAULT_SYSTEM, self.model(), workdir)
            state: dict = {"emitted": ""}
            with StreamProcess(inv.cmd, stdin=inv.stdin, timeout=timeout, cwd=workdir, env=inv.env,
                               label=self.name) as sp:
                for line in sp.lines():
                    piece = self.parse(line, state)   # raises ToolGuardError -> group killed on exit
                    if piece:
                        state["emitted"] += piece
                        yield piece
            self.finish(state, sp.returncode, sp.stderr)
        finally:
            shutil.rmtree(workdir, ignore_errors=True)

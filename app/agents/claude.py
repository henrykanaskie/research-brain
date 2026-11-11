"""Claude Code (the `claude` CLI), wrapping app/claude_cli.py.

All the lockdown lives in claude_cli: no tools (`--tools ""`), no MCP, no settings/hooks/CLAUDE.md
(`--setting-sources ""`), no saved session, an empty private working directory, and the init-event tool
guard (any tool in the session kills the process before it does anything). Claude also keeps its own,
faster Research path (claude_cli.run_agent with only research_brain's MCP tools; `native_research`),
but the universal app-driven loop (app/agents/research.py) works with it too.
"""
from __future__ import annotations

import os
from typing import Iterator

from .. import claude_cli
from . import model_for, register
from .base import AgentError, Backend, Detection, ToolGuardError


def _convert(e: claude_cli.ClaudeError) -> AgentError:
    cls = ToolGuardError if isinstance(e, claude_cli.ToolGuardError) else AgentError
    err = cls(str(e))
    err.__cause__ = e
    return err


class ClaudeBackend(Backend):
    id = "claude"
    name = "Claude Code"
    kind = "cli"
    home = "https://docs.anthropic.com/en/docs/claude-code"
    cost_note = "Uses your Claude subscription through the CLI's own sign-in; no API key."
    native_research = True  # server.py may use claude_cli.run_agent (MCP tools) instead of the JSON loop

    def detect(self) -> Detection:
        st = claude_cli.status()
        if os.environ.get("RB_FAKE_CLAUDE"):
            return Detection(True, version="fake", path="fake", signed_in=True, note=st.get("detail", ""))
        detail = st.get("detail") or ""
        version = detail.removeprefix("Claude Code ").strip() if st.get("available") else ""
        note = "" if st.get("available") else detail
        if st.get("available"):
            note = "If answers fail, open Terminal, run `claude` once and sign in."
        return Detection(bool(st.get("available")), version=version, path=st.get("path") or "", signed_in=None,
                         note=note)

    lists_models_cheaply = True

    def models(self) -> list[str]:
        # the CLI's own aliases; they always point at the current model of each family
        return ["sonnet", "opus", "haiku"]

    def _model(self) -> str | None:
        return model_for(self.id) or None

    # The server passes instruction and context separately (the instruction is the -p argument, the long
    # context goes on stdin), exactly as before the agents package existed.
    def ask_parts(self, instruction: str, context: str = "", system: str = "") -> str:
        try:
            m = self._model()
            return claude_cli.ask(instruction, context, system, model=m) if m else \
                claude_cli.ask(instruction, context, system)
        except claude_cli.ClaudeError as e:
            raise _convert(e)

    def stream_parts(self, instruction: str, context: str = "", system: str = "",
                     heartbeat: float | None = None) -> Iterator[str]:
        try:
            yield from claude_cli.ask_stream(instruction, context, system, heartbeat=heartbeat, model=self._model())
        except claude_cli.ClaudeError as e:
            raise _convert(e)

    def ask(self, prompt: str, *, system: str = "", timeout: float = 180) -> str:
        return self.ask_parts("Follow the request on stdin.", prompt, system)

    def ask_stream(self, prompt: str, *, system: str = "", timeout: float = 180) -> Iterator[str]:
        yield from self.stream_parts("Follow the request on stdin.", prompt, system)


register(ClaudeBackend())

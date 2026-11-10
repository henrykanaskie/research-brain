"""Ollama (https://ollama.com): models running on this computer, at http://127.0.0.1:11434 by default.

Free and offline. /api/tags lists installed models (and doubles as detection), /api/chat streams the
answer as newline-delimited JSON. The request never carries a `tools` field; a reply that contains
tool calls anyway is refused (ToolGuardError). Loopback addresses only (app/agents/localhttp.py).
Address: settings `agent_urls.ollama`, else RB_OLLAMA_URL, else OLLAMA_HOST if it is loopback.
"""
from __future__ import annotations

import json
import os
import shutil
from typing import Iterator

from . import model_for, register, url_for
from .base import AgentError, Backend, Detection, ToolGuardError
from .localhttp import check_loopback, get_json, post_lines

DEFAULT_URL = "http://127.0.0.1:11434"


def messages(prompt: str, system: str) -> list[dict]:
    msgs = [{"role": "system", "content": system}] if system else []
    return msgs + [{"role": "user", "content": prompt}]


class OllamaBackend(Backend):
    id = "ollama"
    name = "Ollama"
    kind = "local-server"
    home = "https://ollama.com/download"
    cost_note = "Free and offline. Needs a capable machine; answers are weaker than the big hosted agents."

    def check_url(self, url: str) -> str:
        return check_loopback(url)

    def base_url(self) -> str:
        for cand in (url_for(self.id), os.environ.get("RB_OLLAMA_URL", "")):
            if cand.strip():
                return check_loopback(cand)
        host = os.environ.get("OLLAMA_HOST", "").strip()
        if host:
            if "://" not in host:
                host = "http://" + host
            try:
                return check_loopback(host)
            except AgentError:
                pass  # e.g. 0.0.0.0 (a bind address): use the default
        return DEFAULT_URL

    def models(self) -> list[str]:
        data = get_json(self.base_url() + "/api/tags", timeout=3)
        rows = data.get("models") if isinstance(data, dict) else None
        return [str(m.get("name") or m.get("model")) for m in rows or [] if isinstance(m, dict) and (m.get("name") or m.get("model"))]

    def model(self) -> str:
        chosen = model_for(self.id)
        if chosen:
            return chosen
        names = self.models()
        if not names:
            raise AgentError("Ollama has no models yet. Download one in Terminal, for example `ollama pull llama3.1`.")
        return names[0]

    def detect(self) -> Detection:
        exe = shutil.which("ollama") or ""
        try:
            names = self.models()
        except AgentError:
            note = ("Ollama is installed but not running. Open the Ollama app, or run `ollama serve`."
                    if exe else "Install Ollama, then download a model with `ollama pull <model>`.")
            return Detection(False, path=exe, signed_in=None, note=note)
        version = ""
        try:
            v = get_json(self.base_url() + "/api/version", timeout=2)
            version = str(v.get("version") or "") if isinstance(v, dict) else ""
        except AgentError:
            pass
        if not names:
            return Detection(False, version=version, path=exe or self.base_url(), signed_in=None,
                             note="Ollama is running but has no models. Download one, e.g. `ollama pull llama3.1`.")
        chosen = model_for(self.id)
        note = f"Model: {chosen or names[0]}." + ("" if not chosen or chosen in names else " (not installed)")
        return Detection(True, version=version, path=self.base_url(), signed_in=True, note=note)

    def ask_stream(self, prompt: str, *, system: str = "", timeout: float = 180) -> Iterator[str]:
        body = {"model": self.model(), "messages": messages(prompt, system), "stream": True}
        got = False
        for line in post_lines(self.base_url() + "/api/chat", body, timeout):
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("error"):
                raise AgentError(f"Ollama: {str(ev['error'])[:400]}")
            msg = ev.get("message") or {}
            if msg.get("tool_calls"):
                raise ToolGuardError("Stopped the local model: it tried to call a tool, which research_brain doesn't allow.")
            text = msg.get("content") or ""
            if text:
                got = True
                yield text
            if ev.get("done"):
                break
        if not got:
            raise AgentError("Ollama returned an empty answer.")


register(OllamaBackend())

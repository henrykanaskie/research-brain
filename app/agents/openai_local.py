"""Any OpenAI-compatible model server on this computer: LM Studio (default http://127.0.0.1:1234/v1),
llama.cpp's llama-server, vLLM, LocalAI, Jan, ...

GET /v1/models lists models (and doubles as detection); POST /v1/chat/completions with stream=true
sends Server-Sent Events. No API key and no Authorization header; the request never carries a `tools`
(or `functions`) field, and a reply with tool calls is refused (ToolGuardError). Loopback addresses
only (app/agents/localhttp.py). Address: settings `agent_urls.openai-local`, else RB_OPENAI_LOCAL_URL.
"""
from __future__ import annotations

import json
import os
from typing import Iterator

from . import model_for, register, url_for
from .base import AgentError, Backend, Detection, ToolGuardError
from .localhttp import check_loopback, get_json, post_lines
from .ollama import messages

DEFAULT_URL = "http://127.0.0.1:1234/v1"


class OpenAILocalBackend(Backend):
    id = "openai-local"
    name = "LM Studio or another local server"
    kind = "local-server"
    home = "https://lmstudio.ai"
    cost_note = "Free and offline. Needs a capable machine; answers are weaker than the big hosted agents."

    def check_url(self, url: str) -> str:
        u = check_loopback(url)
        return u if u.endswith("/v1") else u + "/v1"

    def base_url(self) -> str:
        for cand in (url_for(self.id), os.environ.get("RB_OPENAI_LOCAL_URL", "")):
            if cand.strip():
                return self.check_url(cand)
        return DEFAULT_URL

    def models(self) -> list[str]:
        data = get_json(self.base_url() + "/models", timeout=3)
        rows = data.get("data") if isinstance(data, dict) else None
        return [str(m["id"]) for m in rows or [] if isinstance(m, dict) and m.get("id")]

    def model(self) -> str:
        chosen = model_for(self.id)
        if chosen:
            return chosen
        names = self.models()
        if not names:
            raise AgentError("The local server has no model loaded. Load one in LM Studio (or your server) first.")
        return names[0]

    def detect(self) -> Detection:
        try:
            names = self.models()
        except AgentError:
            return Detection(False, signed_in=None,
                             note=f"No server answered at {self.base_url()}. In LM Studio, start the local server "
                                  "(Developer tab), or set the address of your own server.")
        if not names:
            return Detection(False, path=self.base_url(), note="The server is running but has no model loaded.")
        chosen = model_for(self.id)
        return Detection(True, path=self.base_url(), signed_in=True, note=f"Model: {chosen or names[0]}.")

    def ask_stream(self, prompt: str, *, system: str = "", timeout: float = 180) -> Iterator[str]:
        body = {"model": self.model(), "messages": messages(prompt, system), "stream": True}
        got = False
        for line in post_lines(self.base_url() + "/chat/completions", body, timeout):
            if not line.startswith("data:"):
                continue
            data = line[5:].strip()
            if data == "[DONE]":
                break
            try:
                ev = json.loads(data)
            except ValueError:
                continue
            if not isinstance(ev, dict):
                continue
            if ev.get("error"):
                err = ev["error"]
                raise AgentError(f"Local server: {str(err.get('message') if isinstance(err, dict) else err)[:400]}")
            for ch in ev.get("choices") or []:
                if not isinstance(ch, dict):
                    continue
                d = ch.get("delta") or ch.get("message") or {}
                if d.get("tool_calls") or d.get("function_call") or ch.get("finish_reason") in ("tool_calls", "function_call"):
                    raise ToolGuardError("Stopped the local model: it tried to call a tool, which research_brain doesn't allow.")
                text = d.get("content") or ""
                if text:
                    got = True
                    yield text
        if not got:
            raise AgentError("The local server returned an empty answer.")


register(OpenAILocalBackend())

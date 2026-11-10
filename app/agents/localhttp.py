"""HTTP to model servers on this computer only (Ollama, LM Studio, llama.cpp, vLLM, ...).

- Loopback only: the URL's host must be 127.0.0.1, localhost or ::1 (localhost is connected as
  127.0.0.1 so a hosts-file entry can't send it elsewhere); no user:password@ part; http or https.
- No proxies (HTTP(S)_PROXY is ignored, so the prompt never passes through one) and no redirects.
- Hard timeouts: per read, plus an overall deadline checked between streamed lines.
"""
from __future__ import annotations

import json
import time
import urllib.error
import urllib.request
from typing import Iterator
from urllib.parse import urlsplit, urlunsplit

from .base import AgentError

LOOPBACK = ("127.0.0.1", "localhost", "::1")


def check_loopback(url: str) -> str:
    """The URL, normalised (no trailing slash), or AgentError if it isn't a loopback http(s) URL."""
    url = (url or "").strip()
    try:
        parts = urlsplit(url)
        host = (parts.hostname or "").lower()
        parts.port  # noqa: B018  (raises on a bad port)
    except ValueError:
        raise AgentError(f"Not a valid address: {url[:100]!r}")
    if parts.scheme not in ("http", "https"):
        raise AgentError("The server address must start with http:// (for example http://127.0.0.1:1234).")
    if host not in LOOPBACK:
        raise AgentError("research_brain only talks to model servers on this computer "
                         "(127.0.0.1, localhost or ::1), never to an address elsewhere.")
    if parts.username or parts.password:
        raise AgentError("The server address can't contain a user name or password.")
    if parts.query or parts.fragment:
        raise AgentError("The server address can't contain ? or #.")
    return urlunsplit((parts.scheme, parts.netloc, parts.path.rstrip("/"), "", ""))


def _connect_url(url: str) -> str:
    parts = urlsplit(url)
    if (parts.hostname or "").lower() == "localhost":
        netloc = "127.0.0.1" + (f":{parts.port}" if parts.port else "")
        parts = parts._replace(netloc=netloc)
    return urlunsplit(parts)


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *a, **k):
        raise AgentError("The local model server tried to redirect the request elsewhere; refused.")


_opener = urllib.request.build_opener(urllib.request.ProxyHandler({}), _NoRedirect())


def _request(url: str, body: dict | None, timeout: float):
    full = check_loopback(url)
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(_connect_url(full), data=data, method="POST" if data is not None else "GET",
                                 headers={"Content-Type": "application/json", "Accept": "application/json"})
    try:
        return _opener.open(req, timeout=timeout)
    except urllib.error.HTTPError as e:
        try:
            detail = e.read().decode("utf-8", "replace")[:400]
        except Exception:
            detail = ""
        try:
            j = json.loads(detail)
            detail = (j.get("error") if isinstance(j.get("error"), str) else
                      (j.get("error") or {}).get("message") if isinstance(j.get("error"), dict) else None) or detail
        except (ValueError, AttributeError):
            pass
        raise AgentError(f"The local model server answered {e.code}: {detail or e.reason}")
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        raise AgentError(f"Couldn't reach the local model server at {full} ({reason}). Is it running?")


def get_json(url: str, timeout: float = 3.0):
    with _request(url, None, timeout) as r:
        raw = r.read(4 << 20)
    try:
        return json.loads(raw.decode("utf-8", "replace"))
    except ValueError:
        raise AgentError("The local model server sent something that isn't JSON.")


def post_lines(url: str, body: dict, timeout: float) -> Iterator[str]:
    """POST JSON and yield the response's lines (decoded, stripped) until it ends or `timeout` passes."""
    deadline = time.monotonic() + timeout
    resp = _request(url, body, timeout=min(timeout, 60.0))
    try:
        while True:
            if time.monotonic() > deadline:
                raise AgentError(f"The local model took longer than {int(timeout)}s and was stopped.")
            try:
                line = resp.readline(1 << 20)
            except (OSError, TimeoutError) as e:
                raise AgentError(f"The local model server stopped responding ({e}).")
            if not line:
                return
            text = line.decode("utf-8", "replace").strip()
            if text:
                yield text
    finally:
        try:
            resp.close()
        except Exception:
            pass

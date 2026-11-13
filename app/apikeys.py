"""Optional free keys for the paper databases, saved on this computer only.

Semantic Scholar answers keyless requests from a pool every user shares, so a busy moment returns HTTP 429
("rate limited"). A free personal key gives this app its own allowance. OpenAlex needs no key at all; a free
key there only raises the daily allowance. Keys live in settings.json in the app's data folder (created
readable only by you) and are sent only to their own service. An environment variable wins over a saved key.
"""
from __future__ import annotations

import os
import re

SERVICES = {
    "s2": {"env": "S2_API_KEY", "setting": "s2_api_key", "name": "Semantic Scholar",
           "get": "https://www.semanticscholar.org/product/api#api-key-form"},
    "openalex": {"env": "OPENALEX_API_KEY", "setting": "openalex_api_key", "name": "OpenAlex",
                 "get": "https://openalex.org/settings/api"},
}
_OK = re.compile(r"^[A-Za-z0-9_\-.]{8,128}$")


def get(service: str) -> str:
    s = SERVICES[service]
    env = os.environ.get(s["env"], "").strip()
    if env:
        return env
    try:
        from . import paths
        v = paths.load_settings().get(s["setting"]) or ""
    except Exception:
        v = ""
    return v.strip() if isinstance(v, str) else ""


def status() -> dict:
    out = {}
    for sid, s in SERVICES.items():
        k = get(sid)
        out[sid] = {"name": s["name"], "set": bool(k), "from_env": bool(os.environ.get(s["env"], "").strip()),
                    "hint": f"…{k[-4:]}" if k else "", "get_url": s["get"], "env": s["env"]}
    return out


def save(service: str, key: str | None) -> dict:
    """Save (or with "" / None, forget) a key. Raises ValueError for something that can't be a key."""
    from . import paths
    if service not in SERVICES:
        raise ValueError("Unknown service")
    key = (key or "").strip()
    if key and not _OK.match(key):
        raise ValueError("That doesn't look like an API key: paste just the key, with no spaces.")
    paths.save_settings({SERVICES[service]["setting"]: key or None})
    return status()

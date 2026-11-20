"""HTTP endpoints for updates (app/updates.py). Installed by app/server.py.

    GET  /api/update?force=false  {current, latest, newer, notes, url, published, checked, enabled, system,
                                   can_install, reason, installing, result}
                                  checks at most once a day unless force; `result` is how the last update from
                                  the app went (once, until POST /api/update/ack)
    POST /api/update/install      {} -> {ok, from, to}: downloads the new version, starts the installer and
                                  stops research brain; the installer starts it again (about a minute)
    POST /api/update/ack          {} -> {ok}: the app has shown `result`
"""
from __future__ import annotations

import os
import platform
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException

from . import __version__, updates

router = APIRouter()
ctx = SimpleNamespace(stop=None)


def _system() -> str:
    if platform.system() == "Darwin":
        return f"macOS {platform.mac_ver()[0]}".strip()
    if platform.system() == "Windows":
        return f"Windows {platform.release()}"
    return f"{platform.system()} {platform.release()}"


@router.get("/api/update")
def api_update(force: bool = False):
    out = updates.check(force=force)
    ok, why = updates.can_install()
    return {**out, "current": __version__, "system": _system(), "can_install": ok, "reason": why,
            "installing": updates.in_progress(), "result": updates.last_result()}


@router.post("/api/update/ack")
def api_update_ack(body: dict):
    updates.acknowledge()
    return {"ok": True}


@router.post("/api/update/install")
def api_update_install(body: dict):
    """A JSON body is required, so (like /api/quit) only this app's own pages can ask."""
    if updates.in_progress():
        raise HTTPException(409, "An update is already installing.")
    ok, why = updates.can_install()
    if not ok:
        raise HTTPException(409, why)
    info = updates.fetch()
    if not info or not updates.parse(str(info.get("version", ""))):
        raise HTTPException(502, "Couldn't reach the update. Check the internet connection and try again.")
    if not updates.newer(info["version"]):
        raise HTTPException(409, f"You're up to date ({__version__}).")
    pids = [os.getpid()]
    if os.environ.get("RB_RESTART_FILE"):  # run by `research-brain serve`, which must stop too
        pids.append(os.getppid())
    try:
        local = updates.prepare(info)
        state = updates.start_install(local, port=int(os.environ.get("RB_PORT", "8765") or 8765), pids=pids)
    except updates.UpdateError as e:
        raise HTTPException(502, str(e)) from e
    if ctx.stop:
        ctx.stop()
    return {"ok": True, "from": state["from"], "to": state["to"]}


def install(app, *, stop) -> None:
    ctx.stop = stop
    app.include_router(router)

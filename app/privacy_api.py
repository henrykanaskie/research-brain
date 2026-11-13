"""HTTP endpoints for privacy (app/privacy.py). Installed by app/server.py.

    GET  /api/privacy                  {settings, kinds, counts: {private, total}}
    PUT  /api/privacy/settings         {private_by_default?: [types], private_ok_for_local_ai?: bool} -> settings
    GET  /api/privacy/log?limit=200    {entries: [...newest first], path}: what was sent to AI (ids, never content)
    POST /api/notes/{id}/privacy       {private: true|false|null} -> the note (null = back to its type's default)
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import privacy

router = APIRouter()
ctx = SimpleNamespace(store=None, with_extras=None, publish=None)


class PrivacySettingsIn(BaseModel):
    private_by_default: list[str] | None = None
    private_ok_for_local_ai: bool | None = None
    web_search: bool | None = None  # let the agent search the web in General chats (never with library content)


class NotePrivacyIn(BaseModel):
    private: bool | None = None


def install(app, *, store, with_extras, publish=None) -> None:
    ctx.store, ctx.with_extras, ctx.publish = store, with_extras, publish
    app.include_router(router)

    @app.on_event("startup")
    def _warm_privacy():  # build the private-items index in the background, so the first AI call doesn't wait
        import threading
        threading.Thread(target=privacy.private_ids, daemon=True, name="rb-privacy").start()


def _summary() -> dict:
    priv = privacy.private_ids()
    try:
        total = len(ctx.store.all())
    except Exception:
        total = 0
    return {"settings": privacy.settings(), "kinds": privacy.kinds(),
            "counts": {"private": len(priv), "total": total}}


@router.get("/api/privacy")
def api_privacy():
    return _summary()


@router.put("/api/privacy/settings")
def api_privacy_settings(body: PrivacySettingsIn):
    try:
        privacy.save_settings(body.private_by_default, body.private_ok_for_local_ai, body.web_search)
    except ValueError as e:
        raise HTTPException(400, str(e))
    privacy.refresh()
    if ctx.publish:
        ctx.publish({"type": "notes-changed", "ids": [], "reason": "privacy"})
    return _summary()


@router.get("/api/privacy/log")
def api_privacy_log(limit: int = 200):
    entries = privacy.read_log(max(1, min(limit, 5000)))
    titles: dict[str, str] = {}
    try:
        by_id = {n["id"]: n for n in ctx.store.all()}
    except Exception:
        by_id = {}
    for e in entries:
        for i in e.get("items") or []:
            if i in by_id:
                titles[i] = by_id[i].get("title") or i
    return {"entries": entries, "titles": titles, "path": "state/ai_log.jsonl"}


@router.post("/api/notes/{nid}/privacy")
def api_note_privacy(nid: str, body: NotePrivacyIn):
    try:
        ctx.store.get(nid)
    except KeyError:
        raise HTTPException(404, f"No note with id '{nid}'")
    note = ctx.store.update(nid, {"private": body.private})
    privacy.refresh()
    if ctx.publish:
        ctx.publish({"type": "notes-changed", "ids": [nid], "reason": "privacy"})
    return ctx.with_extras(note)

"""HTTP endpoints for Find papers and On deck (logic in app/finder.py). server.py calls install(app, store=...) once.

    (a finished search is announced on GET /api/events as "finder-done")
    GET    /api/finder                    {searches, items, deck (papers waiting for review), running}
    POST   /api/finder/search             {topic, detail?, since?: any|10y|5y|3y|1y, question?: auto|""|id}: start a
                                          search (background), tied to the open question it's about
    PUT    /api/finder/items/{id}         {status: deck|dismissed}
    DELETE /api/finder/searches/{id}      forget a search and the papers still on deck from it
    POST   /api/finder/items/{id}/added   {note_id}: you added it; its free copy's note goes into the paper's
                                          body and that copy is downloaded as its PDF (background)

Adding a paper to the library uses POST /api/import/lookup (the paper's DOI, arXiv id or title).
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import finder, metadata

router = APIRouter()
ctx = SimpleNamespace(store=None, publish=None, match_question=None)


def install(app, *, store, publish=None, match_question=None):
    """publish(event): the live-update bus (GET /api/events); a finished search is announced on it as
    {"type": "finder-done", "id", "topic", "status", "added", "error", "deck"} so the page can notify you."""
    ctx.store, ctx.publish, ctx.match_question = store, publish, match_question
    app.include_router(router)


@router.get("/api/finder")
def api_finder():
    return finder.view(ctx.store)


class SearchIn(BaseModel):
    topic: str
    detail: str = ""
    since: str = "any"
    question: str = "auto"   # "auto": the open question the topic matches, if any; "": none; or a question id


@router.post("/api/finder/search", status_code=202)
def api_finder_search(body: SearchIn):
    if not metadata.enabled():
        raise HTTPException(503, "Online lookups are turned off (RB_METADATA=0).")
    q = None
    if body.question == "auto" and ctx.match_question is not None:
        q = ctx.match_question(f"{body.topic}\n{body.detail}")
    elif body.question and body.question != "auto":
        try:
            n = ctx.store.get(body.question)
        except KeyError:
            raise HTTPException(404, "No such question")
        if n.get("type") != "question":
            raise HTTPException(400, "That isn't a question")
        q = {"id": n["id"], "title": n["title"], "score": None}
    try:
        row = finder.start(ctx.store, body.topic, body.detail, body.since, on_done=_announce, question=q)
    except finder.FinderError as e:
        raise HTTPException(400, str(e))
    return {"search": {**row, "running": True}}


def _announce(row: dict) -> None:
    if ctx.publish is None:
        return
    ctx.publish({"type": "finder-done", "id": row.get("id"), "topic": row.get("topic") or "",
                 "status": row.get("status") or "done", "added": row.get("added") or 0, "error": row.get("error") or "",
                 "deck": finder.view(ctx.store)["deck"]})


class StatusIn(BaseModel):
    status: str


@router.put("/api/finder/items/{iid}")
def api_finder_item(iid: str, body: StatusIn):
    try:
        item = finder.set_status(ctx.store, iid, body.status)
    except finder.FinderError as e:
        raise HTTPException(400, str(e))
    except KeyError:
        raise HTTPException(404, "No such paper on deck")
    return {"item": item, "deck": finder.view(ctx.store)["deck"]}


class AddedIn(BaseModel):
    note_id: str


@router.post("/api/finder/items/{iid}/added", status_code=202)
def api_finder_added(iid: str, body: AddedIn):
    item = next((i for i in finder.load(ctx.store)["items"] if i["id"] == iid), None)
    if item is None:
        raise HTTPException(404, "No such paper on deck")
    try:
        ctx.store.get(body.note_id)
    except KeyError:
        raise HTTPException(404, "No such note")
    c = item.get("free_copy") or {}
    if not c.get("url"):
        return {"free_copy": False}
    finder.use_free_copy(ctx.store, body.note_id, c, publish=ctx.publish)
    return {"free_copy": True, "note": c.get("note") or ""}


@router.delete("/api/finder/searches/{sid}")
def api_finder_delete(sid: str):
    try:
        finder.delete_search(ctx.store, sid)
    except KeyError:
        raise HTTPException(404, "No such search")
    return {"ok": True}

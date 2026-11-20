"""HTTP endpoints for the novelty check (logic in app/novelty.py). server.py calls install(app, ...) once.

    POST   /api/novelty/plan        {note_id?, text?, use_ai?: true} (no note_id: the idea is the text typed in Ask): the preview. {title, idea, parts, queries,
                                    online, online_why, ai, ai_why, planned_by_ai}. Splitting the idea into parts
                                    asks your AI agent (only when it may read the note); nothing goes to a
                                    paper index yet.
    POST   /api/novelty             {note_id?, text?, parts, queries, use_ai?: true}: start a check (background)
    GET    /api/novelty?note_id=    {runs: [...newest first], running}
    GET    /api/novelty/runs/{id}   one run (poll it while it runs)
    DELETE /api/novelty/runs/{id}
    (a finished check is announced on GET /api/events as "novelty-done" {id, note_id, title, status, error, counts})
"""
from __future__ import annotations

from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import metadata, novelty

router = APIRouter()
ctx = SimpleNamespace(store=None, publish=None, ask=None, agent=None, embedder=None, chunk_index=None,
                      sources=None, cites=None)  # sources / cites: test doubles for the paper indexes


def install(app, *, store, publish=None, ask=None, agent=None, embedder=None, chunk_index=None):
    """ask(instruction, context, purpose, sources) -> text from the AI agent; agent() -> the agent it goes to
    (for privacy.allow_ai); embedder / chunk_index: the library's embeddings, for local similarity."""
    ctx.store, ctx.publish, ctx.ask, ctx.agent = store, publish, ask, agent
    ctx.embedder, ctx.chunk_index = embedder, chunk_index
    app.include_router(router)


def _note(nid: str) -> dict:
    try:
        return ctx.store.get(nid)
    except KeyError:
        raise HTTPException(404, "No such note")


def _access(n: dict | None) -> dict:
    """What this note's check (n None: text typed in Ask) may do: search the paper indexes, let the AI read it."""
    from . import privacy
    try:
        private = privacy.is_private(n) if n is not None else False
    except Exception:
        private = True
    online, online_why = True, ""
    if private:
        online, online_why = False, ("This note is private, so only your library is checked and nothing leaves "
                                     "this computer. Mark it shareable to search published papers too.")
    elif not metadata.enabled():
        online, online_why = False, "Online lookups are turned off, so only your library is checked."
    ai, ai_why = False, ""
    if ctx.ask is None:
        ai_why = "No AI agent is set up."
    else:
        try:
            agent = ctx.agent() if ctx.agent else None
            ai = agent is not None and (n is None or bool(privacy.allow_ai(n, agent)))
        except Exception:
            ai = False
        if not ai:
            ai_why = "No AI agent is set up." if n is None else "This note is private, so no AI agent reads it."
    return {"online": online, "online_why": online_why, "ai": ai, "ai_why": ai_why}


def _asker(nid: str, purpose: str):
    return lambda instruction, context: ctx.ask(instruction, context, purpose, [nid] if nid else None)


def _subject(note_id: str, text: str) -> tuple[dict | None, str, str]:
    """(the note or None, title, idea) for a check of a note, the text selected in it, or text typed in Ask."""
    if note_id:
        n = _note(note_id)
        title, idea = novelty.idea_of(n, text)
        return n, title, idea
    return None, "", novelty.typed_idea(text)


def _family() -> str:
    from . import questions
    try:
        return questions.embedder_family(ctx.embedder.kind) if ctx.embedder is not None else "tfidf"
    except Exception:
        return "tfidf"


def _sim():
    from . import scout
    dense = ctx.chunk_index if _family() != "tfidf" else None
    return lambda text, texts: scout.similarity(text, texts, dense)


class PlanIn(BaseModel):
    note_id: str = ""
    text: str = ""
    use_ai: bool = True


@router.post("/api/novelty/plan")
def api_novelty_plan(body: PlanIn):
    n, title, idea = _subject(body.note_id, body.text)
    if len(f"{title} {idea}".strip()) < 8:
        raise HTTPException(400, "Describe the idea first: a sentence or two on the problem and how you'd go at it.")
    acc = _access(n)
    p, why = None, ""
    if acc["ai"] and body.use_ai:
        try:
            p = novelty.plan(title, idea, _asker(n["id"] if n else "", "novelty check"))
        except HTTPException as e:
            why = str(e.detail)
        except Exception as e:
            why = str(e)[:200]
    if p is None:
        p = novelty.plan(title, idea)
    return {"title": title, "idea": idea, "parts": p["parts"], "queries": p["queries"], "planned_by_ai": p["ai"],
            "plan_error": why, "from_selection": bool(n and body.text.strip()), "typed": n is None, **acc}


class StartIn(BaseModel):
    note_id: str = ""
    text: str = ""
    parts: list = []
    queries: list[str] = []
    use_ai: bool = True


@router.post("/api/novelty", status_code=202)
def api_novelty_start(body: StartIn):
    n, title, idea = _subject(body.note_id, body.text)
    if len(f"{title} {idea}".strip()) < 8:
        raise HTTPException(400, "Describe the idea first: a sentence or two on the problem and how you'd go at it.")
    acc = _access(n)
    parts = body.parts or novelty.plan(title, idea)["parts"]
    nid = n["id"] if n else ""
    try:
        row = novelty.start(ctx.store, note_id=nid, title=title, idea=idea, parts=parts, queries=body.queries,
                            online=acc["online"], ask=_asker(nid, "novelty check") if acc["ai"] and body.use_ai else None,
                            sim=_sim(), family=_family(), on_done=_announce,
                            sources=ctx.sources, cites=ctx.cites)
    except novelty.NoveltyError as e:
        raise HTTPException(400, str(e))
    return {"run": {**row, "running": True}, **acc}


def _announce(run: dict) -> None:
    if ctx.publish is None:
        return
    ctx.publish({"type": "novelty-done", "id": run.get("id"), "note_id": run.get("note_id") or "",
                 "title": run.get("title") or " ".join((run.get("idea") or "").split())[:100], "status": run.get("status") or "done",
                 "error": run.get("error") or "", "counts": run.get("counts") or {}})


@router.get("/api/novelty")
def api_novelty_list(note_id: str = ""):
    runs = novelty.runs_for(ctx.store, note_id) if note_id else [novelty._out(r) for r in novelty.load(ctx.store)["runs"]]
    return {"runs": runs, "running": len(novelty.RUNNING)}


@router.get("/api/novelty/runs/{rid}")
def api_novelty_run(rid: str):
    r = novelty.get(ctx.store, rid)
    if r is None:
        raise HTTPException(404, "No such novelty check")
    return r


@router.delete("/api/novelty/runs/{rid}")
def api_novelty_delete(rid: str):
    try:
        novelty.delete(ctx.store, rid)
    except KeyError:
        raise HTTPException(404, "No such novelty check")
    return {"ok": True}

"""HTTP endpoints for open questions (logic in app/questions.py). server.py calls install(app, ...) once.

The question itself is a note: create, edit and delete it with the notes API (POST /api/notes {type: "question"},
PUT /api/notes/{id} {title, body, answer, question_status}, DELETE /api/notes/{id}).

    GET  /api/questions                          [{id, title, question_status, counts, new_evidence, ...}] + total_new
    GET  /api/questions/settings                 {threshold_tfidf, threshold_dense, daily_cap, embedder, threshold}
    PUT  /api/questions/settings                 {threshold_tfidf?, threshold_dense?, daily_cap?}
    GET  /api/questions/{id}/evidence            the question, its evidence (with sources), counts, ai availability
    PUT  /api/questions/{id}/evidence/{eid}      {verdict?, user_note?}
    POST /api/questions/{id}/seen                last_seen = now ("Mark all seen")
    POST /api/questions/{id}/rescan              look for evidence now (local), returns what was added
    POST /api/questions/{id}/summarize           optional AI summary of the kept evidence; 403 while the
                                                 question is private (the default)
"""
from __future__ import annotations

import os
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel

from . import budget, metadata, scout
from . import questions as Q

router = APIRouter()
ctx = SimpleNamespace(store=None, matcher=None, index=None, passage_links=None, ask=None)


def install(app, *, store, chunk_index, embedder, index_fn, event_bus=None, passage_links=None, ask=None):
    """index_fn() -> the server's note index ({ids, m, by_id}); passage_links(row, by_id) adds article links;
    ask(instruction, context) -> text from the active AI agent (used only when the privacy check allows)."""
    ctx.store, ctx.index, ctx.passage_links, ctx.ask = store, index_fn, passage_links, ask
    ctx.matcher = Q.Matcher(Q.Context(store, chunk_index, embedder, index_fn))
    store.listen(ctx.matcher.on_store)
    if event_bus is not None and hasattr(event_bus, "listen"):
        event_bus.listen(ctx.matcher.on_event)
    app.include_router(router)

    @app.on_event("startup")
    def _start_questions():
        if os.environ.get("RB_QUESTIONS_MATCH", "1") != "0":
            ctx.matcher.start()

    @app.on_event("shutdown")
    def _stop_questions():
        ctx.matcher.stop()
    return ctx.matcher


def _question(qid: str) -> dict:
    try:
        n = ctx.store.get(qid)
    except KeyError:
        raise HTTPException(404, f"No question with id '{qid}'")
    if not Q.is_question(n):
        raise HTTPException(404, f"'{qid}' is not a question")
    return n


def _where(ref: dict) -> str:
    if ref.get("page"):
        return f"p. {ref['page']}"
    if ref.get("section"):
        return f"§{ref['section']}"
    return ""


def _enrich(q: dict, item: dict, by_id: dict) -> dict:
    """An evidence item for the page: the source's title and type, a link to the passage, and whether it is new."""
    ref = item.get("ref") or {}
    out = dict(item)
    out["new"] = Q.is_new(q, item)
    if item.get("kind") == "external":  # a published paper the scout found; not in the library (yet)
        p = item.get("paper") or {}
        lib = _in_library(ref, by_id)
        out["where"] = ""
        out["href"] = ref.get("url") or ""
        out["source"] = {"id": lib or "", "title": item.get("title") or "", "type": "paper", "year": p.get("year"),
                         "authors": (p.get("authors") or [])[:3], "exists": bool(lib), "external": True,
                         "venue": p.get("venue") or "", "access": p.get("access") or "", "found_via": p.get("found_via") or "",
                         "doi": ref.get("doi") or "", "arxiv": ref.get("arxiv") or ""}
        return out
    nid = ref.get("note") or ""
    n = by_id.get(nid)
    owner = by_id.get(n.get("parent")) if n is not None and n.get("type") == "fragment" else n
    out["where"] = _where(ref)
    if n is None:
        out["source"] = {"id": nid, "title": item.get("title") or nid, "type": "", "exists": False}
        return out
    out["source"] = {"id": n["id"], "title": n["title"], "type": n["type"], "year": n.get("year"),
                     "authors": (n.get("authors") or [])[:3], "exists": True,
                     "paper": {"id": owner["id"], "title": owner["title"]} if owner is not None and owner is not n else None}
    href = ""
    if owner is not None and owner.get("has_pdf"):
        href = f"/viewer/{owner['id']}" + (f"#page={ref['page']}" if ref.get("page") else "")
    elif ctx.passage_links is not None and item.get("kind") == "passage":
        try:
            row = ctx.passage_links({"id": n["id"], "page": ref.get("page"), "src": "text", "text": item.get("snippet") or "",
                                     **({"section": ref["section"], "section_title": ref["section"]} if ref.get("section") else {})},
                                    by_id)
            href = row.get("url") or ""
        except Exception:
            href = ""
    out["href"] = href
    return out


def _in_library(ref: dict, by_id: dict) -> str:
    """The library paper a scouted paper became (after "Add to library"), if any."""
    doi, arxiv = (ref.get("doi") or "").lower(), (ref.get("arxiv") or "").lower()
    for n in by_id.values():
        if n.get("type") == "paper" and ((doi and (n.get("doi") or "").lower() == doi) or (arxiv and (n.get("arxiv") or "").lower() == arxiv)):
            return n["id"]
    return ""


ORDER = ("new", "supports", "against", "related", "dismissed")
LABELS = {"new": "To triage", "supports": "Supports", "against": "Against", "related": "Related", "dismissed": "Dismissed"}


@router.get("/api/questions")
def api_questions():
    rows = Q.list_questions(ctx.store)
    return {"questions": rows, "total_new": sum(r["new_evidence"] for r in rows if r["question_status"] == "open"),
            "matching": {"runs": ctx.matcher.runs if ctx.matcher else 0, "enabled": bool(ctx.matcher and ctx.matcher.enabled)}}


class SettingsIn(BaseModel):
    threshold_tfidf: float | None = None
    threshold_dense: float | None = None
    daily_cap: int | None = None


def _settings_out() -> dict:
    s = Q.get_settings()
    kind = ctx.matcher.ctx.embedder.kind if ctx.matcher else "tfidf"
    return {**s, "embedder": kind, "family": Q.embedder_family(kind), "threshold": Q.threshold_for(kind, s),
            "defaults": {"threshold_tfidf": Q.THRESHOLDS["tfidf"], "threshold_dense": Q.THRESHOLDS["dense"],
                         "daily_cap": Q.DAILY_CAP}}


@router.get("/api/questions/settings")
def api_questions_settings():
    return _settings_out()


@router.put("/api/questions/settings")
def api_questions_settings_put(body: SettingsIn):
    try:
        Q.save_settings(body.model_dump())
    except Q.QuestionError as e:
        raise HTTPException(400, str(e))
    if ctx.matcher:
        ctx.matcher.poke()
    return _settings_out()


@router.get("/api/questions/{qid}/evidence")
def api_question_evidence(qid: str):
    q = _question(qid)
    data = Q.load(ctx.store, qid)
    by_id = {n["id"]: n for n in ctx.store.all()}
    items = [_enrich(q, i, by_id) for i in data["items"]]
    items.sort(key=lambda i: (ORDER.index(i["verdict"]), not i["new"], -(i.get("score") or 0)))
    groups = [{"verdict": v, "label": LABELS[v], "count": sum(1 for i in items if i["verdict"] == v)} for v in ORDER]
    ok, why = Q.ai_allowed(q)
    sc = data.get("scout") or {}
    return {"question": {"id": q["id"], "title": q["title"], "question_status": q.get("question_status") or "open",
                         "last_seen": q.get("last_seen") or "", "reach": scout.reach(q)},
            "scout": {"query": scout.query_for(q), "last": sc.get("last") or "", "error": sc.get("error") or "",
                      "looked_at": sc.get("looked_at") or 0, "online": metadata.enabled()},
            "agent": {**{k: (data.get("agent") or {}).get(k) for k in ("last", "turns", "lookups", "proposed", "added", "note", "error")},
                      "tokens": budget.tokens((data.get("agent") or {}).get("chars_sent") or 0),
                      "running": qid in Q.AGENT_RUNNING},
            "items": items, "groups": groups, "counts": Q.counts(q, data),
            "ai": {"available": ok, "reason": why}}


class VerdictIn(BaseModel):
    verdict: str | None = None
    user_note: str | None = None


@router.put("/api/questions/{qid}/evidence/{eid}")
def api_question_verdict(qid: str, eid: str, body: VerdictIn):
    q = _question(qid)
    try:
        item = Q.set_verdict(ctx.store, qid, eid, body.verdict, body.user_note)
    except KeyError:
        raise HTTPException(404, "No such evidence for this question")
    except Q.QuestionError as e:
        raise HTTPException(400, str(e))
    by_id = {n["id"]: n for n in ctx.store.all()}
    return {"item": _enrich(q, item, by_id), "counts": Q.counts(q, Q.load(ctx.store, qid))}


@router.post("/api/questions/{qid}/seen")
def api_question_seen(qid: str):
    _question(qid)
    n = Q.mark_seen(ctx.store, qid)
    return {"last_seen": n["last_seen"], "counts": Q.counts(n, Q.load(ctx.store, qid))}


@router.post("/api/questions/{qid}/rescan")
def api_question_rescan(qid: str):
    q = _question(qid)
    try:
        added = ctx.matcher.rescan(qid)
    except KeyError:
        raise HTTPException(404, f"No question with id '{qid}'")
    return {"added": len(added), "counts": Q.counts(q, Q.load(ctx.store, qid))}


class ReachIn(BaseModel):
    reach: str


@router.put("/api/questions/{qid}/reach")
def api_question_reach(qid: str, body: ReachIn):
    """private | library | scout (app/scout.py). Private also marks the note private for every AI feature."""
    if body.reach not in scout.REACHES:
        raise HTTPException(400, f"reach must be one of {', '.join(scout.REACHES)}")
    with ctx.store.lock:
        _question(qid)
        ctx.store.update(qid, {"private": body.reach == "private"})
        n = ctx.store.get(qid)
        n["_extra"] = {**(n.get("_extra") or {}), "reach": body.reach}
        n = ctx.store.save(n, touch=False)
    if body.reach in scout.SCOUTING:
        ctx.matcher.poke(qid, force=True)  # the first search happens shortly, in the background
        # (and, for agent, the first agent search: Matcher.scout_due starts it when it's due)
    ok, why = Q.ai_allowed(n)
    return {"reach": body.reach, "ai": {"available": ok, "reason": why}, "query": scout.query_for(n)}


@router.post("/api/questions/{qid}/scout")
def api_question_scout(qid: str):
    """Search published papers for this question now (only when its reach is scout)."""
    q = _question(qid)
    if scout.reach(q) not in scout.SCOUTING:
        raise HTTPException(400, "Set this question's reach to Scout first: its key words would be sent to OpenAlex and arXiv.")
    if not metadata.enabled():
        raise HTTPException(503, "Online lookups are turned off (RB_METADATA=0).")
    try:
        r = Q.scout_scan(ctx.matcher.ctx, qid)
    except KeyError:
        raise HTTPException(404, f"No question with id '{qid}'")
    if r["error"] and not r["looked_at"]:
        raise HTTPException(503, f"Couldn't search published papers ({r['error']}). Check your connection and try again.")
    return {"added": len(r["added"]), "query": r["query"], "looked_at": r["looked_at"], "error": r["error"],
            "counts": Q.counts(q, Q.load(ctx.store, qid))}


@router.post("/api/questions/{qid}/agent-search")
def api_question_agent_search(qid: str):
    """Start an AI agent search for published papers now (reach must be agent); poll the evidence for results."""
    q = _question(qid)
    if scout.reach(q) != "agent":
        raise HTTPException(400, "Set this question's reach to Agent first.")
    ok, why = Q.ai_allowed(q)
    if not ok:
        raise HTTPException(403, why)
    if not metadata.enabled():
        raise HTTPException(503, "Online lookups are turned off (RB_METADATA=0).")
    started = Q.start_agent_search(ctx.matcher.ctx, qid)
    return {"started": started, "running": True}


SUMMARY_INSTRUCTION = (
    "The user's open research QUESTION and the evidence they kept for it are on stdin, each piece marked "
    "supports, against or related, with its source. Summarise where the evidence stands in at most 6 short "
    "bullets: what supports it, what argues against it, and what is still missing. Cite each piece as its "
    "heading shows. Do not invent sources. Typeset math with $...$.")


@router.post("/api/questions/{qid}/summarize")
def api_question_summarize(qid: str):
    q = _question(qid)
    ok, why = Q.ai_allowed(q)
    if not ok:
        raise HTTPException(403, why)
    if ctx.ask is None:
        raise HTTPException(503, "No AI agent is set up")
    data = Q.load(ctx.store, qid)
    kept = [i for i in data["items"] if i["verdict"] in Q.KEPT]
    if not kept:
        raise HTTPException(400, "Keep some evidence first (supports, against or related), then summarise it")
    # evidence from private notes (your own notes are private by default) stays out of the prompt
    by_id = {n["id"]: n for n in ctx.store.all()}
    # published papers the scout found are public: they may always go
    shareable = [i for i in kept if i.get("kind") == "external" or Q.source_allowed(by_id.get((i.get("ref") or {}).get("note") or ""))]
    left_out = len(kept) - len(shareable)
    if not shareable:
        raise HTTPException(403, "All the evidence you kept comes from private notes, so there is nothing an AI agent may read.")
    kept = shareable
    blocks = []
    for i in kept[:30]:
        ref = i.get("ref") or {}
        where = (" " + _where(ref)) if _where(ref) else ""
        note = f"\nUser's note: {i['user_note']}" if i.get("user_note") else ""
        if i.get("kind") == "external":
            p = i.get("paper") or {}
            cite = f"{i.get('title', '')} ({', '.join((p.get('authors') or [])[:2])} {p.get('year') or ''}; {ref.get('doi') or ref.get('url') or ''}, not in the library)"
            blocks.append(f"### {cite} ({i['verdict']})\nAbstract: {i.get('snippet', '')}{note}")
            continue
        blocks.append(f"### [[{ref.get('note', '')}]]{where} ({i['verdict']})\n{i.get('snippet', '')}{note}")
    context = (f"QUESTION: {q['title']}\n{q.get('body') or ''}\n\nUSER'S CURRENT ANSWER:\n{q.get('answer') or '(none yet)'}"
               f"\n\n---\nEVIDENCE:\n\n" + "\n\n".join(blocks))
    return {"text": ctx.ask(SUMMARY_INSTRUCTION, context), "used": len(kept), "left_out": left_out}

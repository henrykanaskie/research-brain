"""HTTP endpoints for idea cards (app/cards.py). server.py calls install(app, ...) once. See API.md "Idea cards".

    GET    /api/papers/{id}/cards                  a paper's cards (+ links to other papers, conflicts, job)
    POST   /api/papers/{id}/cards                  add a card by hand (yours: confirmed, private per setting)
    POST   /api/papers/{id}/cards/extract          suggest cards with the active AI agent (background) -> job
    POST   /api/papers/{id}/cards/confirm-all      {ids?} confirm suggested cards
    GET    /api/cards/jobs/{job_id}                extraction progress
    PATCH  /api/cards/{card_id}                    edit / confirm / reject (status) / private
    DELETE /api/cards/{card_id}
    GET    /api/cards?kind=&q=&quantity=&status=&paper=&limit=     search (local; no AI)
    GET    /api/cards/export.csv?...               the same rows as CSV
    GET    /api/cards/links                        same-idea links between papers' cards, with conflicts
"""
from __future__ import annotations

import csv
import io
import re
import threading
import time
from types import SimpleNamespace

from fastapi import APIRouter, HTTPException
from fastapi.responses import Response
from pydantic import BaseModel

from . import cards, units

router = APIRouter()
ctx = SimpleNamespace(store=None, publish=None, article_url=None, keys=(None, {}))
MAX_LIMIT = 2000


def install(app, *, store, embedder=None, publish=None, article_url=None) -> None:
    cards.configure(store, embedder)
    ctx.store, ctx.publish, ctx.article_url = store, publish, article_url
    app.include_router(router)


# ---------------------------------------------------------------- helpers
def _privacy():
    try:
        from . import privacy
        return privacy
    except ImportError:  # pragma: no cover
        return None


def _paper(pid: str) -> dict:
    try:
        n = ctx.store.get(pid)
    except KeyError:
        raise HTTPException(404, f"No note with id '{pid}'")
    if n["type"] != "paper":
        raise HTTPException(400, "Idea cards belong to papers")
    return n


def _fulltext(pid: str) -> str:
    try:
        p = ctx.store.text_path(pid)
    except KeyError:
        return ""
    return p.read_text(encoding="utf-8", errors="ignore") if p.exists() else ""


def default_private() -> bool:
    """Cards you write are private when the library keeps standalone notes (or cards) private."""
    pv = _privacy()
    if pv is None:
        return False
    try:
        return bool(pv.is_private({"type": "note"}) or pv.is_private({"type": "card"}))
    except Exception:
        return False


def paper_private(n: dict) -> bool:
    pv = _privacy()
    try:
        return bool(pv and pv.is_private(n))
    except Exception:
        return False


def _keys() -> dict:
    """Citation keys for display (computed, never written; app/cite.py)."""
    sig = ctx.store.signature()
    if ctx.keys[0] != sig:
        try:
            from . import cite
            ctx.keys = (sig, {i: k for i, (k, _) in cite.assign_keys(ctx.store.all()).items()})
        except Exception as e:
            print(f"[research_brain] citation keys for cards failed: {e!r}")
            ctx.keys = (sig, {})
    return ctx.keys[1]


def _href(n: dict | None, c: dict) -> str:
    if not n:
        return ""
    loc = c.get("locator") or {}
    page = loc.get("page")
    if n.get("has_pdf"):
        return f"/viewer/{n['id']}" + (f"#page={int(page)}" if page else "")
    url = n.get("url") or ""
    if re.match(r"^https?://", url):
        if n.get("source") == "linked" and ctx.article_url:
            return ctx.article_url(url, page, loc.get("quote") or "", loc.get("section") or "")
        return url.split("#", 1)[0]
    return ""


def _notes() -> dict:
    return {n["id"]: n for n in ctx.store.all()}


def card_out(c: dict, by_id: dict, links_by: dict | None = None, by_card: dict | None = None) -> dict:
    n = by_id.get(c["paper"])
    out = dict(c)
    out["paper_title"] = n["title"] if n else c["paper"]
    out["year"] = n.get("year") if n else None
    out["citekey"] = _keys().get(c["paper"], "")
    out["where"] = cards.where(c)
    out["href"] = _href(n, c)
    if c["kind"] == "quantity" and c.get("quantity"):
        q = c["quantity"]
        out["custom_text"] = bool(c.get("text")) and c.get("text") != cards.quantity_text(q)
        out["value_text"] = units.fmt(q.get("value")) + (f" ± {units.fmt(q['uncertainty'])}" if q.get("uncertainty") else "")
        if q.get("canonical_unit") is not None and q.get("canonical_unit_value") is not None:
            out["canonical_text"] = units.fmt(q["canonical_unit_value"]) + (
                f" ± {units.fmt(q['canonical_uncertainty'])}" if q.get("canonical_uncertainty") else "") + \
                (f" {q['canonical_unit']}" if q["canonical_unit"] not in ("", "1") else "")
    if links_by is not None:
        rows = []
        for ln in links_by.get(c["id"], []):
            other = ln["b"] if ln["a"] == c["id"] else ln["a"]
            oc = (by_card or {}).get(other)
            if not oc:
                continue
            on = by_id.get(oc["paper"])
            rows.append({"card": other, "paper": oc["paper"], "paper_title": on["title"] if on else oc["paper"],
                         "citekey": _keys().get(oc["paper"], ""), "where": cards.where(oc), "kind": ln["kind"],
                         "conflict": ln["conflict"], "score": ln["score"], "text": oc.get("text") or "",
                         "status": oc["status"], "href": _href(on, oc),
                         "value_text": (units.fmt(oc["quantity"].get("value")) + (f" ± {units.fmt(oc['quantity']['uncertainty'])}" if oc["quantity"].get("uncertainty") else "")
                                        + (f" {oc['quantity'].get('unit')}" if oc["quantity"].get("unit") else "")) if oc.get("quantity") else ""})
        rows.sort(key=lambda r: (not r["conflict"], -r["score"]))
        out["links"] = rows
        out["conflicts"] = sum(1 for r in rows if r["conflict"])
    return out


def _link_maps():
    try:
        lks = cards.links()
    except Exception as e:  # linking must never break listing
        print(f"[research_brain] card links failed: {e!r}")
        lks = []
    by: dict[str, list] = {}
    for ln in lks:
        by.setdefault(ln["a"], []).append(ln)
        by.setdefault(ln["b"], []).append(ln)
    by_card = {c["id"]: c for c in cards.card_store().all_cards()}
    return by, by_card


def _agent_info() -> dict:
    try:
        from . import agents
        b = agents.active()
        det = agents.detection(b)
        why = "" if det.available else (det.note or f"{b.name} isn't installed or signed in.")
        return {"available": bool(det.available), "name": b.name, "id": b.id, "why": why}
    except Exception as e:
        return {"available": False, "name": "AI agent", "id": "", "why": str(e)}


def _changed(pid: str) -> None:
    if ctx.publish:
        try:
            ctx.publish({"type": "cards-changed", "ids": [pid], "at": time.time()})
        except Exception:
            pass


def _verify(pid: str, quote: str):
    n = ctx.store.get(pid)
    return cards.PaperText(cards.cut_references(_fulltext(pid)), n.get("abstract") or "").locate(quote)


# ---------------------------------------------------------------- a paper's cards
@router.get("/api/papers/{pid}/cards")
def api_paper_cards(pid: str):
    n = _paper(pid)
    by_id = _notes()
    links_by, by_card = _link_maps()
    data = cards.card_store().load(pid)
    rows = [card_out(c, by_id, links_by, by_card) for c in data["cards"]]
    counts = {s: sum(1 for c in data["cards"] if c["status"] == s) for s in cards.STATUSES}
    run = cards.jobs.running(pid)
    return {"paper": {"id": n["id"], "title": n["title"], "has_pdf": n["has_pdf"], "has_text": n["has_text"],
                      "source": n.get("source") or "", "private": paper_private(n)},
            "cards": rows, "counts": counts, "agent": _agent_info(), "job": run[0] if run else None,
            "extractions": data.get("extractions") or [], "default_private": default_private(),
            "max_ai": cards.MAX_AI_CARDS}


class CardIn(BaseModel):
    kind: str
    text: str = ""
    latex: str = ""
    quantity: dict | None = None
    locator: dict | None = None
    private: bool | None = None


@router.post("/api/papers/{pid}/cards", status_code=201)
def api_add_card(pid: str, body: CardIn):
    _paper(pid)
    fields = body.model_dump(exclude_none=True)
    try:
        c = cards.card_store().add(pid, fields, origin="user",
                                   private=body.private if body.private is not None else default_private())
    except ValueError as e:
        raise HTTPException(400, str(e))
    if c["locator"].get("quote"):
        hit = _verify(pid, c["locator"]["quote"])
        if hit:  # your quote is in the paper: keep where it is
            loc = dict(c["locator"], verified=True)
            for k in ("page", "section"):
                if hit.get(k) and not loc.get(k):
                    loc[k] = hit[k]
            c = cards.card_store().update(c["id"], {"locator": loc, "status": c["status"]})
    _changed(pid)
    links_by, by_card = _link_maps()
    return card_out(c, _notes(), links_by, by_card)


class CardPatch(BaseModel):
    kind: str | None = None
    text: str | None = None
    latex: str | None = None
    quantity: dict | None = None
    locator: dict | None = None
    status: str | None = None
    private: bool | None = None


@router.patch("/api/cards/{cid}")
def api_patch_card(cid: str, body: CardPatch):
    fields = body.model_dump(exclude_unset=True)
    try:
        c = cards.card_store().update(cid, fields, verify=_verify)
    except KeyError:
        raise HTTPException(404, "No card with that id")
    except ValueError as e:
        raise HTTPException(400, str(e))
    _changed(c["paper"])
    links_by, by_card = _link_maps()
    return card_out(c, _notes(), links_by, by_card)


@router.delete("/api/cards/{cid}")
def api_delete_card(cid: str):
    try:
        pid = cards.card_store().delete(cid)
    except KeyError:
        raise HTTPException(404, "No card with that id")
    except ValueError as e:
        raise HTTPException(409, str(e))
    _changed(pid)
    return {"ok": True}


class ConfirmIn(BaseModel):
    ids: list[str] | None = None


@router.post("/api/papers/{pid}/cards/confirm-all")
def api_confirm_all(pid: str, body: ConfirmIn | None = None):
    _paper(pid)
    ids = (body.ids if body else None) or None
    if ids is not None and (len(ids) > 500 or not all(isinstance(i, str) and cards.ID_RE.fullmatch(i) for i in ids)):
        raise HTTPException(400, "ids must be card ids")
    n = cards.card_store().confirm_all(pid, ids)
    if n:
        _changed(pid)
    return {"confirmed": n}


# ---------------------------------------------------------------- extraction
@router.post("/api/papers/{pid}/cards/extract", status_code=202)
def api_extract(pid: str):
    n = _paper(pid)
    text = _fulltext(pid)
    if not text.strip() and not (n.get("abstract") or "").strip():
        raise HTTPException(400, "This paper has no text yet (attach the PDF or save the article), so there's nothing "
                                 "to suggest ideas from. You can still add cards by hand.")
    from . import agents
    try:
        backend = agents.active()
    except agents.AgentError as e:
        raise HTTPException(503, f"{e} You can still add cards by hand.")
    det = agents.detection(backend)
    if not det.available:
        raise HTTPException(503, f"{backend.name} isn't available, so ideas can't be suggested now. "
                                 f"{det.note or ''} You can still add cards by hand.".replace("  ", " "))
    pv = _privacy()
    if pv is not None and paper_private(n) and not pv.allow_ai(n, backend):
        raise HTTPException(403, f"This paper is marked private, so its text isn't sent to {backend.name}. "
                                 "Add cards by hand, or mark the paper shareable.")
    have = sum(1 for c in cards.card_store().cards(pid) if c.get("origin") == "ai")
    if have >= cards.MAX_AI_CARDS:
        raise HTTPException(409, f"This paper already has {have} suggested ideas (the most per paper). "
                                 "Review them, or add your own.")
    try:
        job = cards.jobs.new(pid, backend.id)
    except RuntimeError as e:
        if str(e) == "busy-paper":
            run = cards.jobs.running(pid)
            return {"job_id": run[0]["id"] if run else "", "state": "running", "already_running": True}
        raise HTTPException(429, "Ideas are already being suggested for other papers; try again in a minute.")

    def work():
        try:
            res = cards.extract(cards.card_store(), n, text, backend, job=job)
            added = len(res["added"])
            msg = (f"{added} idea{'s' if added != 1 else ''} suggested" if added else "No new ideas found") + \
                  (f"; {res['dropped']} without a matching quote in the paper {'was' if res['dropped'] == 1 else 'were'} left out"
                   if res["dropped"] else "")
            cards.jobs.update(job["id"], state="done", added=added, dropped=res["dropped"],
                              duplicates=res["duplicates"], errors=res["errors"][:5], message=msg,
                              finished=time.time(), cards=[c["id"] for c in res["added"]])
            if added:
                _changed(pid)
        except agents.AgentError as e:
            cards.jobs.update(job["id"], state="error", message=str(e), finished=time.time())
        except Exception as e:  # never leave a job running forever
            print(f"[research_brain] card extraction failed: {e!r}")
            cards.jobs.update(job["id"], state="error", message=f"Suggesting ideas failed: {e}", finished=time.time())

    threading.Thread(target=work, daemon=True, name=f"cards-{pid[:20]}").start()
    return {"job_id": job["id"], "state": "running"}


@router.get("/api/cards/jobs/{jid}")
def api_job(jid: str):
    j = cards.jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


# ---------------------------------------------------------------- search, table, CSV, links
def _query(kind: str, q: str, quantity: str, status: str, paper: str, limit: int) -> list[dict]:
    if kind and kind not in cards.KINDS:
        raise HTTPException(400, f"kind must be one of {', '.join(cards.KINDS)}")
    sts = None
    status = (status or "").strip().lower()
    if status in ("known", "confirmed+edited"):
        sts = list(cards.KNOWN)
    elif status and status != "all":
        sts = [s for s in status.split(",") if s in cards.STATUSES]
        if not sts:
            raise HTTPException(400, f"status must be all, known or one of {', '.join(cards.STATUSES)}")
    limit = max(1, min(int(limit or 500), MAX_LIMIT))
    return cards.search(q[:500], k=limit, confirmed_only=False, kind=kind or None, quantity=quantity[:120] or None,
                        statuses=sts or [s for s in cards.STATUSES if s != "rejected"], paper=paper or None)


@router.get("/api/cards")
def api_cards(kind: str = "", q: str = "", quantity: str = "", status: str = "", paper: str = "", limit: int = 500):
    rows = _query(kind, q, quantity, status, paper, limit)
    by_id = _notes()
    links_by, by_card = _link_maps()
    out = [card_out(c, by_id, links_by, by_card) for c in rows if c["paper"] in by_id]
    if not q.strip():  # a stable order for browsing: paper, then the card's place in it
        out.sort(key=lambda c: (c["paper_title"].lower(), c["created"]))
    names: dict[str, int] = {}
    for c in cards.card_store().all_cards():
        if c["kind"] == "quantity" and c.get("quantity") and c["status"] != "rejected":
            nm = cards.concept(c["quantity"]) or (c["quantity"].get("name") or "").lower()
            if nm:
                names[nm] = names.get(nm, 0) + 1
    return {"cards": out, "total": len(out),
            "quantities": [{"name": k, "count": v} for k, v in sorted(names.items(), key=lambda x: (-x[1], x[0]))][:60]}


def _cell(v) -> str:
    """A CSV cell that a spreadsheet won't run as a formula."""
    if v is None:
        return ""
    if isinstance(v, (int, float)) and not isinstance(v, bool):
        return repr(float(v)) if isinstance(v, float) else str(v)
    s = str(v).replace("\r", " ").replace("\n", " ")
    return "'" + s if s[:1] in ("=", "+", "-", "@", "\t") else s


CSV_COLUMNS = ("kind", "name", "symbol", "value", "uncertainty", "unit", "canonical_value", "canonical_uncertainty",
               "canonical_unit", "conditions", "text", "latex", "paper", "citekey", "year", "where", "quote", "status",
               "origin", "conflicts", "card_id")


@router.get("/api/cards/export.csv")
def api_cards_csv(kind: str = "", q: str = "", quantity: str = "", status: str = "", paper: str = "",
                  limit: int = MAX_LIMIT, include_private: bool = True):
    rows = _query(kind, q, quantity, status, paper, limit)
    by_id = _notes()
    links_by, by_card = _link_maps()
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLUMNS)
    for c in rows:
        if c["paper"] not in by_id or (c.get("private") and not include_private):
            continue
        o = card_out(c, by_id, links_by, by_card)
        qd = c.get("quantity") or {}
        w.writerow([_cell(x) for x in (
            c["kind"], qd.get("name"), qd.get("symbol"), qd.get("value"), qd.get("uncertainty"), qd.get("unit"),
            qd.get("canonical_unit_value"), qd.get("canonical_uncertainty"), qd.get("canonical_unit"),
            qd.get("conditions"), c.get("text"), c.get("latex"), o["paper_title"], o["citekey"], o.get("year"),
            o["where"], (c.get("locator") or {}).get("quote"), c["status"], c["origin"], o.get("conflicts", 0), c["id"])])
    name = "idea-cards" + (f"-{re.sub(r'[^a-z0-9]+', '-', (quantity or kind).lower()).strip('-')}" if (quantity or kind) else "")
    return Response(buf.getvalue(), media_type="text/csv; charset=utf-8",
                    headers={"Content-Disposition": f'attachment; filename="{name[:60]}.csv"'})


@router.get("/api/cards/links")
def api_card_links():
    return {"links": cards.links()}


# ---------------------------------------------------------------- for server.py: graph, agent tool, chat
def graph_extras() -> tuple[dict, list]:
    """({paper id: card count}, [{a_paper, b_paper, kind, conflict, a, b}]) for /api/graph. Never raises."""
    try:
        counts = cards.counts()
        lks = [{"a_paper": ln["a_paper"], "b_paper": ln["b_paper"], "kind": ln["kind"], "conflict": ln["conflict"],
                "a": ln["a"], "b": ln["b"]} for ln in cards.links()]
        return counts, lks
    except Exception as e:
        print(f"[research_brain] cards for the graph failed: {e!r}")
        return {}, []


def signature() -> str:
    try:
        return cards.signature()
    except Exception:
        return ""


def for_ai(query: str, agent, k: int = 8, kind: str = "") -> list[dict]:
    """Confirmed (or edited) cards matching `query` that this agent may see: not private, and not from a
    private paper (app/privacy.py)."""
    try:
        rows = cards.search(query, k=k * 3, confirmed_only=True, kind=kind or None)
    except Exception as e:
        print(f"[research_brain] cards search failed: {e!r}")
        return []
    pv = _privacy()
    out = []
    for c in rows:
        if c.get("private"):
            local_ok = bool(pv and pv.local_exception(agent))
            if not local_ok:
                continue
        if pv is not None:
            try:
                if not pv.allow_ai(c["paper"], agent):
                    continue
            except Exception:
                continue
        out.append(c)
    return out[:k]


def has_known() -> bool:
    try:
        return any(c["status"] in cards.KNOWN for c in cards.card_store().all_cards())
    except Exception:
        return False


def fmt_for_ai(c: dict, titles: dict) -> str:
    where = cards.where(c)
    head = f"[[{c['paper']}]]" + (f" {where}" if where and where != "abstract" else "")
    t = (titles.get(c["paper"]) or "")[:80]
    body = c.get("text") or ""
    if c["kind"] == "equation" and c.get("latex"):
        body = f"$${c['latex']}$$ {body}".strip()
    q = c.get("quantity") or {}
    if q.get("canonical_unit_value") is not None and q.get("canonical_unit") and q.get("canonical_unit") != q.get("unit"):
        body += f" (= {units.fmt(q['canonical_unit_value'])} {q['canonical_unit']})"
    quote = (c.get("locator") or {}).get("quote") or ""
    mine = "the user's own card" if c["origin"] == "user" else "confirmed by the user"
    return f"{head} | {t} ({c['kind']}, {mine})\n{body}" + (f"\nQuote: \"{quote}\"" if quote else "")

"""A paper's title and authors, checked against its first page by the AI when they look wrong.

A paper added from a PDF without a DOI gets the title the app guesses from its first page, and sometimes that's an
affiliation ("*Plasma Propulsion Laboratory, Imperial College..."), an author ("Kazuma Emoto1"), a word like
"Manuscript", or a title cut off mid-phrase ("... Hall Thruster with"); and its authors can be missing. When the
lookup by identifier finds nothing better, the AI reads page 1 and says what's printed there: title, authors, year,
DOI. Only what is really on the page is used (each title word and each author's surname must be in the text, a DOI
must be there as written), so it can't invent details. Then:
  - a DOI it found: the paper's record is looked up by it (Crossref...), which settles title, authors and the rest;
  - otherwise a suspicious title is replaced, and missing authors and year are filled in.
Titles and authors you typed yourself are never changed (a title only changes when it looks broken), and private
papers are never sent.

    suspect(note) -> [reasons]          what looks wrong ([] = nothing)
    check(nid) -> {changed, fields, reasons, skipped}
    POST /api/notes/{id}/check-details  one paper (also part of Refresh metadata)
    POST /api/papers/check-details      every paper that looks wrong, in the background → {queued}
"""
from __future__ import annotations

import json
import re
import threading
import unicodedata

from fastapi import HTTPException

PAGE_CHARS = 5000
GENERIC = re.compile(r"^\W*(accepted )?(manuscript|article|paper|preprint|untitled( paper)?|draft|document|title|"
                     r"supplementary (material|information)|author manuscript|full paper|research article|letter)\W*$", re.I)
AFFILIATION = re.compile(r"(^\s*[*†‡§\d]\s*\w)|\b(laborator(y|ies)|department|dept\.|universit[yéeàä]\w*|institut\w*|"
                         r"facult\w*|school of|college|centre|center for|corresponding author|e-?mail)\b|@", re.I)
DANGLING = re.compile(r"\b(with|and|of|the|for|in|on|by|to|a|an|from|using|via|at|into|toward|towards|under|between)\s*$", re.I)

INSTRUCTION = (
    "Below is the start of page 1 of a scientific paper. Reply with ONE JSON object and nothing else: "
    "{\"title\": \"...\", \"authors\": [\"First Last\", ...], \"year\": 2020, \"doi\": \"10....\", \"arxiv\": \"2101.01234\"}. "
    "Copy the title and the author names exactly as printed on the page (without affiliation marks like 1, *, †); "
    "use null for anything the page doesn't show. Never guess or use outside knowledge.")


def _fold(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s or ""))
    return "".join(ch for ch in s if not unicodedata.combining(ch)).lower()


def _words(s: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", _fold(s))


def suspect(n: dict) -> list[str]:
    """Why a paper's title or authors look wrong ([] when they look fine)."""
    if n.get("type") != "paper":
        return []
    why = []
    t = " ".join(str(n.get("title") or "").split())
    authors = [a for a in (n.get("authors") or []) if str(a).strip()]
    if not authors:
        why.append("no authors")
    if not t or GENERIC.match(t):
        why.append("no real title")
    elif AFFILIATION.search(t):
        why.append("the title looks like an affiliation")
    elif DANGLING.search(t):
        why.append("the title looks cut off")
    else:
        tw = _words(t)
        surnames = {_words(a)[-1] for a in authors if _words(a)}
        short = len(t.split()) <= 4
        if short and (re.search(r"[A-Za-z][\d*†‡]+(\s|,|$)", t) or (surnames and set(tw) & surnames)):
            why.append("the title looks like a name")  # "Kazuma Emoto1", "V. M. Simulik1", an author's own name
        elif len(tw) < 3:
            why.append("the title is very short")
    return why


def _parse(text: str) -> dict:
    m = re.search(r"\{[\s\S]*\}", text or "")
    if not m:
        return {}
    try:
        d = json.loads(m.group(0))
    except ValueError:
        return {}
    return d if isinstance(d, dict) else {}


def on_page(found: dict, page: str) -> dict:
    """What the AI said, keeping only what is really on the page."""
    words = set(_words(page))
    out: dict = {}
    title = " ".join(str(found.get("title") or "").split())[:400]
    tw = [w for w in _words(title) if len(w) > 2]
    if title and tw and sum(w in words for w in tw) >= 0.9 * len(tw) and not suspect({"type": "paper", "title": title, "authors": ["x"]}):
        out["title"] = title
    authors = []
    for a in found.get("authors") or []:
        a = re.sub(r"[\d*†‡§]+", "", " ".join(str(a).split())).strip(" ,;")
        if not a or len(a) > 80:
            continue
        last = _words(a)[-1:] if _words(a) else []
        if last and last[0] in words and a not in authors:
            authors.append(a)
    if authors:
        out["authors"] = authors[:40]
    y = found.get("year")
    if isinstance(y, int) and 1800 < y < 2200 and str(y) in page:
        out["year"] = y
    doi = str(found.get("doi") or "").strip().rstrip(".")
    doi = re.sub(r"^(https?://(dx\.)?doi\.org/|doi:\s*)", "", doi, flags=re.I)
    if re.fullmatch(r"10\.\d{4,9}/\S+", doi) and doi.lower() in page.lower():
        out["doi"] = doi
    ax = re.sub(r"^arxiv:\s*", "", str(found.get("arxiv") or "").strip(), flags=re.I)
    ax = re.sub(r"v\d+$", "", ax)
    if re.fullmatch(r"\d{4}\.\d{4,5}", ax) and ax in page:
        out["arxiv"] = ax
    return out


class Ctx:
    store = None
    ask = None          # (instruction, context, purpose, sources) -> text   (app/server.py _ask)
    may_send = None     # (note) -> bool: not private for the active agent
    refresh = None      # (nid) -> dict: the record looked up again by identifier (metadata.refresh, force)
    publish = None


ctx = Ctx()


def _first_page(nid: str) -> str:
    try:
        text = ctx.store.text_path(nid).read_text(encoding="utf-8", errors="replace")
    except (OSError, KeyError):
        return ""
    page = text.split("[[page 2]]")[0]
    return page.replace("[[page 1]]", "").strip()[:PAGE_CHARS]


def check(nid: str) -> dict:
    store = ctx.store
    n = store.get(nid)
    reasons = suspect(n)
    if not reasons:
        return {"changed": False, "reasons": [], "fields": {}}
    if not ctx.may_send(n):
        return {"changed": False, "reasons": reasons, "fields": {}, "skipped": "private"}
    page = _first_page(nid)
    if len(page) < 40:
        return {"changed": False, "reasons": reasons, "fields": {}, "skipped": "no text"}
    found = on_page(_parse(ctx.ask(INSTRUCTION, "PAGE 1:\n\n" + page, "paper details", [nid])), page)
    fields: dict = {}
    if found.get("doi") or found.get("arxiv"):  # an identifier: the paper's own record settles it
        with store.lock:
            m = store.get(nid)
            for k in ("doi", "arxiv"):
                if found.get(k) and not m.get(k) and store.find_duplicate(found[k] if k == "doi" else "", found[k] if k == "arxiv" else "", "") is None:
                    m[k] = found[k]
                    fields[k] = found[k]
            if fields:
                store.save(m, touch=False)
        if fields and ctx.refresh:
            try:
                ctx.refresh(nid)
            except Exception as e:  # offline: what the page says still goes in below
                print(f"[research_brain] details: lookup by {fields} failed: {e!r}")
    with store.lock:
        m = store.get(nid)
        left = suspect(m)
        upd = {}
        if found.get("title") and any("title" in r for r in left):
            upd["title"] = found["title"]
        if found.get("authors") and not [a for a in (m.get("authors") or []) if str(a).strip()]:
            upd["authors"] = found["authors"]
        if found.get("year") and not m.get("year"):
            upd["year"] = found["year"]
        if upd:
            for k, v in upd.items():
                m[k] = v
            store.save(m)
            fields.update(upd)
    if fields and ctx.publish:
        ctx.publish({"type": "notes-changed", "ids": [nid], "reason": "details"})
    return {"changed": bool(fields), "reasons": reasons, "fields": fields}


def check_quietly(nid: str) -> None:
    """After an import, in the background: a paper whose details look wrong is checked (no AI set up, offline,
    private: nothing happens)."""
    try:
        n = ctx.store.get(nid)
        if suspect(n):
            check(nid)
    except Exception as e:  # never in the way of an import
        print(f"[research_brain] details check skipped for {nid}: {e!r}")


# one paper at a time, in the background: a folder of 200 PDFs doesn't start 200 AI calls at once. A paper waits
# DELAY seconds first, so the lookup by identifier (app/metadata.py, also in the background) has had its turn.
DELAY = 25.0
_queue: list[tuple[float, str]] = []
_cv = threading.Condition()
_worker = None


def later(nid: str, delay: float | None = None) -> None:
    global _worker
    import time
    with _cv:
        if any(q[1] == nid for q in _queue):
            return
        _queue.append((time.monotonic() + (DELAY if delay is None else delay), nid))
        _cv.notify()
        if _worker is None or not _worker.is_alive():
            _worker = threading.Thread(target=_work, daemon=True, name="rb-details")
            _worker.start()


def _work() -> None:
    import time
    while True:
        with _cv:
            while not _queue:
                if not _cv.wait(timeout=300):
                    return
            _queue.sort()
            at, nid = _queue[0]
            wait = at - time.monotonic()
            if wait > 0:
                _cv.wait(timeout=wait)
                continue
            _queue.pop(0)
        check_quietly(nid)


def check_all() -> dict:
    todo = [n["id"] for n in ctx.store.all() if suspect(n)]
    for nid in todo:
        later(nid, delay=0)
    return {"queued": len(todo)}


def install(app, *, store, ask, may_send, refresh, publish=None):
    ctx.store, ctx.ask, ctx.may_send, ctx.refresh, ctx.publish = store, ask, may_send, refresh, publish

    @app.post("/api/notes/{nid}/check-details")
    def api_check(nid: str):
        try:
            n = store.get(nid)
        except KeyError:
            raise HTTPException(404, f"No note with id '{nid}'")
        if n.get("type") != "paper":
            raise HTTPException(400, "Only papers have a title and authors to check")
        return check(nid)

    @app.post("/api/papers/check-details")
    def api_check_all():
        return check_all()

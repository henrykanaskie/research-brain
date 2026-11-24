"""A paper's title and authors, checked against its first page by the AI when they look wrong.

A paper added from a PDF without a DOI gets the title the app guesses from its first page, and sometimes that's an
affiliation ("*Plasma Propulsion Laboratory, Imperial College..."), an author ("Kazuma Emoto1", "A Crowe and J W
McConkey"), a word like "Manuscript", a publisher's code ("PII: S0010-4655(00)00231-9"), a cover sheet ("REPORT
DOCUMENTATION PAGE", "Download details:"), or a title cut off mid-phrase ("... Hall Thruster with"); and its authors
can be missing. In order, cheapest first:
  1. the PDF's own document info (/Author, /Title): used when each author's surname (and the title's words) are on
     the paper's first pages. No AI needed;
  2. a DOI that found nothing and looks misprinted (a journal's ISSN without its hyphen, "10.1088/00223727/42/..."):
     looked up again as it should read;
  3. the AI reads the first pages (PAGES of them, so a cover sheet doesn't hide the paper) and says what's printed
     there: title, authors, year, DOI. Only what is really on the pages is used (each title word and each author's
     surname must be in the text, a DOI must be there as written), so it can't invent details. Then:
  - a DOI it found: the paper's record is looked up by it (Crossref...), which settles title, authors and the rest;
  - otherwise a suspicious title is replaced, and missing authors and year are filled in.
A title the AI finds that continues the one the paper has (it was cut off) replaces it too. Titles and authors you
typed yourself are never changed (a title only changes when it looks broken or cut short), and private papers are
never sent.

Papers are checked after an import, and every paper in the library that looks wrong is checked once a little after
the app starts (papers added before these checks existed too). What was checked is kept in state/details-checked.json
by each paper's title and authors, so a paper is asked about again only when they change, or when the AI it needed
wasn't there.

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

PAGE_CHARS = 9000
PAGES = 3
GENERIC = re.compile(r"^\W*(accepted )?(manuscript|article|paper|preprint|untitled( paper)?|draft|document|title|"
                     r"supplementary (material|information)|author manuscript|full paper|research article|letter)\W*$", re.I)
AFFILIATION = re.compile(r"(^\s*[*†‡§\d]\s*\w)|\b(laborator(y|ies)|department|dept\.|universit[yéeàä]\w*|institut\w*|"
                         r"facult\w*|school of|college|centre|center for|corresponding author|e-?mail)\b|@", re.I)
JUNK = re.compile(r"^\W*(pii|doi)\s*[:.]|report documentation page|form approved|^\W*download details|^\W*presented at\b|"
                  r"^\W*(this|the) (article|paper) (is|was|has)\b|^\W*(see|view) (discussions|this article)|"
                  r"^\W*(journal|proceedings|transactions) of [\w ]+,? (vol|volume)\b|[:;,]\s*$", re.I)
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
    elif JUNK.search(t):
        why.append("the title isn't the paper's title")
    elif _names_only(t):
        why.append("the title looks like a name")
    elif AFFILIATION.search(t):
        why.append("the title looks like an affiliation")
    elif DANGLING.search(t):
        why.append("the title looks cut off")
    else:
        tw = _words(t)
        surnames = {_words(a)[-1] for a in authors if _words(a)}
        short = len(t.split()) <= 4
        # a name with its affiliation mark ("Emoto1", "Simulik*"), not a formula ("in N2", "CO2 lasers")
        if short and (re.search(r"\b[A-Z][a-z]+[\d*†‡]+(\s|,|$)", t) or (surnames and set(tw) & surnames)):
            why.append("the title looks like a name")  # "Kazuma Emoto1", "V. M. Simulik1", an author's own name
        elif len(tw) < 3:
            why.append("the title is very short")
    return why


def _names_only(t: str) -> bool:
    """"A Crowe and J W McConkey", "Smith, J. and Lee, K.": names and initials, nothing else."""
    toks = [x for x in re.split(r"[\s,;]+", t) if x and x.lower() not in ("and", "&", "et", "al", "al.")]
    if not 2 <= len(toks) <= 10 or not re.search(r"\b(and|&)\b|,", t, re.I):
        return False
    initials = sum(bool(re.fullmatch(r"[A-Z]\.?(-[A-Z]\.?)?", x)) for x in toks)
    return initials >= 2 and all(re.fullmatch(r"[A-Z][\w'.-]*", x) for x in toks)


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
    ready = None        # () -> bool: an AI is set up and signed in (background checks wait for one, never start one)
    publish = None


ctx = Ctx()


def _first_page(nid: str) -> str:
    """The text of the paper's first PAGES pages (a cover sheet or a publisher's download page can come first)."""
    try:
        text = ctx.store.text_path(nid).read_text(encoding="utf-8", errors="replace")
    except (OSError, KeyError):
        return ""
    page = text.split(f"[[page {PAGES + 1}]]")[0]
    return re.sub(r"\[\[page \d+\]\]", "", page).strip()[:PAGE_CHARS]


def split_authors(raw: str) -> list[str]:
    """A PDF's /Author: "A. Smith; B. Lee", "A. Smith, B. Lee and C. Wu", "Smith, A." (one person)."""
    raw = " ".join(str(raw or "").split())
    if not raw:
        return []
    if ";" in raw:
        parts = raw.split(";")
    else:
        parts = re.split(r",\s*|\s+and\s+|\s*&\s*", raw)
        # "Smith, A." / "Smith, A. B.": a surname then initials is one person, not two
        if len(parts) == 2 and re.fullmatch(r"([A-Z]\.?\s*)+", parts[1].strip()):
            parts = [f"{parts[1].strip()} {parts[0].strip()}"]
    out = []
    for a in parts:
        a = re.sub(r"[\d*†‡§]+", "", a).strip(" ,.;")
        if a and len(a) <= 80 and re.search(r"[A-Za-z]{2}", a) and a not in out:
            out.append(a)
    return out[:40]


def from_pdf(nid: str, page: str) -> dict:
    """Title and authors from the PDF's own document info, kept only when they're on the paper's first pages."""
    try:
        path = ctx.store.pdf_path(nid)
        if not path.exists():
            return {}
        from .pdftext import pdf_info
        info = pdf_info(path)
    except Exception:
        return {}
    title = re.sub(r"^\s*microsoft (word|powerpoint) - ", "", str(info.get("title") or ""), flags=re.I)
    if re.search(r"\.(pdf|docx?|tex|dvi|ps)\s*$", title, re.I):
        title = ""
    return on_page({"title": title, "authors": split_authors(info.get("author") or "")}, page)


def misprinted_doi(doi: str) -> str:
    """A DOI as it should read when a journal's ISSN lost its hyphen ("10.1088/00223727/42/28/185203" ->
    "10.1088/0022-3727/42/28/185203"); "" when it doesn't look like that."""
    m = re.fullmatch(r"(10\.\d{4,9}/)(\d{4})(\d{3}[\dXx])(/\S+)", str(doi or "").strip())
    return f"{m.group(1)}{m.group(2)}-{m.group(3)}{m.group(4)}" if m else ""


def _found(r) -> bool:
    return bool(r.get("found")) if isinstance(r, dict) else bool(r)


def _apply(nid: str, found: dict, fields: dict) -> None:
    """Fill in what the paper is missing (or has wrong) from what was found; adds to fields."""
    store = ctx.store
    with store.lock:
        m = store.get(nid)
        left = suspect(m)
        upd = {}
        t = " ".join(str(m.get("title") or "").split())
        nt = found.get("title") or ""
        if nt and nt != t and (any("title" in r for r in left)
                               or (len(nt) > len(t) + 3 and _fold(nt).startswith(_fold(t).rstrip(" .:")))):
            upd["title"] = nt  # broken, or cut short ("Fast Optical Emission Spectroscopy for Studying")
        if found.get("authors") and not [a for a in (m.get("authors") or []) if str(a).strip()]:
            upd["authors"] = found["authors"]
        if found.get("year") and not m.get("year"):
            upd["year"] = found["year"]
        if upd:
            for k, v in upd.items():
                m[k] = v
            store.save(m)
            fields.update(upd)


def check(nid: str, use_ai: bool = True) -> dict:
    """Check one paper's title and authors (see the top of this file). use_ai=False: only the PDF's own details and
    the DOI; `ai` in the result says whether the AI was asked."""
    store = ctx.store
    n = store.get(nid)
    reasons = suspect(n)
    if not reasons:
        return {"changed": False, "reasons": [], "fields": {}}
    fields: dict = {}
    page = _first_page(nid)
    # 1. the PDF's own document info
    if page:
        _apply(nid, from_pdf(nid, page), fields)
    # 2. a misprinted DOI, looked up again as it should read
    m = store.get(nid)
    fixed = misprinted_doi(m.get("doi") or "")
    if suspect(m) and fixed and ctx.refresh:
        try:
            if not _found(ctx.refresh(nid)):
                with store.lock:
                    m = store.get(nid)
                    old, m["doi"] = m.get("doi"), fixed
                    store.save(m, touch=False)
                if _found(ctx.refresh(nid)):
                    fields["doi"] = fixed
                else:
                    with store.lock:
                        m = store.get(nid)
                        m["doi"] = old
                        store.save(m, touch=False)
        except Exception as e:  # offline: the AI may still read the pages
            print(f"[research_brain] details: lookup of {fixed} failed: {e!r}")
    # 3. the AI reads the first pages
    asked = False
    if suspect(store.get(nid)) and use_ai:
        n = store.get(nid)
        if not ctx.may_send(n):
            return _done(nid, reasons, fields, skipped="private")
        if len(page) < 40:
            return _done(nid, reasons, fields, skipped="no text")
        asked = True
        found = on_page(_parse(ctx.ask(INSTRUCTION, "PAGES 1-3:\n\n" + page, "paper details", [nid])), page)
        ids: dict = {}
        if found.get("doi") or found.get("arxiv"):  # an identifier: the paper's own record settles it
            with store.lock:
                m = store.get(nid)
                for k in ("doi", "arxiv"):
                    if found.get(k) and (not m.get(k) or (k == "doi" and suspect(m))) and m.get(k) != found[k] \
                            and store.find_duplicate(found[k] if k == "doi" else "", found[k] if k == "arxiv" else "", "") is None:
                        m[k] = found[k]
                        ids[k] = found[k]
                if ids:
                    store.save(m, touch=False)
            if ids and ctx.refresh:
                try:
                    ctx.refresh(nid)
                except Exception as e:  # offline: what the page says still goes in below
                    print(f"[research_brain] details: lookup by {ids} failed: {e!r}")
            fields.update(ids)
        _apply(nid, found, fields)
    return _done(nid, reasons, fields, ai=asked)


def _done(nid: str, reasons: list, fields: dict, **extra) -> dict:
    if fields and ctx.publish:
        ctx.publish({"type": "notes-changed", "ids": [nid], "reason": "details"})
    return {"changed": bool(fields), "reasons": reasons, "fields": fields, **extra}


CHECKED = "details-checked.json"
_mem_lock = threading.Lock()


def _sig(n: dict) -> str:
    import hashlib
    key = json.dumps([n.get("title") or "", n.get("authors") or [], n.get("doi") or ""], ensure_ascii=False)
    return hashlib.sha1(key.encode("utf-8")).hexdigest()[:16]


def _checked_path():
    from pathlib import Path
    d = Path(ctx.store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / CHECKED


def _checked() -> dict:
    try:
        d = json.loads(_checked_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _remember(nid: str) -> None:
    from . import fsio
    with _mem_lock:
        d = _checked()
        try:
            d[nid] = _sig(ctx.store.get(nid))
        except KeyError:
            d.pop(nid, None)
        p = _checked_path()
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        fsio.replace(tmp, p)


def check_quietly(nid: str) -> None:
    """In the background (after an import, or the check of the whole library after the app starts): a paper whose
    details look wrong is checked. The PDF's own details and the DOI always; the AI only when one is set up and
    signed in (never started for this), and never for a private paper. A paper is remembered as checked unless it
    still needed an AI that wasn't there."""
    try:
        n = ctx.store.get(nid)
        if not suspect(n):
            return
        ai_ok = ctx.ready is None or ctx.ready()
        r = check(nid, use_ai=ai_ok)
        if ai_ok or not suspect(ctx.store.get(nid)) or r.get("skipped"):
            _remember(nid)
    except Exception as e:  # never in the way of an import
        print(f"[research_brain] details check skipped for {nid}: {e!r}")


def check_library(delay: float = 0) -> dict:
    """Every paper that looks wrong and hasn't been checked as it is now, queued (one at a time)."""
    seen = _checked()
    todo = [n["id"] for n in ctx.store.all() if suspect(n) and seen.get(n["id"]) != _sig(n)]
    for nid in todo:
        later(nid, delay=delay)
    return {"queued": len(todo)}


# one paper at a time, in the background: a folder of 200 PDFs doesn't start 200 AI calls at once. A paper waits
# DELAY seconds first, so the lookup by identifier (app/metadata.py, also in the background) has had its turn.
DELAY = 25.0
_queue: list[tuple[float, str]] = []
_cv = threading.Condition()
_worker = None


def auto() -> bool:
    """Checks after imports are on (RB_DETAILS_AUTO=0 turns them off, as the tests do)."""
    import os
    return os.environ.get("RB_DETAILS_AUTO", "1") != "0"


def later(nid: str, delay: float | None = None) -> None:
    global _worker
    import time
    if not auto():
        return
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


STARTUP_DELAY = 90.0  # after the app starts: the window, the index and the lookups go first


def install(app, *, store, ask, may_send, refresh, publish=None, ready=None):
    ctx.store, ctx.ask, ctx.may_send, ctx.refresh, ctx.publish, ctx.ready = store, ask, may_send, refresh, publish, ready
    if auto():  # the papers already in the library, once each (state/details-checked.json)
        def _start():
            try:
                print(f"[research_brain] details: {check_library(delay=0)['queued']} papers to check")
            except Exception as e:
                print(f"[research_brain] details: library check didn't start: {e!r}")
        t = threading.Timer(STARTUP_DELAY, _start)
        t.daemon = True
        t.start()

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

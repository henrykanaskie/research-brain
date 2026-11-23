"""One paper, one PDF: when a paper you added without its PDF (from a DOI, a search...) and the PDF you imported
later became two papers, the PDF's paper is folded into the first one.

    GET  /api/notes/{nid}/pdf-matches?q=     the papers it could be joined with: for a paper without a PDF, the
                                             papers with one; for a paper with a PDF, the papers without one.
                                             Closest title first; q narrows them by title, author or year.
    POST /api/notes/{a}/take-pdf  {from: b}  paper `a` gets paper `b`'s PDF, then `b` is removed.

What a merge does, in this order (copies and repointing first, `b` is removed last, so a failure part way
leaves both papers with nothing lost):
    the PDF       a stored PDF is renamed to <papers folder>/<a>.pdf; a PDF in a folder of papers you follow
                  (app/linked.py) stays where it is and is now `a`'s file (followed as before, never added again)
    the text      text/<b>.txt is copied to text/<a>.txt; the PDF's hash moves too (app/acquire.py PdfHashes)
    the fields    `a` keeps its own; blank ones are filled from `b` (authors, year, DOI, abstract...), tags
                  joined, `b`'s notes added under `a`'s, its reading status kept when `a` was only "to read"
    attached      `b`'s highlights and notes, links (yours, citations, AI connections), [[wikilinks]] to it,
                  regions, open-question evidence, idea cards, chats, AI summary, review schedule, open count
Then `b` goes to the library's .trash like any deleted note.
"""
from __future__ import annotations

import difflib
import json
import os
import re
import shutil
import time
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import fsio


class TakeIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field("", alias="from")  # the paper whose PDF it is


class Ctx:
    store = None
    with_extras = None
    wiki = None          # () -> the [[wikilink]] graph before the merge (server._wiki(index()))
    eng_lock = None
    eng_rev = None
    publish = None


ctx = Ctx()

FILL = ("authors", "year", "venue", "doi", "arxiv", "s2_id", "url", "abstract", "summary", "saved_because",
        "zotero_key", "csl", "meta_source", "meta_checked", "critique", "critiques")
BLANK = (None, "", [], {})


def _norm(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (s or "").lower()).strip()


def title_score(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, _norm(a), _norm(b)).ratio()


def _has_pdf(store, n: dict) -> bool:
    if "has_pdf" in n:
        return bool(n["has_pdf"])
    try:
        return store.pdf_path(n["id"]).is_file()
    except (KeyError, OSError):
        return False


def matching_paper(store, titles: list[str], ids: dict | None = None) -> dict | None:
    """The one paper without a PDF whose title is the same as this PDF's (app/server.py _same_title: 0.85 or more),
    for an import that matched nothing by DOI or arXiv id. Conservative: a title of at least four words, exactly
    one such paper, and none whose own DOI or arXiv id says it is another paper."""
    titles = [t for t in titles if len(_norm(t).split()) >= 4 and len(_norm(t)) >= 20]
    if not titles:
        return None
    ids = ids or {}
    keys = [_norm(t) for t in titles]
    found = []
    for n in store.all():
        if n.get("type") != "paper" or _has_pdf(store, n) or store.is_linked(n["id"]):
            continue
        nt = _norm(n.get("title") or "")
        if not nt:
            continue
        best = 0.0
        for k in keys:
            if min(len(k), len(nt)) / max(len(k), len(nt)) < 0.7:
                continue  # SequenceMatcher can't reach 0.85 on very different lengths: skip the work
            sm = difflib.SequenceMatcher(None, k, nt)
            if sm.real_quick_ratio() >= 0.85 and sm.quick_ratio() >= 0.85:
                best = max(best, sm.ratio())
        if best < 0.85:
            continue
        clash = any(ids.get(k) and n.get(k) and str(ids[k]).lower() != str(n[k]).lower() for k in ("doi", "arxiv"))
        if not clash:
            found.append(n)
        if len(found) > 1:
            return None  # two papers with that title: not sure which one it is
    return found[0] if found else None


def install(app, *, store, with_extras, wiki, eng_lock, eng_rev, publish=None):
    ctx.store, ctx.with_extras, ctx.wiki = store, with_extras, wiki
    ctx.eng_lock, ctx.eng_rev, ctx.publish = eng_lock, eng_rev, publish

    @app.get("/api/notes/{nid}/pdf-matches")
    def api_pdf_matches(nid: str, q: str = "", limit: int = 40):
        n = _paper(nid)
        want_pdf = not _has_pdf(store, n)
        ql = q.strip().lower()
        rows = []
        for o in store.all():
            if o["id"] == nid or o.get("type") != "paper" or _has_pdf(store, o) != want_pdf:
                continue
            if ql and not (ql in (o.get("title") or "").lower() or any(ql in a.lower() for a in o.get("authors") or [])
                           or ql == str(o.get("year") or "")):
                continue
            rows.append({"id": o["id"], "title": o.get("title") or o["id"], "authors": list(o.get("authors") or [])[:4],
                         "year": o.get("year"), "venue": o.get("venue") or "", "doi": o.get("doi") or "",
                         "has_pdf": want_pdf, "score": round(title_score(n.get("title") or "", o.get("title") or ""), 3)})
        rows.sort(key=lambda r: (-r["score"], (r["title"] or "").casefold()))
        return {"has_pdf": not want_pdf, "items": rows[:max(1, min(limit, 200))]}

    @app.post("/api/notes/{nid}/take-pdf")
    def api_take_pdf(nid: str, body: TakeIn):
        src = (body.from_ or "").strip()
        if not src:
            raise HTTPException(400, "Say which paper's PDF to take ({\"from\": id})")
        out = merge(nid, src)
        if ctx.publish:
            ctx.publish({"type": "notes-changed", "ids": [nid, src]})
        return out


def _paper(nid: str) -> dict:
    try:
        n = ctx.store.get(nid)
    except KeyError:
        raise HTTPException(404, f"No note with id '{nid}'")
    if n.get("type") != "paper":
        raise HTTPException(400, f"“{n.get('title') or nid}” isn't a paper: only a paper can have a PDF")
    return n


# ---------------------------------------------------------------- the merge
def merge(a_id: str, b_id: str) -> dict:
    store = ctx.store
    if a_id == b_id:
        raise HTTPException(400, "That's the same paper")
    with store.lock:
        a, b = _paper(a_id), _paper(b_id)
        for n in (a, b):
            if n.get("_yaml_error"):
                raise HTTPException(409, f"“{n['title']}” has a file that can't be read right now; fix it first")
        a_pdf, b_pdf = store.pdf_path(a_id), store.pdf_path(b_id)
        if store.is_linked(a_id) or a_pdf.is_file():
            raise HTTPException(409, f"“{a['title']}” already has a PDF. Remove it first, or keep both papers.")
        b_linked = store.links.data()["files"].get(b_id) if store.is_linked(b_id) else None
        if not b_linked and not b_pdf.is_file():
            raise HTTPException(400, f"“{b['title']}” has no PDF to give")
        before = _safe(ctx.wiki, None)  # [[wikilinks]] as they resolve now (b still there)

        # the text first (a copy), then the PDF
        bt, at = store.text_path(b_id), store.text_path(a_id)
        if bt.is_file():
            part = at.with_name(f".{at.name}.part")
            try:
                shutil.copyfile(bt, part)
                fsio.replace(part, at)
            except BaseException:
                part.unlink(missing_ok=True)
                raise
        if b_linked:  # your own file: stays where it is, now a's (b's entry is dropped just before b is removed)
            store.links.link(a_id, b_linked["f"], b_linked["rel"], b_linked.get("size", 0))
        else:
            os.replace(b_pdf, a_pdf)  # same folder: a rename, nothing copied
        _move_hash(store, b_id, a_id)

        # a's fields: its own, blanks filled from b
        a = store.get(a_id)
        for k in FILL:
            if a.get(k) in BLANK and b.get(k) not in BLANK:
                a[k] = b[k]
        a["tags"] = list(dict.fromkeys([*(a.get("tags") or []), *(b.get("tags") or [])]))
        if (b.get("body") or "").strip():
            a["body"] = (a["body"].rstrip() + "\n\n" + b["body"].strip()) if (a.get("body") or "").strip() else b["body"]
        if b.get("status") and a.get("status") in ("", "to-read") and b["status"] != "to-read":
            a["status"] = b["status"]
        extra = dict(a.get("_extra") or {})
        for k, v in (b.get("_extra") or {}).items():  # other properties (a citation key...) a doesn't have
            extra.setdefault(k, v)
        if (b.get("_extra") or {}).get("private") is True:  # b was private: so is the paper it becomes
            extra["private"] = True
        a["_extra"] = extra
        a["source"] = "pdf"
        a["links"] = list(dict.fromkeys([*a.get("links", []), *(x for x in b.get("links", []) if x not in (a_id, b_id))]))
        a["cites"] = list(dict.fromkeys([*(a.get("cites") or []), *(x for x in b.get("cites") or [] if x not in (a_id, b_id))]))
        store.save(a)

        moved = {"highlights": 0, "links": 0, "mentions": 0, "regions": 0}
        # b's highlights and notes (anything whose parent is b)
        for n in store.all():
            if n.get("parent") == b_id and n["id"] != a_id:
                kid = store.get(n["id"])
                if kid.get("_yaml_error"):
                    continue
                kid["parent"] = a_id
                store.save(kid, touch=False)
                moved["highlights"] += 1
        # links and citations from other notes
        for n in store.all():
            if n["id"] in (a_id, b_id):
                continue
            links, cites = n.get("links") or [], n.get("cites") or []
            if b_id not in links and b_id not in cites:
                continue
            o = store.get(n["id"])
            if o.get("_yaml_error"):
                continue
            o["links"] = list(dict.fromkeys(a_id if x == b_id else x for x in o.get("links") or []))
            o["cites"] = list(dict.fromkeys(a_id if x == b_id else x for x in o.get("cites") or []))
            store.save(o, touch=False)
            moved["links"] += 1
        moved["mentions"] = _move_wikilinks(before, a_id, b_id)
        for step in (_move_ai_links, _move_regions, _move_evidence, _move_cards, _move_chats, _move_ai_summary,
                     _move_review, _move_opens):
            n = _safe(lambda: step(a_id, b_id), 0)
            if step is _move_regions:
                moved["regions"] = n or 0

        # last: b itself (to the trash, as a deleted note). Its PDF and children are a's now.
        if b_linked:
            store.links.unlink(b_id)  # not "ignore": the file is followed, as a's
        store.delete(b_id)
        _safe(lambda: _trash_cards(b_id), None)
    out = ctx.with_extras(store.get(a_id))
    out["merged"] = {"from": b_id, "title": b["title"], **moved}
    return out


def _safe(fn, default):
    try:
        return fn()
    except Exception as e:  # one side table that can't be updated must not stop the merge
        print(f"[research_brain] PDF merge: {getattr(fn, '__name__', 'step')} failed: {e!r}")
        return default


def _move_hash(store, b_id: str, a_id: str) -> None:
    """The PDF's sha256 follows it (an online-only file isn't read again to hash it)."""
    from .acquire import PdfHashes
    h = PdfHashes(store)
    try:
        with h.lock:
            d = h._load()
            if b_id in d:
                d[a_id] = d.pop(b_id)
                h._save(d)
    except Exception as e:
        print(f"[research_brain] PDF merge: hash index not updated ({e!r}); it is rebuilt from the files")


_WIKI_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]*))?\]\]")


def _move_wikilinks(before: dict | None, a_id: str, b_id: str) -> int:
    """[[b's title]] and [[b-id]] in other notes now say [[a-id|what they showed]], so they open a."""
    if not before:
        return 0
    from .server import WIKI_FIELDS, _code_spans, _title_key
    store = ctx.store
    titles = before.get("titles") or {}
    sources = {sid for sid, _ in (before.get("backlinks") or {}).get(b_id, [])}
    n_changed = 0
    for sid in sorted(sources - {a_id, b_id}):
        try:
            n = store.get(sid)
        except KeyError:
            continue
        if n.get("_yaml_error"):
            continue
        fields = {}
        for f in WIKI_FIELDS:
            text = n.get(f) or ""
            if "[[" not in text:
                continue
            spans = _code_spans(text)

            def repl(m):
                if any(x <= m.start() < y for x, y in spans):
                    return m.group(0)
                raw = m.group(1).strip()
                if raw != b_id and (store.exists(raw) or titles.get(_title_key(raw)) != b_id):
                    return m.group(0)
                return f"[[{a_id}|{(m.group(2) if m.group(2) is not None else raw)}]]"
            new = _WIKI_RE.sub(repl, text)
            if new != text:
                fields[f] = new
        if fields:
            store.update(sid, fields)
            n_changed += 1
    return n_changed


def _move_ai_links(a_id: str, b_id: str) -> int:
    from . import connect_api as c
    n = 0
    with c._cache_lock:
        cache = c._load_cache()
        for k in [k for k in cache if b_id in k.split("|")]:
            x, _, y = k.partition("|")
            other = y if x == b_id else x
            e = cache.pop(k)
            if other == a_id:
                continue
            nk = c._key(a_id, other)
            if nk not in cache:
                cache[nk] = e
                n += 1
        c._save_cache(cache)
    c.REV[0] += 1
    return n


def _move_regions(a_id: str, b_id: str) -> int:
    from . import topics as t
    n = 0
    with t._lock:
        for slug in t._all_slugs(ctx.store):
            try:
                top = t._read(ctx.store, slug)
            except (KeyError, OSError, UnicodeDecodeError):
                continue
            if b_id not in top["members"]:
                continue
            top["members"] = list(dict.fromkeys(a_id if m == b_id else m for m in top["members"]))
            t._write(ctx.store, top)
            n += 1
    return n


def _move_evidence(a_id: str, b_id: str) -> int:
    """Open questions' evidence from b (its passages, the paper itself, its highlights' parent) is a's now."""
    from . import questions as q
    n = 0
    with q._lock:
        for p in sorted(q.qdir(ctx.store).glob("*.json")):
            qid = p.stem
            try:
                data = q.load(ctx.store, qid)
            except (KeyError, q.Unreadable):
                continue
            changed, keys, items = False, {i["key"] for i in data["items"]}, []
            for it in data["items"]:
                ref = it.get("ref") if isinstance(it.get("ref"), dict) else {}
                if b_id not in (ref.get("note"), ref.get("parent")):
                    items.append(it)
                    continue
                changed = True
                ref = {**ref, **{k: a_id for k in ("note", "parent") if ref.get(k) == b_id}}
                key = it["key"]
                for kind in ("passage", "paper", "note"):
                    if key.startswith(f"{kind}:{b_id}"):
                        key = f"{kind}:{a_id}" + key[len(kind) + 1 + len(b_id):]
                if key != it["key"] and key in keys:
                    continue  # a already has that piece of evidence: keep a's
                keys.add(key)
                items.append({**it, "ref": ref, "key": key})
            if changed:
                data["items"] = items
                q._save(ctx.store, qid, data)
                n += 1
    return n


def _move_cards(a_id: str, b_id: str) -> int:
    from . import cards as c
    cs = c.card_store()
    with cs.lock:
        bd = cs.load(b_id)
        if not bd["cards"] or bd.get("_unreadable"):
            return 0
        ad = cs.load(a_id)
        if ad.get("_unreadable"):
            return 0
        for card in bd["cards"]:
            ad["cards"].append({**card, "paper": a_id})
        ad["extractions"] = [*(ad.get("extractions") or []), *(bd.get("extractions") or [])]
        cs.save(a_id, ad)
        cs.save(b_id, {"cards": [], "extractions": []})
    return len(bd["cards"])


def _trash_cards(b_id: str) -> None:
    from . import cards as c
    c.card_store().trash(b_id, Path(ctx.store.trash_dir))


def _move_chats(a_id: str, b_id: str) -> int:
    """Chats about b: the current one becomes a's (or one of its past chats when a has its own)."""
    from . import chats as ch
    d = ch._dir(ctx.store)
    n = 0
    with ch._lock:
        for p in sorted(d.glob(f"paper-{b_id}*.json")):
            key = p.stem
            base, _, stamp = key.partition("~")
            if base != f"paper-{b_id}":
                continue
            new = f"paper-{a_id}" + (f"~{stamp}" if stamp else "")
            if not ch.KEY_RE.fullmatch(new):
                continue
            dest = d / f"{new}.json"
            if dest.exists():
                new = f"paper-{a_id}~m{int(time.time() * 1000) % 10**10:x}{n}"
                dest = d / f"{new}.json"
            try:
                data = json.loads(p.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    data["key"] = new
                dest.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
                p.unlink()
                n += 1
            except (OSError, ValueError):
                continue
    return n


def _move_ai_summary(a_id: str, b_id: str) -> int:
    from . import ai_summary as s
    src, dst = s._path(ctx.store.root, b_id), s._path(ctx.store.root, a_id)
    if src.is_file() and not dst.is_file():
        fsio.replace(src, dst)
        return 1
    return 0


def _move_review(a_id: str, b_id: str) -> int:
    from . import review as r
    with r._lock:
        data = ctx.store.load_json(r.FILE)
        items = data.get("items") or {}
        if b_id in items and a_id not in items:
            items[a_id] = items.pop(b_id)
            data["items"] = items
            ctx.store.save_json(r.FILE, data)
            return 1
    return 0


def _move_opens(a_id: str, b_id: str) -> int:
    with ctx.eng_lock:
        data = ctx.store.load_json("engagement.json")
        if b_id not in data:
            return 0
        data[a_id] = int(data.get(a_id, 0) or 0) + int(data.pop(b_id) or 0)
        ctx.store.save_json("engagement.json", data)
        ctx.eng_rev[0] += 1
    return 1

"""Real citation links between papers already in the library.

For a paper with an s2_id (or DOI / arXiv id) we ask Semantic Scholar for its references and store
`cites: [note-id, ...]` in its frontmatter for the referenced papers that are in the library
(matched by s2_id, DOI, arXiv id, then exact normalised title). References outside the library are
ignored. `cites` is derived data: writing it never changes a note's `updated` time.

- On import, `schedule(nid)` runs in a background thread and links both ways: the new paper's
  references, and library papers that cite it (from its citations list).
- `start_backfill()` walks the whole library as a background job (one at a time); poll `get_job()`.
All Semantic Scholar calls from here go through one throttle (about one request per second) and
back off on rate limiting.
"""
from __future__ import annotations

import os
import re
import threading
import time
import uuid

from . import scholar
from .store import now_iso

MIN_INTERVAL = float(os.environ.get("RB_S2_INTERVAL", "1.05"))   # seconds between S2 requests
BACKOFF = (5.0, 20.0, 60.0)                                      # extra waits after a 429 (on top of _get's own)

_throttle_lock = threading.Lock()
_last_call = [0.0]
_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_sleep = time.sleep  # patched in tests


class Unreachable(RuntimeError):
    pass


def _throttle() -> None:
    with _throttle_lock:
        wait = _last_call[0] + MIN_INTERVAL - time.monotonic()
        if wait > 0:
            _sleep(wait)
        _last_call[0] = time.monotonic()


def _fetch(key: str, citing: bool = False) -> list[dict]:
    """scholar.references with throttling and 429 backoff. Raises Unreachable when offline."""
    for attempt in range(len(BACKOFF) + 1):
        _throttle()
        try:
            return scholar.references(key, citing=citing)
        except scholar.ScholarError as e:
            msg = str(e)
            if "Couldn't reach" in msg:
                raise Unreachable(msg)
            if "rate-limit" in msg.lower() and attempt < len(BACKOFF):
                _sleep(BACKOFF[attempt])
                continue
            raise


def s2_key(n: dict) -> str:
    if n.get("s2_id"):
        return n["s2_id"]
    if n.get("doi"):
        return f"DOI:{n['doi']}"
    if n.get("arxiv"):
        return f"ARXIV:{n['arxiv']}"
    return ""


def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def _norm_arxiv(a: str) -> str:
    return re.sub(r"v\d+$", "", (a or "").strip().lower())


class LibraryIndex:
    """Lookup from S2 id / DOI / arXiv id / title to a library paper id."""

    def __init__(self, notes: list[dict]):
        self.s2, self.doi, self.arxiv, self.title = {}, {}, {}, {}
        for n in sorted(notes, key=lambda n: n.get("created") or ""):
            if n["type"] != "paper":
                continue
            if n.get("s2_id"):
                self.s2.setdefault(n["s2_id"], n["id"])
            if n.get("doi"):
                self.doi.setdefault(n["doi"].lower(), n["id"])
            if n.get("arxiv"):
                self.arxiv.setdefault(_norm_arxiv(n["arxiv"]), n["id"])
            t = _norm_title(n.get("title", ""))
            if len(t) >= 12:
                self.title.setdefault(t, n["id"])

    def match(self, ref: dict) -> str | None:
        return (self.s2.get(ref.get("s2_id") or "\0") or self.doi.get((ref.get("doi") or "\0").lower())
                or self.arxiv.get(_norm_arxiv(ref.get("arxiv") or "\0"))
                or self.title.get(_norm_title(ref.get("title") or "") or "\0"))


def _set_cites(store, nid: str, cites: list[str]) -> bool:
    """Write `cites` without bumping `updated` (it's derived, not the user's edit). True if changed."""
    with store.lock:
        try:
            note = store.get(nid)
        except KeyError:
            return False
        if note["cites"] == cites:
            return False
        note["cites"] = cites
        path = store.note_path(nid)
        tmp = path.with_suffix(".md.tmp")
        tmp.write_text(store._dump(note), encoding="utf-8")
        tmp.replace(path)
        store.rev += 1
        return True


def link_paper(store, nid: str, incoming: bool = False, lib: LibraryIndex | None = None) -> dict:
    """Fetch nid's references and store the in-library ones as `cites`. With incoming=True also add
    nid to the `cites` of library papers that cite it. Returns {"added": int, "refs": int}."""
    note = store.get(nid)
    key = s2_key(note)
    if note["type"] != "paper" or not key:
        return {"added": 0, "refs": 0}
    lib = lib or LibraryIndex(store.all())
    refs = _fetch(key)
    found = []
    for r in refs:
        t = lib.match(r)
        if t and t != nid and t not in found:
            found.append(t)
    cur = [c for c in store.get(nid)["cites"] if store.exists(c)]
    merged = cur + [t for t in found if t not in cur]
    added = len(merged) - len(cur)
    _set_cites(store, nid, merged)
    if incoming:
        for r in _fetch(key, citing=True):
            src = lib.match(r)
            if src and src != nid:
                try:
                    sc = store.get(src)["cites"]
                except KeyError:
                    continue
                if nid not in sc and _set_cites(store, src, sc + [nid]):
                    added += 1
    return {"added": added, "refs": len(refs)}


def schedule(store, nid: str) -> None:
    """Link a just-imported paper in the background; never raises, never blocks the request."""
    if os.environ.get("RB_CITES_ON_IMPORT", "1") == "0":
        return

    def run():
        try:
            link_paper(store, nid, incoming=True)
        except Exception as e:  # offline, rate-limited, note deleted meanwhile...
            print(f"[research_brain] citation links for {nid} skipped: {e}")
    threading.Thread(target=run, daemon=True, name=f"cites-{nid}").start()


# ---------------------------------------------------------------- backfill job
def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        j = _jobs.get(job_id)
        return dict(j, errors=list(j["errors"])) if j else None


def start_backfill(store) -> tuple[str, bool]:
    """Start (or return the running) backfill job. Returns (job_id, already_running)."""
    with _jobs_lock:
        for j in _jobs.values():
            if j["state"] == "running":
                return j["id"], True
        jid = uuid.uuid4().hex[:12]
        papers = [n for n in store.all() if n["type"] == "paper" and s2_key(n)]
        _jobs[jid] = {"id": jid, "state": "running", "done": 0, "total": len(papers), "linked": 0,
                      "skipped": 0, "errors": [], "current": "", "message": "", "started": now_iso(),
                      "finished": None}
    threading.Thread(target=_run_backfill, args=(store, jid, [p["id"] for p in papers]), daemon=True,
                     name=f"cites-backfill-{jid}").start()
    return jid, False


def _upd(jid: str, **kw) -> None:
    with _jobs_lock:
        _jobs[jid].update(kw)


def _run_backfill(store, jid: str, ids: list[str]) -> None:
    lib = LibraryIndex(store.all())
    linked = skipped = 0
    errors: list[str] = []
    try:
        for i, nid in enumerate(ids):
            if not store.exists(nid):
                skipped += 1
                _upd(jid, done=i + 1, skipped=skipped)
                continue
            _upd(jid, current=store.get(nid)["title"])
            try:
                linked += link_paper(store, nid, lib=lib)["added"]
            except Unreachable as e:
                raise
            except Exception as e:
                errors.append(f"{store.get(nid)['title'][:80]}: {e}")
                skipped += 1
            with _jobs_lock:
                _jobs[jid].update(done=i + 1, linked=linked, skipped=skipped)
                _jobs[jid]["errors"] = errors[-20:]
        msg = (f"Added {linked} citation link{'s' if linked != 1 else ''} across {len(ids)} paper"
               f"{'s' if len(ids) != 1 else ''}.") if ids else "No papers with a DOI, arXiv id or Semantic Scholar id."
        _upd(jid, state="done", current="", message=msg, finished=now_iso())
    except Unreachable as e:
        _upd(jid, state="error", current="", message=str(e), finished=now_iso())
    except Exception as e:  # never leave a job stuck in "running"
        _upd(jid, state="error", current="", message=f"Backfill failed: {e}", finished=now_iso())

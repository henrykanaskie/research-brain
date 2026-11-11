"""Find and download a legal open-access PDF for a paper added by DOI, arXiv id or title.

Runs in the background after the paper's note is created, so adding a paper never waits on a download.
Candidates, in order (each tried at most once):

    1. arXiv:                      https://arxiv.org/pdf/<id>          (when the paper has an arXiv id)
    2. Semantic Scholar:           the paper's openAccessPdf URL        (from the lookup, when present)
    3. Unpaywall (by DOI):         best_oa_location, then other oa_locations' url_for_pdf
                                   (only when you set a contact email in Settings; Unpaywall requires one)

Every download goes through app/urlfetch.download_pdf (http/https only, public addresses only, every
redirect re-checked, size cap, "%PDF" sniffing). Overall caps: MAX_MB per file, DEADLINE seconds per paper,
at most MAX_PARALLEL papers at once. The PDF is attached only if the paper still has none; a PDF whose
sha256 already belongs to another note is not stored twice. When nothing is found the paper simply stays
metadata-only (title, authors, abstract, links); the result is recorded in state/acquire.json so the app
can say "no open-access copy found" and offer the browser extension's "Save PDF" instead.

This never uses the user's credentials, cookies or institutional access: paywalled PDFs come only through
the browser extension, which the user triggers one paper at a time.

Tests replace `_download`, `_unpaywall` and `extract` (no network).
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from . import urlfetch

MAX_MB = int(os.environ.get("RB_OA_MAX_MB", "60"))
DEADLINE = float(os.environ.get("RB_OA_DEADLINE", "90"))
MAX_PARALLEL = 2
UNPAYWALL = "https://api.unpaywall.org/v2/"
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}$")

_sem = threading.BoundedSemaphore(MAX_PARALLEL)
_lock = threading.Lock()
_running: set[str] = set()


def enabled() -> bool:
    return os.environ.get("RB_ACQUIRE", "1") != "0"


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


# ---------------------------------------------------------------- replaceable I/O (tests)
def _download(url: str, max_bytes: int) -> tuple[Path, str]:
    return urlfetch.download_pdf(url, max_bytes=max_bytes)


def _unpaywall(doi: str, email: str) -> dict | None:
    """Unpaywall's record for `doi` (fixed host, JSON only), or None."""
    import requests
    from urllib.parse import quote
    try:
        r = requests.get(UNPAYWALL + quote(doi, safe="/"), params={"email": email}, timeout=(8, 20),
                         headers={"User-Agent": "research_brain (open-access PDF lookup)"})
    except requests.RequestException:
        return None
    if r.status_code != 200:
        return None
    try:
        return r.json()
    except ValueError:
        return None


def extract(pdf: Path) -> tuple[str, str]:
    from .pdftext import extract as _x
    return _x(pdf)


# ---------------------------------------------------------------- sha256 index of stored PDFs
def sha256_file(p: Path) -> str:
    h = hashlib.sha256()
    with open(p, "rb") as f:
        while chunk := f.read(1 << 20):
            h.update(chunk)
    return h.hexdigest()


class PdfHashes:
    """sha256 of every pdfs/<id>.pdf, cached in .index/pdf_hashes.json (derived: rebuilt from the files,
    re-hashing only files whose size or mtime changed)."""

    def __init__(self, store):
        self.store = store
        self.path = Path(store.index_dir) / "pdf_hashes.json"
        self.lock = threading.Lock()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            return d if isinstance(d, dict) else {}
        except (OSError, ValueError):
            return {}

    def _save(self, d: dict) -> None:
        tmp = self.path.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8", newline="\n")
        tmp.replace(self.path)

    def refresh(self) -> dict:
        """{nid: sha} for every stored PDF."""
        with self.lock:
            old, new, changed = self._load(), {}, False
            for p in Path(self.store.pdf_dir).glob("*.pdf"):
                try:
                    s = p.stat()
                except OSError:
                    continue
                key = [s.st_size, s.st_mtime_ns]
                rec = old.get(p.stem)
                if isinstance(rec, list) and len(rec) == 3 and rec[:2] == key:
                    new[p.stem] = rec
                else:
                    try:
                        new[p.stem] = key + [sha256_file(p)]
                    except OSError:
                        continue
                    changed = True
            if changed or set(new) != set(old):
                self._save(new)
            return {k: v[2] for k, v in new.items()}

    def owner(self, sha: str, exclude: str = "") -> str:
        """The note whose stored PDF has this sha256, or ""."""
        for nid, h in self.refresh().items():
            if h == sha and nid != exclude and self.store.exists(nid):
                return nid
        return ""


# ---------------------------------------------------------------- status (state/acquire.json)
def _status_path(store) -> Path:
    d = Path(store.root) / "state"
    d.mkdir(exist_ok=True)
    return d / "acquire.json"


def status(store, nid: str) -> dict:
    with _lock:
        running = nid in _running
    try:
        rec = json.loads(_status_path(store).read_text(encoding="utf-8")).get(nid) or {}
    except (OSError, ValueError, AttributeError):
        rec = {}
    if running:
        rec = dict(rec, state="running")
    return rec or {"state": "none"}


def _record(store, nid: str, rec: dict) -> None:
    with store.lock:
        p = _status_path(store)
        try:
            data = json.loads(p.read_text(encoding="utf-8"))
            data = data if isinstance(data, dict) else {}
        except (OSError, ValueError):
            data = {}
        data[nid] = rec
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        tmp.replace(p)


# ---------------------------------------------------------------- candidates
def candidates(note: dict, meta: dict | None, email: str) -> list[tuple[str, str]]:
    """[(source, url)], best first, without duplicates. Unpaywall is queried here (one JSON request)."""
    out: list[tuple[str, str]] = []
    arxiv = (note.get("arxiv") or (meta or {}).get("arxiv") or "").strip()
    if arxiv and re.fullmatch(r"[\w.\-/]{4,40}", arxiv):
        out.append(("arxiv", f"https://arxiv.org/pdf/{arxiv}"))
    oa = (meta or {}).get("oa_pdf") or ""
    if isinstance(oa, dict):
        oa = oa.get("url") or ""
    if oa:
        out.append(("semantic-scholar", str(oa)))
    doi = (note.get("doi") or (meta or {}).get("doi") or "").strip()
    if doi and email and EMAIL_RE.match(email):
        rec = _unpaywall(doi, email) or {}
        locs = [rec.get("best_oa_location") or {}] + list(rec.get("oa_locations") or [])
        for loc in locs:
            u = (loc or {}).get("url_for_pdf")
            if u:
                out.append(("unpaywall", str(u)))
    seen, uniq = set(), []
    for s, u in out:
        if u not in seen and u.startswith(("http://", "https://")):
            seen.add(u)
            uniq.append((s, u))
    return uniq[:6]


# ---------------------------------------------------------------- the job
def acquire(store, nid: str, meta: dict | None = None, *, email: str = "", hashes: PdfHashes | None = None,
            on_done: Callable[[str, dict], None] | None = None) -> dict:
    """Try the candidates for `nid` now (blocking). Returns and records the result."""
    started = time.monotonic()
    tried: list[dict] = []
    try:
        note = store.get(nid)
    except KeyError:
        return {"state": "gone"}
    if note.get("has_pdf"):
        rec = {"state": "has-pdf", "at": _now()}
        _record(store, nid, rec)
        return rec
    rec = {"state": "not-found", "at": _now(), "tried": tried}
    try:
        cands = candidates(note, meta, email)
    except Exception as e:  # Unpaywall down, odd data...
        cands = []
        tried.append({"source": "unpaywall", "error": str(e)[:200]})
    for source, url in cands:
        if time.monotonic() - started > DEADLINE:
            tried.append({"source": source, "url": url, "error": "skipped (time limit)"})
            continue
        try:
            tmp, final = _download(url, MAX_MB << 20)
        except urlfetch.FetchError as e:
            tried.append({"source": source, "url": url, "error": str(e)[:200]})
            continue
        except Exception as e:
            tried.append({"source": source, "url": url, "error": f"{e.__class__.__name__}: {e}"[:200]})
            continue
        try:
            sha = sha256_file(tmp)
            other = (hashes or PdfHashes(store)).owner(sha, exclude=nid)
            if other:
                rec = {"state": "duplicate", "at": _now(), "source": source, "url": url, "sha256": sha,
                       "same_as": other, "tried": tried}
                break
            try:
                text, _ = extract(tmp)
            except Exception as e:
                tried.append({"source": source, "url": url, "error": f"unreadable PDF: {e}"[:200]})
                continue
            with store.lock:
                if not store.exists(nid):
                    return {"state": "gone"}
                if store.pdf_path(nid).exists():  # the user attached one meanwhile: keep theirs
                    rec = {"state": "has-pdf", "at": _now()}
                    break
                shutil.move(str(tmp), store.pdf_path(nid))
                if text.strip():
                    store.text_path(nid).write_text(text, encoding="utf-8", newline="\n")
                n = store.get(nid)
                store.save(n)  # bumps the revision so indexes and open pages see the new PDF
            rec = {"state": "found", "at": _now(), "source": source, "url": url, "final_url": final,
                   "sha256": sha, "tried": tried}
            break
        finally:
            shutil.rmtree(Path(tmp).parent, ignore_errors=True)
    if not cands and rec["state"] == "not-found":
        rec["message"] = ("No open-access source to try" + ("" if email else
                          " (set a contact email in Settings to search Unpaywall by DOI)"))
    _record(store, nid, rec)
    if on_done:
        try:
            on_done(nid, rec)
        except Exception:
            pass
    return rec


def schedule(store, nid: str, meta: dict | None = None, **kw) -> bool:
    """Start acquire() in a background thread (at most MAX_PARALLEL run at once; one per note).
    Returns False when disabled or already running for this note. Never raises."""
    if not enabled():
        return False
    with _lock:
        if nid in _running:
            return False
        _running.add(nid)

    def run():
        try:
            with _sem:
                acquire(store, nid, meta, **kw)
        except Exception as e:
            print(f"[research_brain] open-access PDF for {nid} skipped: {e!r}")
        finally:
            with _lock:
                _running.discard(nid)
    threading.Thread(target=run, daemon=True, name=f"oa-{nid}").start()
    return True


def running() -> set[str]:
    with _lock:
        return set(_running)

"""A paywalled paper through your own library's subscription.

Settings › Open-access PDFs takes your library's sign-in link (its proxy prefix, for example Oregon State's
https://oregonstate.idm.oclc.org/login?url=, or one with {url} where the address goes). A paper without a PDF then
has "Through your library": it opens the paper's page (its DOI, else its link) through that sign-in, in your
browser, with your account. Nothing here signs in or downloads for you.

While that page is open, research brain watches your Downloads folder for a while (WATCH_MINUTES). The first new
PDF that is this paper (its DOI on the first pages, or its title there) is copied in and attached; any other PDF
you download meanwhile is left alone. Your Downloads copy stays where it is.

    POST /api/notes/{id}/library-access -> {"url", "watching": true, "minutes"}
    GET  /api/notes/{id}/library-access -> {"watching": bool, "attached": bool}
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

from .metadata import clean_doi, norm_title

WATCH_MINUTES = 10
POLL_S = 2.0
PARTIAL = (".crdownload", ".part", ".download", ".partial", ".tmp")
_watching: dict[str, dict] = {}
_lock = threading.Lock()


class LibraryError(ValueError):
    pass


def clean_proxy(p: str) -> str:
    """The sign-in link as saved: https only, and somewhere for the paper's address to go."""
    p = (p or "").strip()
    if not p:
        return ""
    if not re.match(r"(?i)^https://[^\s/]+\.[^\s/]+/", p):
        raise LibraryError("Paste your library's sign-in link, starting with https://")
    if "{url}" not in p and not re.search(r"(?i)[?&](url|qurl)=$", p):
        raise LibraryError("The link should end in url= (or contain {url} where the paper's address goes)")
    return p


def target(n: dict) -> str:
    """The paper's own address: its DOI, else its saved link (http/https only)."""
    d = clean_doi(n.get("doi") or "", keep_case=True)
    if d:
        return "https://doi.org/" + d
    u = str(n.get("url") or "").strip()
    return u if re.match(r"(?i)^https?://", u) else ""


def link(proxy: str, n: dict) -> str:
    t = target(n)
    if not proxy or not t:
        return ""
    return proxy.replace("{url}", t) if "{url}" in proxy else proxy + t


def downloads() -> Path:
    return Path(os.environ.get("RB_DOWNLOADS") or (Path.home() / "Downloads"))


def is_it(n: dict, text: str) -> bool:
    """Is this PDF's text this paper? Its DOI printed on the first pages, or its whole title there."""
    head = (text or "")[:20000]
    d = clean_doi(n.get("doi") or "")
    if d and d in head.lower():
        return True
    t = norm_title(n.get("title") or "")
    return len(t.split()) >= 4 and t in norm_title(head)


def _pdfs(folder: Path) -> dict[Path, tuple[float, int]]:
    out = {}
    try:
        for p in folder.iterdir():
            if p.suffix.lower() == ".pdf" and not p.name.startswith(".") and p.is_file():
                st = p.stat()
                out[p] = (st.st_mtime, st.st_size)
    except OSError:
        pass
    return out


def watch(nid: str, n: dict, attach, read, *, folder: Path | None = None, minutes: float = WATCH_MINUTES,
          poll: float = POLL_S) -> None:
    """Watch `folder` (Downloads) for this paper's PDF in the background. read(path) -> text; attach(nid, path) once
    it's found. A second call while watching only extends the time."""
    folder = folder or downloads()
    with _lock:
        w = _watching.get(nid)
        if w and w["until"] > time.time():
            w["until"] = time.time() + minutes * 60
            return
        w = _watching[nid] = {"until": time.time() + minutes * 60, "attached": False}
    before = _pdfs(folder)

    def run():
        seen: dict[Path, tuple[float, int]] = {}
        checked: set[tuple[Path, float, int]] = set()
        try:
            while time.time() < w["until"]:
                time.sleep(poll)
                now = _pdfs(folder)
                for p, sig in now.items():
                    if before.get(p) == sig or (p, *sig) in checked:
                        continue
                    if seen.get(p) != sig:  # still being written: wait for it to stop growing
                        seen[p] = sig
                        continue
                    if any((folder / (p.name + s)).exists() for s in PARTIAL):
                        continue
                    checked.add((p, *sig))
                    try:
                        text = read(p)
                    except Exception:
                        continue
                    if is_it(n, text):
                        attach(nid, p)
                        w["attached"] = True
                        return
        finally:
            w["until"] = 0

    threading.Thread(target=run, daemon=True, name=f"rb-library-{nid[:20]}").start()


def status(nid: str) -> dict:
    w = _watching.get(nid) or {}
    return {"watching": w.get("until", 0) > time.time(), "attached": bool(w.get("attached"))}

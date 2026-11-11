"""Can you read this paper for free, or will the publisher ask you to sign in or pay?

For a paper you don't have the PDF of, the app says so quietly next to "View paper", and points "View
paper" at a free copy when one exists (a repository or author copy rather than the paywalled publisher
page). Answers, best first:

    arXiv id                      -> open (arXiv is always free)
    OpenAlex open_access by DOI   -> open (gold, green, hybrid, diamond), free (bronze: free to read on the
                                     publisher's site, sometimes after a free registration) or closed
    nothing known                 -> unknown (nothing is shown)

Results are cached in <vault>/state/access.json for 30 days ("closed" for 7, since papers open up). The
lookup sends only the DOI to OpenAlex. Tests replace `_openalex`.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
from pathlib import Path

from . import openalex

TTL_DAYS = {"open": 30, "free": 30, "closed": 7, "unknown": 2}
_lock = threading.Lock()

LABELS = {
    "open": "Free to read",
    "free": "Free on the publisher's site (may ask you to register)",
    "closed": "Paywalled: needs a subscription, a library login or an account",
    "unknown": "",
}


def _path(store) -> Path:
    d = Path(store.root) / "state"
    d.mkdir(exist_ok=True)
    return d / "access.json"


def _load(store) -> dict:
    try:
        data = json.loads(_path(store).read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, ValueError):
        return {}


def _save(store, key: str, rec: dict) -> None:
    with _lock:
        data = _load(store)
        data[key] = rec
        p = _path(store)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
        from . import fsio
        fsio.replace(tmp, p)


def _openalex(doi: str) -> dict | None:
    """{"is_oa", "oa_status", "oa_url"} for a DOI, or None when OpenAlex doesn't know it."""
    w = openalex._get(f"/works/doi:{openalex.quote(doi, safe='/.')}", {"select": "open_access,best_oa_location"})
    if not w:
        return None
    oa = w.get("open_access") or {}
    best = w.get("best_oa_location") or {}
    return {"is_oa": bool(oa.get("is_oa")), "oa_status": oa.get("oa_status") or "",
            "oa_url": best.get("pdf_url") or best.get("landing_page_url") or oa.get("oa_url") or ""}


def classify(oa: dict | None) -> tuple[str, str]:
    if not oa:
        return "unknown", ""
    st = (oa.get("oa_status") or "").lower()
    url = oa.get("oa_url") or ""
    if st in ("gold", "green", "hybrid", "diamond") or (oa.get("is_oa") and st != "bronze"):
        return "open", url
    if st == "bronze":
        return "free", url
    if st == "closed" or oa.get("is_oa") is False:
        return "closed", ""
    return "unknown", ""


def of(store, note: dict, refresh: bool = False) -> dict:
    """{"state": open|free|closed|unknown, "label", "url"} for a paper; cached."""
    arxiv = (note.get("arxiv") or "").strip()
    if arxiv:
        return {"state": "open", "label": LABELS["open"], "url": f"https://arxiv.org/pdf/{arxiv}", "via": "arxiv"}
    doi = (note.get("doi") or "").strip().lower()
    if not doi:
        return {"state": "unknown", "label": "", "url": ""}
    key = "doi:" + doi
    rec = _load(store).get(key)
    now = dt.datetime.now(dt.timezone.utc)
    if rec and not refresh:
        try:
            age = (now - dt.datetime.fromisoformat(rec["checked"])).days
        except (KeyError, ValueError, TypeError):
            age = 999
        if age < TTL_DAYS.get(rec.get("state"), 2):
            return {"state": rec["state"], "label": LABELS.get(rec["state"], ""), "url": rec.get("url", ""), "via": "openalex"}
    try:
        state, url = classify(_openalex(doi))
    except openalex.OpenAlexError:
        return {"state": "unknown", "label": "", "url": ""}  # offline or over the allowance: say nothing
    _save(store, key, {"state": state, "url": url, "checked": now.isoformat(timespec="seconds")})
    return {"state": state, "label": LABELS[state], "url": url, "via": "openalex"}

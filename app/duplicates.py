"""The same paper twice: a preprint and its journal version, or one paper added from two places, whose DOI or arXiv id
don't match (or one of them has none), so app/store.py find_duplicate can't tell.

Two papers are the same when
  - their titles match once math, accents, case and punctuation are set aside ("$\\vec E \\times \\vec B$" and
    "E × B" alike; similarity 0.92 or more, and the same numbers in them, so "Part 1" and "Part 2" never do),
  - their authors' surnames overlap, in any order (at least half of the shorter list; a list on one side only
    doesn't count against it), and
  - their years are at most one apart (a preprint and its journal version).

    GET  /api/notes/{id}/duplicates            papers that look like the same one (for the paper's panel)
    POST /api/notes/{id}/merge-duplicate {from} the other one folded into this one (app/pdfmerge.py merge)
    POST /api/notes/{id}/not-duplicate   {other} never suggested again (state/not_duplicates.json)
"""
from __future__ import annotations

import difflib
import json
import re
import threading
import unicodedata

from fastapi import HTTPException
from pydantic import BaseModel, ConfigDict, Field

from . import fsio

TITLE_MIN = 0.92
_lock = threading.Lock()

# LaTeX in titles: what it reads as, so "$\vec E \times \vec B$" compares equal to "E × B" (and "ExB")
_TEX = {"times": "x", "cdot": "", "alpha": "alpha", "beta": "beta", "gamma": "gamma", "delta": "delta",
        "epsilon": "epsilon", "varepsilon": "epsilon", "lambda": "lambda", "mu": "mu", "nu": "nu", "pi": "pi",
        "rho": "rho", "sigma": "sigma", "tau": "tau", "phi": "phi", "varphi": "phi", "omega": "omega",
        "theta": "theta", "kappa": "kappa", "chi": "chi", "psi": "psi", "eta": "eta", "zeta": "zeta", "xi": "xi"}
_UNI = {"×": "x", "·": "", "α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "λ": "lambda",
        "μ": "mu", "ν": "nu", "π": "pi", "ρ": "rho", "σ": "sigma", "τ": "tau", "φ": "phi", "ω": "omega", "θ": "theta",
        "κ": "kappa", "χ": "chi", "ψ": "psi", "η": "eta", "ζ": "zeta", "ξ": "xi", "Δ": "delta", "Ω": "omega"}


def norm_title(t: str) -> str:
    t = str(t or "")
    t = re.sub(r"\\([A-Za-z]+)", lambda m: " " + _TEX.get(m.group(1), "") + " ", t)  # \vec, \mathrm... go
    t = "".join(_UNI.get(ch, ch) for ch in t)
    t = unicodedata.normalize("NFKD", t)
    t = "".join(ch for ch in t if not unicodedata.combining(ch)).lower()
    t = re.sub(r"[^a-z0-9]+", " ", t)
    t = re.sub(r"\b(\w+?)s\b", r"\1", t)  # "thrusters" and "thruster"
    return " ".join(t.split())


def surname(a: str) -> str:
    a = unicodedata.normalize("NFKD", str(a or ""))
    a = "".join(ch for ch in a if not unicodedata.combining(ch))
    a = re.sub(r"[\d*†‡§]+", " ", a).strip()
    if "," in a:  # "Mandal, Debraj"
        a = a.split(",", 1)[0]
    else:
        parts = a.split()
        a = parts[-1] if parts else ""
    return re.sub(r"[^a-z]", "", a.lower())


def _nums(t: str) -> list[str]:
    return sorted(re.findall(r"\d+", t))


def same_paper(a: dict, b: dict) -> bool:
    if a.get("id") == b.get("id") or a.get("type") != "paper" or b.get("type") != "paper":
        return False
    ta, tb = norm_title(a.get("title")), norm_title(b.get("title"))
    if len(ta.split()) < 4 or len(tb.split()) < 4 or _nums(ta) != _nums(tb):
        return False
    if min(len(ta), len(tb)) / max(len(ta), len(tb)) < 0.8:
        return False
    sm = difflib.SequenceMatcher(None, ta, tb)
    if sm.real_quick_ratio() < TITLE_MIN or sm.quick_ratio() < TITLE_MIN or sm.ratio() < TITLE_MIN:
        return False
    ya, yb = a.get("year"), b.get("year")
    if isinstance(ya, int) and isinstance(yb, int) and abs(ya - yb) > 1:
        return False
    sa = {s for s in map(surname, a.get("authors") or []) if s}
    sb = {s for s in map(surname, b.get("authors") or []) if s}
    if sa and sb and len(sa & sb) < max(1, (min(len(sa), len(sb)) + 1) // 2):
        return False
    return True


# ---------------------------------------------------------------- "not the same" (state/not_duplicates.json)
def _path(store):
    d = store.root / "state"
    d.mkdir(exist_ok=True)
    return d / "not_duplicates.json"


def _pairs(store) -> set[tuple[str, str]]:
    try:
        data = json.loads(fsio.read_text(_path(store)))
    except (OSError, ValueError):
        return set()
    return {tuple(sorted(p)) for p in data.get("pairs", []) if isinstance(p, list) and len(p) == 2}


def not_same(store, a: str, b: str) -> None:
    with _lock:
        pairs = _pairs(store) | {tuple(sorted((a, b)))}
        from .paths import write_json_atomic
        write_json_atomic(_path(store), {"pairs": sorted(list(p) for p in pairs)})


def duplicates_of(store, nid: str, notes: list[dict] | None = None) -> list[dict]:
    """The papers that look like the same one as `nid` (not those you said aren't)."""
    try:
        n = store.get(nid)
    except KeyError:
        return []
    if n.get("type") != "paper":
        return []
    skip = _pairs(store)
    out = []
    for o in notes if notes is not None else store.all():
        if o["id"] != nid and tuple(sorted((nid, o["id"]))) not in skip and same_paper(n, o):
            out.append(o)
    return out


def match(store, meta: dict, exclude: str = "") -> dict | None:
    """The one paper in the library that is the same as a paper about to be added (title, authors, year), or None
    (none, or more than one: not sure which)."""
    probe = {"id": "\0new", "type": "paper", "title": meta.get("title") or "", "authors": meta.get("authors") or [],
             "year": meta.get("year") if isinstance(meta.get("year"), int) else None}
    found = [o for o in store.all() if o["id"] != exclude and same_paper(probe, o)]
    return found[0] if len(found) == 1 else None


def _row(store, o: dict) -> dict:
    try:
        has_pdf = store.is_linked(o["id"]) or store.pdf_path(o["id"]).is_file()
    except (KeyError, OSError):
        has_pdf = False
    return {"id": o["id"], "title": o.get("title") or o["id"], "authors": list(o.get("authors") or [])[:4],
            "year": o.get("year"), "venue": o.get("venue") or "", "doi": o.get("doi") or "", "arxiv": o.get("arxiv") or "",
            "has_pdf": bool(has_pdf)}


class MergeIn(BaseModel):
    model_config = ConfigDict(populate_by_name=True)
    from_: str = Field("", alias="from")


class NotIn(BaseModel):
    other: str = ""


def install(app, *, store, publish=None):
    from . import pdfmerge

    @app.get("/api/notes/{nid}/duplicates")
    def api_duplicates(nid: str):
        return {"items": [_row(store, o) for o in duplicates_of(store, nid)]}

    @app.post("/api/notes/{nid}/merge-duplicate")
    def api_merge(nid: str, body: MergeIn):
        other = (body.from_ or "").strip()
        try:
            a, b = store.get(nid), store.get(other)
        except KeyError:
            raise HTTPException(404, "That paper isn't in your library any more")
        if a.get("type") != "paper" or b.get("type") != "paper":
            raise HTTPException(400, "Only papers can be merged")
        out = pdfmerge.merge(nid, other, duplicate=True)
        if publish:
            publish({"type": "notes-changed", "ids": [nid, other]})
        return out

    @app.post("/api/notes/{nid}/not-duplicate")
    def api_not(nid: str, body: NotIn):
        if not body.other:
            raise HTTPException(400, "Say which paper it isn't")
        not_same(store, nid, body.other)
        return {"ok": True}

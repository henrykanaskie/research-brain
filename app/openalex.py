"""Related papers from OpenAlex, when Semantic Scholar is rate-limited or doesn't know the paper.

OpenAlex (openalex.org, run by the nonprofit OurResearch) is free and needs no key for basic queries; a free
key (Settings → Paper lookup) raises the daily allowance. Three requests per paper: the work itself (its
reference list and related works), the referenced and related works' details, and the works citing it.
Results use the same shape as scholar.discover's, with "openalex" ids instead of Semantic Scholar ones.
"""
from __future__ import annotations

from urllib.parse import quote

import requests

from . import apikeys

API = "https://api.openalex.org"
UA = {"User-Agent": "research_brain/1.0 (local personal knowledge graph)"}
SELECT = "id,display_name,publication_year,authorships,doi,ids,cited_by_count,open_access"


class OpenAlexError(RuntimeError):
    pass


def _get(path: str, params: dict | None = None, timeout: int = 20) -> dict | None:
    from . import metadata
    if not metadata.enabled():  # RB_METADATA=0: no online lookups (tests, offline use)
        raise OpenAlexError("Online paper lookups are turned off")
    params = dict(params or {})
    key = apikeys.get("openalex")
    if key:
        params["api_key"] = key
    try:
        r = requests.get(API + path, params=params, headers=UA, timeout=timeout)
    except requests.RequestException as e:
        raise OpenAlexError(f"Couldn't reach OpenAlex ({e.__class__.__name__}). Check your internet connection.")
    if r.status_code == 404:
        return None
    if r.status_code == 429:
        raise OpenAlexError("OpenAlex's free daily allowance is used up. A free OpenAlex key (Settings → Paper "
                            "lookup) gives ten times more.")
    if r.status_code != 200:
        raise OpenAlexError(f"OpenAlex error {r.status_code}")
    return r.json()


def _short(oa_id: str) -> str:
    return (oa_id or "").rsplit("/", 1)[-1]


def _paper(w: dict) -> dict:
    ids = w.get("ids") or {}
    doi = (w.get("doi") or ids.get("doi") or "").replace("https://doi.org/", "")
    arxiv = ""
    if doi.lower().startswith("10.48550/arxiv."):
        arxiv = doi.split(".", 2)[-1]
    return {
        "s2_id": "",
        "openalex": _short(w.get("id", "")),
        "title": (w.get("display_name") or "").strip(),
        "year": w.get("publication_year"),
        "authors": [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])
                    if (a.get("author") or {}).get("display_name")],
        "url": w.get("id") or "",
        "doi": doi,
        "arxiv": arxiv,
        "citationCount": w.get("cited_by_count") or 0,
        "access": _access(w.get("open_access") or {}, arxiv),
        "oa_pdf": ((w.get("open_access") or {}).get("oa_url") or "") if (w.get("open_access") or {}).get("is_oa") else "",
    }


def _access(oa: dict, arxiv: str) -> str:
    st = (oa.get("oa_status") or "").lower()
    if arxiv or st in ("gold", "green", "hybrid", "diamond"):
        return "open"
    if st == "bronze":
        return "free"
    return "closed" if st == "closed" else ""


def _work_path(doi: str = "", arxiv: str = "", openalex: str = "") -> str:
    if openalex:
        return f"/works/{quote(openalex)}"
    if doi:
        return f"/works/doi:{quote(doi, safe='/.')}"
    if arxiv:  # arXiv's DataCite DOIs
        return f"/works/doi:10.48550/arXiv.{quote(arxiv, safe='/.')}"
    return ""


def _details(ids: list[str]) -> list[dict]:
    out = []
    for i in range(0, len(ids), 50):  # the OR filter takes up to 50 ids
        chunk = "|".join(_short(x) for x in ids[i:i + 50])
        data = _get("/works", {"filter": f"openalex:{chunk}", "per-page": 50, "select": SELECT}) or {}
        out += [_paper(w) for w in data.get("results") or [] if w.get("display_name")]
    return out


def discover(doi: str = "", arxiv: str = "", openalex: str = "") -> dict:
    """{"references", "citations", "recommended"} for a paper, most-cited first. Raises OpenAlexError."""
    path = _work_path(doi, arxiv, openalex)
    if not path:
        raise OpenAlexError("OpenAlex needs a DOI or arXiv id for this paper")
    work = _get(path, {"select": "id,referenced_works,related_works"})
    if not work:
        return {"references": [], "citations": [], "recommended": []}
    wid = _short(work.get("id", ""))
    refs = _details((work.get("referenced_works") or [])[:100])
    related = _details((work.get("related_works") or [])[:30])
    cites = _get("/works", {"filter": f"cites:{wid}", "per-page": 100, "sort": "cited_by_count:desc",
                            "select": SELECT}) or {}
    res = {"references": refs, "citations": [_paper(w) for w in cites.get("results") or [] if w.get("display_name")],
           "recommended": related}
    for k in res:
        res[k].sort(key=lambda d: -(d.get("citationCount") or 0))
    return res

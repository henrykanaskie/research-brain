"""Paper metadata + citation graph from Semantic Scholar (free API), with arXiv and
Crossref fallbacks for metadata when Semantic Scholar is rate-limited or misses.

Set S2_API_KEY in the environment if you have a (free) Semantic Scholar key; without
one, requests share a public rate limit and may occasionally return 429 (we retry).
"""
from __future__ import annotations

import os
import re
import time
import xml.etree.ElementTree as ET
from urllib.parse import quote, unquote

import requests

from . import apikeys

S2 = "https://api.semanticscholar.org/graph/v1"
S2_REC = "https://api.semanticscholar.org/recommendations/v1"
FIELDS = "paperId,title,year,authors,abstract,externalIds,venue,url,citationCount,openAccessPdf"
LIST_FIELDS = "paperId,title,year,authors,externalIds,citationCount,url,isOpenAccess,openAccessPdf"
UA = {"User-Agent": "research_brain/1.0 (local personal knowledge graph)"}

DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
ARXIV_NEW_RE = re.compile(r"(?<![\d.])(\d{4}\.\d{4,5})(v\d+)?(?![\d])")
ARXIV_OLD_RE = re.compile(r"\b([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(v\d+)?\b")
ARXIV_URL_RE = re.compile(r"arxiv\.org/(?:abs|pdf|html)/([^\s?#]+?)(?:v\d+)?(?:\.pdf)?(?:[?#]|$)", re.I)


class ScholarError(RuntimeError):
    pass


class RateLimited(ScholarError):
    """HTTP 429 (or 5xx) after retries: the shared keyless pool is busy."""


def clean_doi(doi: str) -> str:
    doi = unquote(doi).strip().rstrip(".,;)]}'\"")
    doi = re.sub(r"(?i)^(https?://)?(dx\.)?doi\.org/", "", doi)
    doi = re.sub(r"(?i)^doi:\s*", "", doi)
    for suffix in (".pdf", "/full", "/abstract", "/epdf"):
        if doi.lower().endswith(suffix):
            doi = doi[: -len(suffix)]
    return doi


def parse_identifier(text: str) -> tuple[str, str]:
    """Return (kind, value) where kind in {'arxiv','doi','title'}."""
    t = (text or "").strip()
    m = ARXIV_URL_RE.search(t)
    if m:
        return "arxiv", re.sub(r"v\d+$", "", m.group(1))
    if re.match(r"(?i)^arxiv:\s*", t):
        t2 = re.sub(r"(?i)^arxiv:\s*", "", t)
        return "arxiv", re.sub(r"v\d+$", "", t2.split()[0])
    m = DOI_RE.search(t)
    if m:
        return "doi", clean_doi(m.group(1))
    m = ARXIV_NEW_RE.fullmatch(t) or (ARXIV_NEW_RE.search(t) if len(t) < 40 else None)
    if m:
        return "arxiv", m.group(1)
    m = ARXIV_OLD_RE.fullmatch(t)
    if m:
        return "arxiv", m.group(1)
    return "title", t


def find_ids_in_text(text: str) -> dict:
    """Look for a DOI / arXiv id in the first pages of a PDF's text."""
    head = text[:15000]
    out = {"doi": "", "arxiv": ""}
    m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})(v\d+)?", head, re.I) or re.search(
        r"arXiv:\s*([a-z\-]+(?:\.[A-Z]{2})?/\d{7})", head, re.I)
    if m:
        out["arxiv"] = m.group(1)
    m = DOI_RE.search(head)
    if m:
        out["doi"] = clean_doi(m.group(1))
    return out


def _get(url: str, params: dict | None = None, timeout: int = 20) -> requests.Response:
    headers = dict(UA)
    key = apikeys.get("s2") if "semanticscholar" in url else ""
    if key:
        headers["x-api-key"] = key
    last = None
    for attempt in range(3):
        try:
            r = requests.get(url, params=params, headers=headers, timeout=timeout)
        except (requests.ConnectionError, requests.Timeout) as e:
            last = e
            break  # offline / unreachable: fail fast, callers fall back
        except requests.RequestException as e:
            last = e
            break
        if r.status_code == 429 or r.status_code >= 500:
            last = ScholarError(f"HTTP {r.status_code}")
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))  # 1s, 2s
            continue
        return r
    if isinstance(last, requests.RequestException):
        raise ScholarError(f"Couldn't reach {url.split('/')[2]} ({last.__class__.__name__}). Check your internet connection.")
    raise RateLimited("Semantic Scholar is rate-limiting requests right now. Wait a minute and try again, or "
                      "add a free Semantic Scholar key in Settings → Paper lookup.")


def _paper_from_s2(p: dict) -> dict:
    ext = p.get("externalIds") or {}
    return {
        "s2_id": p.get("paperId") or "",
        "title": (p.get("title") or "").strip(),
        "year": p.get("year"),
        "authors": [a.get("name", "") for a in (p.get("authors") or []) if a.get("name")],
        "abstract": (p.get("abstract") or "").strip(),
        "venue": p.get("venue") or "",
        "url": p.get("url") or "",
        "doi": ext.get("DOI") or "",
        "arxiv": ext.get("ArXiv") or "",
        "citationCount": p.get("citationCount") or 0,
        # a legal open-access PDF link when Semantic Scholar knows one (used by app/acquire.py, not stored)
        "oa_pdf": ((p.get("openAccessPdf") or {}).get("url") or "") if isinstance(p.get("openAccessPdf"), dict) else "",
    }


def s2_paper(paper_id: str) -> dict | None:
    r = _get(f"{S2}/paper/{quote(paper_id, safe=':/.')}", {"fields": FIELDS})
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise ScholarError(f"Semantic Scholar error {r.status_code}: {r.text[:200]}")
    return _paper_from_s2(r.json())


def s2_title_search(title: str) -> dict | None:
    r = _get(f"{S2}/paper/search/match", {"query": title[:300], "fields": FIELDS})
    if r.status_code == 200:
        data = (r.json() or {}).get("data") or []
        if data:
            return _paper_from_s2(data[0])
    r = _get(f"{S2}/paper/search", {"query": title[:300], "fields": FIELDS, "limit": 1})
    if r.status_code == 200:
        data = (r.json() or {}).get("data") or []
        if data:
            return _paper_from_s2(data[0])
    return None


def arxiv_meta(arxiv_id: str) -> dict | None:
    r = _get("https://export.arxiv.org/api/query", {"id_list": arxiv_id})
    if r.status_code != 200:
        return None
    ns = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}
    try:
        root = ET.fromstring(r.content)
    except ET.ParseError:
        return None
    e = root.find("a:entry", ns)
    if e is None or e.find("a:title", ns) is None:
        return None
    title = " ".join((e.findtext("a:title", "", ns) or "").split())
    if not title or title.lower() == "error":
        return None
    pub = e.findtext("a:published", "", ns) or ""
    doi = e.findtext("arxiv:doi", "", ns) or ""
    return {
        "s2_id": "", "title": title, "year": int(pub[:4]) if pub[:4].isdigit() else None,
        "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)],
        "abstract": " ".join((e.findtext("a:summary", "", ns) or "").split()),
        "venue": "arXiv", "url": f"https://arxiv.org/abs/{arxiv_id}", "doi": doi, "arxiv": arxiv_id,
        "citationCount": 0,
    }


def crossref_meta(doi: str) -> dict | None:
    r = _get(f"https://api.crossref.org/works/{quote(doi, safe='/')}")
    if r.status_code != 200:
        return None
    m = (r.json() or {}).get("message") or {}
    title = " ".join((m.get("title") or [""])[0].split())
    if not title:
        return None
    year = None
    for k in ("published-print", "published-online", "issued", "created"):
        parts = ((m.get(k) or {}).get("date-parts") or [[None]])[0]
        if parts and parts[0]:
            year = parts[0]
            break
    abstract = re.sub(r"<[^>]+>", " ", m.get("abstract") or "")
    return {
        "s2_id": "", "title": title, "year": year,
        "authors": [" ".join(x for x in (a.get("given"), a.get("family")) if x) for a in (m.get("author") or [])],
        "abstract": " ".join(abstract.split()), "venue": (m.get("container-title") or [""])[0],
        "url": f"https://doi.org/{doi}", "doi": doi, "arxiv": "", "citationCount": m.get("is-referenced-by-count") or 0,
    }


def lookup(query: str) -> dict | None:
    """Resolve a DOI / arXiv id / URL / title to paper metadata. None if nothing found."""
    kind, value = parse_identifier(query)
    if not value:
        return None
    s2_err = None
    try:
        if kind == "arxiv":
            p = s2_paper(f"ARXIV:{value}")
        elif kind == "doi":
            p = s2_paper(f"DOI:{value}")
        else:
            p = s2_title_search(value)
        if p and p["title"]:
            if kind == "arxiv" and not p["arxiv"]:
                p["arxiv"] = value
            if kind == "doi" and not p["doi"]:
                p["doi"] = value
            return p
    except ScholarError as e:
        s2_err = e
    fallback = None
    if kind == "arxiv":
        fallback = arxiv_meta(value)
    elif kind == "doi":
        fallback = crossref_meta(value)
    if fallback:
        return fallback
    if s2_err:
        raise s2_err
    return None


def _candidates(items: list[dict], key: str) -> list[dict]:
    out = []
    for it in items:
        p = it.get(key) if key else it
        if not p or not p.get("paperId") or not p.get("title"):
            continue
        d = _paper_from_s2(p)
        d.pop("abstract", None)
        d.pop("venue", None)
        # free to read? (Semantic Scholar knows a legal open copy, or it's on arXiv); None = unknown
        d["access"] = "open" if (d.get("oa_pdf") or d.get("arxiv") or p.get("isOpenAccess")) else \
            ("closed" if p.get("isOpenAccess") is False else "")
        out.append(d)
    return out


def discover(paper_id: str) -> dict:
    """References, citing papers, and recommendations for an S2 paper id (or DOI:/ARXIV: id)."""
    res: dict[str, list] = {"references": [], "citations": [], "recommended": []}
    errors = []
    for kind, key in (("references", "citedPaper"), ("citations", "citingPaper")):
        try:
            r = _get(f"{S2}/paper/{quote(paper_id, safe=':/.')}/{kind}", {"fields": LIST_FIELDS, "limit": 100})
            if r.status_code == 200:
                res[kind] = _candidates((r.json() or {}).get("data") or [], key)
            elif r.status_code != 404:
                errors.append(f"{kind}: HTTP {r.status_code}")
        except ScholarError as e:
            errors.append(str(e))
    try:
        r = _get(f"{S2_REC}/papers/forpaper/{quote(paper_id, safe=':/.')}", {"fields": LIST_FIELDS, "limit": 30})
        if r.status_code == 200:
            res["recommended"] = _candidates((r.json() or {}).get("recommendedPapers") or [], "")
    except ScholarError as e:
        errors.append(str(e))
    for k in res:
        res[k].sort(key=lambda d: -(d.get("citationCount") or 0))
    if errors and not any(res.values()):
        raise ScholarError(errors[0])
    return res


REF_FIELDS = "paperId,title,externalIds"


def references(paper_id: str, citing: bool = False, max_items: int = 1000) -> list[dict]:
    """Papers this one cites (or, with citing=True, papers citing it): [{"s2_id","title","doi","arxiv"}].
    One request per 1000 items. 404 -> []. Rate limiting raises ScholarError (after _get's retries)."""
    kind, key = ("citations", "citingPaper") if citing else ("references", "citedPaper")
    out: list[dict] = []
    offset = 0
    while offset < max_items:
        r = _get(f"{S2}/paper/{quote(paper_id, safe=':/.')}/{kind}",
                 {"fields": REF_FIELDS, "limit": min(1000, max_items - offset), "offset": offset})
        if r.status_code == 404:
            return out
        if r.status_code == 429:
            raise RateLimited("Semantic Scholar is rate-limiting requests right now (HTTP 429)")
        if r.status_code != 200:
            raise ScholarError(f"Semantic Scholar error {r.status_code}: {r.text[:200]}")
        data = r.json() or {}
        for it in data.get("data") or []:
            p = it.get(key) or {}
            ext = p.get("externalIds") or {}
            if p.get("paperId") or p.get("title"):
                out.append({"s2_id": p.get("paperId") or "", "title": (p.get("title") or "").strip(),
                            "doi": ext.get("DOI") or "", "arxiv": ext.get("ArXiv") or ""})
        nxt = data.get("next")
        if not nxt or not data.get("data"):
            break
        offset = int(nxt)
    return out


REC_FIELDS = "paperId,title,year,authors,abstract,externalIds,venue,url,citationCount"


def recommend_for_papers(positive: list[str], negative: list[str] | None = None, limit: int = 60) -> list[dict]:
    """Semantic Scholar multi-paper recommendations:
    POST /recommendations/v1/papers?fields=...&limit=N  body {"positivePaperIds": [...], "negativePaperIds": [...]}
    -> {"recommendedPapers": [paper, ...]}. Returns papers in S2's order, shaped like s2_paper().
    Ids may be S2 paper ids or DOI:/ARXIV: prefixed. 404 (none of the inputs found) -> []."""
    positive = [p for p in dict.fromkeys(positive or []) if p][:100]
    if not positive:
        return []
    headers = dict(UA, **({"x-api-key": os.environ["S2_API_KEY"]} if os.environ.get("S2_API_KEY") else {}))
    body = {"positivePaperIds": positive, "negativePaperIds": [n for n in dict.fromkeys(negative or []) if n][:100]}
    r = None
    for attempt in range(3):
        try:
            r = requests.post(f"{S2_REC}/papers", params={"fields": REC_FIELDS, "limit": max(1, min(limit, 500))},
                              json=body, headers=headers, timeout=25)
        except requests.RequestException as e:
            raise ScholarError(f"Couldn't reach api.semanticscholar.org ({e.__class__.__name__}). "
                               "Check your internet connection.")
        if r.status_code == 429 or r.status_code >= 500:
            if attempt < 2:
                time.sleep(1.0 * (2 ** attempt))
            continue
        break
    if r is None or r.status_code == 429 or r.status_code >= 500:
        raise ScholarError("Semantic Scholar is rate-limiting requests right now — wait a minute and try again.")
    if r.status_code == 404:
        return []
    if r.status_code != 200:
        raise ScholarError(f"Semantic Scholar error {r.status_code}: {r.text[:200]}")
    try:
        data = r.json() or {}
    except ValueError:
        raise ScholarError("Semantic Scholar sent an unreadable response")
    out = []
    for p in data.get("recommendedPapers") or []:
        if isinstance(p, dict) and p.get("paperId") and p.get("title"):
            out.append(_paper_from_s2(p))
    return out

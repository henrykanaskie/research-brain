"""A publisher's link to a paper, often behind a paywall, turned into the paper itself (its DOI, arXiv id or
Semantic Scholar id), so it can be added and a free copy looked for.

Most publisher pages block apps (ScienceDirect, AIP, Oxford, Taylor & Francis answer 403), so the link is read
first, without opening it:

    a DOI or arXiv id in the link             doi.org/..., journals.aps.org/.../10.1103/..., arxiv.org/abs/...
    an Elsevier PII                           sciencedirect.com/science/article/pii/S0010465500002319
                                              -> Crossref (the PII is the DOI's "alternative id")
    JSTOR, Nature                             jstor.org/stable/N -> 10.2307/N, nature.com/articles/X -> 10.1038/X
    volume / issue / page in the path         pubs.aip.org/aip/pop/article/27/3/033501/..., academic.oup.com/...
                                              -> OpenAlex, kept only with the publisher's own DOI prefix
    Semantic Scholar                          semanticscholar.org/paper/.../<40 hex> -> its id
then the page itself, when the site lets it be read (its citation_doi, dc.identifier, citation_title tags), then
the title the link spells out (ResearchGate, Cambridge, AIP slugs) searched on Crossref, kept only when it
matches closely. Nothing is guessed: a DOI made from the link is checked on Crossref before it's used.

Used by "Add by DOI / arXiv / URL / title", a link dropped on the window that isn't a PDF, and the papers a
web-search answer links to (app/finder.py from_answer). Tests replace _crossref, _openalex and _page.
"""
from __future__ import annotations

import html
import re
from urllib.parse import unquote, urlencode, urlsplit

from . import metadata, urlfetch

URL_RE = re.compile(r"https?://[^\s<>\"'()\[\]{}|\\^`]+", re.I)

# hosts whose links are papers (the web answers' links to these are looked up; any link typed in is tried)
PAPER_HOSTS = (
    "sciencedirect.com", "linkinghub.elsevier.com", "cell.com", "thelancet.com", "ieeexplore.ieee.org",
    "pubs.aip.org", "aip.scitation.org", "academic.oup.com", "jstor.org", "nature.com", "link.springer.com",
    "springer.com", "onlinelibrary.wiley.com", "tandfonline.com", "iopscience.iop.org", "journals.aps.org",
    "link.aps.org", "arc.aiaa.org", "pubs.acs.org", "science.org", "cambridge.org", "researchgate.net",
    "semanticscholar.org", "journals.sagepub.com", "dl.acm.org", "pnas.org", "mdpi.com", "frontiersin.org",
    "osti.gov", "ntrs.nasa.gov", "pubmed.ncbi.nlm.nih.gov", "ncbi.nlm.nih.gov", "aanda.org", "iop.org",
    "journals.ametsoc.org", "agupubs.onlinelibrary.wiley.com", "epj.org", "worldscientific.com",
    "degruyter.com", "emerald.com", "inderscience.com", "spiedigitallibrary.org", "opg.optica.org",
    "optica.org", "royalsocietypublishing.org", "annualreviews.org", "jstage.jst.go.jp", "hindawi.com",
)
# the DOI prefixes a host's papers carry (to pick the right one of several papers with the same volume and page)
PREFIXES = {"pubs.aip.org": ("10.1063", "10.1121", "10.1116", "10.1122"), "aip.scitation.org": ("10.1063",),
            "academic.oup.com": ("10.1093",)}
MIN_TITLE_WORDS = 4


def host(url: str) -> str:
    try:
        return (urlsplit(url).hostname or "").lower()
    except ValueError:
        return ""


def is_paper_link(url: str) -> bool:
    h = host(url)
    return any(h == d or h.endswith("." + d) for d in PAPER_HOSTS)


def links_in(text: str) -> list[str]:
    """The links to papers on publishers' sites in a text, in order, without repeats."""
    out = []
    for m in URL_RE.finditer(text or ""):
        u = m.group(0).rstrip(".,;:!?*_")
        if is_paper_link(u) and u not in out:
            out.append(u)
    return out


def _clean(url: str) -> str:
    return unquote(html.unescape(str(url or "").strip()))


# ---------------------------------------------------------------------------------------------- the link alone
def read(url: str) -> dict:
    """What the link itself says, without asking anyone: doi / arxiv / pmid / ... (sure), and the clues
    pii, doi_guess, ieee, s2, volume+issue+page, title (to be checked)."""
    u = _clean(url)
    out: dict = dict(metadata.ids_from_url(u))
    h = host(u)
    path = urlsplit(u).path if h else u
    m = re.search(r"/(?:pii|retrieve/pii|fulltext|abstract)/(S?[0-9X][0-9X()\-]{12,24})", path, re.I)
    if m and ("elsevier" in h or "sciencedirect" in h or "cell.com" in h or "thelancet" in h):
        out["pii"] = re.sub(r"[^0-9A-Za-z]", "", m.group(1)).upper()
    if h.endswith("jstor.org"):
        m = re.search(r"/stable/(?:pdf/)?(\d{4,12})", path)
        if m:
            out["doi_guess"] = f"10.2307/{m.group(1)}"
    if h.endswith("nature.com") and "doi" not in out:
        m = re.search(r"/articles/([a-z][a-z0-9.\-]{4,40})", path, re.I)
        if m:
            out["doi_guess"] = "10.1038/" + m.group(1).lower().removesuffix(".pdf")
    if h.endswith("ieeexplore.ieee.org"):
        m = re.search(r"/(?:document|abstract/document|stamp/stamp\.jsp)/?(\d{5,10})", path + "?" + urlsplit(u).query)
        m = m or re.search(r"arnumber=(\d{5,10})", u)
        if m:
            out["ieee"] = m.group(1)
    if h.endswith("semanticscholar.org"):
        m = re.search(r"/paper/(?:[^/]+/)?([0-9a-f]{40})\b", path)
        if m:
            out["s2"] = m.group(1)
    m = re.search(r"/article/(\d{1,5})/(\d{1,4}|[A-Za-z0-9\-]{1,6})/([A-Za-z]?\d{1,8})/\d{3,12}(?:/([^/?#]+))?", path)
    if m and h in PREFIXES:
        out["volume"], out["issue"], out["page"] = m.group(1), m.group(2), m.group(3)
        out["journal"] = (path.split("/article/")[0].rstrip("/").rsplit("/", 1)[-1] or "").lower()
        if m.group(4):
            out["title"] = _slug_title(m.group(4))
    if "title" not in out:
        t = ""
        if h.endswith("researchgate.net"):
            m = re.search(r"/publication/\d+_([^/?#]+)", path)
            t = _slug_title(m.group(1)) if m else ""
        elif h.endswith("cambridge.org"):
            m = re.search(r"/article/(?:abs/)?([^/?#]+)/[0-9A-F]{20,40}\b", path)
            t = _slug_title(m.group(1)) if m else ""
        elif h.endswith("semanticscholar.org"):
            m = re.search(r"/paper/([^/]+)/[0-9a-f]{40}", path)
            t = _slug_title(m.group(1)) if m else ""
        if t:
            out["title"] = t
    return {k: v for k, v in out.items() if v}


def _slug_title(slug: str) -> str:
    words = [w for w in re.split(r"[-_+\s]+", unquote(slug)) if w]
    if len(words) < MIN_TITLE_WORDS or sum(w.isalpha() for w in words) < MIN_TITLE_WORDS - 1:
        return ""
    return " ".join(words)


# ---------------------------------------------------------------------------------------------- asking
_STORE: list = [None]
_PLAIN: list = []


def use(store) -> None:
    """Look things up with this library's resolver (its cache and rate limits)."""
    _STORE[0] = store


def _resolver():
    if _STORE[0] is not None:
        return metadata.resolver_for(_STORE[0])
    if not _PLAIN:
        _PLAIN.append(metadata.Resolver())
    return _PLAIN[0]


def _crossref(params: dict) -> list[dict]:
    st, data = _resolver().http(f"{metadata.CROSSREF.rstrip('/')}?{urlencode(params)}")
    return ((data or {}).get("message") or {}).get("items") or [] if st == 200 and isinstance(data, dict) else []


def _crossref_has(doi: str) -> bool:
    try:
        return bool(_resolver().crossref(doi))
    except Exception:
        return False


def _openalex(params: dict) -> list[dict]:
    from . import openalex
    try:
        return (openalex._get("/works", params) or {}).get("results") or []
    except openalex.OpenAlexError:
        return []


PAGE_TIMEOUT = 8.0


def _page(url: str) -> str:
    """The page's HTML, when the site lets an app read it ("" otherwise). Only that site (and its www. twin)."""
    h = host(url)
    if not h or urlsplit(url).scheme != "https":
        return ""
    allowed = {h, h.removeprefix("www."), "www." + h.removeprefix("www.")}
    try:
        r = urlfetch.fetch(url, allowed_hosts=allowed, accept="text/html,application/xhtml+xml",
                           headers={"User-Agent": "Mozilla/5.0 (research brain; reads a paper's citation tags)"},
                           max_bytes=2 << 20, total_timeout=PAGE_TIMEOUT)
    except urlfetch.FetchError:
        return ""
    return r.body.decode("utf-8", "replace") if r.status == 200 else ""


def from_page(text: str) -> dict:
    """The paper a page describes, from its citation tags (citation_doi, dc.identifier, ...)."""
    if not text:
        return {}
    metas = {}
    for m in re.finditer(r"<meta\b[^>]*>", text[:400000], re.I):
        tag = m.group(0)
        name = re.search(r"\b(?:name|property)\s*=\s*[\"']([^\"']+)[\"']", tag, re.I)
        val = re.search(r"\bcontent\s*=\s*[\"']([^\"']*)[\"']", tag, re.I)
        if name and val:
            metas.setdefault(name.group(1).lower(), html.unescape(val.group(1)).strip())
    out = {}
    for k in ("citation_doi", "dc.identifier", "prism.doi", "bepress_citation_doi", "dc.identifier.doi"):
        d = metadata.clean_doi(re.sub(r"(?i)^(doi:|info:doi/)", "", metas.get(k, "")))
        if d:
            out["doi"] = d
            break
    if "doi" not in out:  # IEEE Xplore and others keep it in the page's JSON
        m = re.search(r"\"doi\"\s*:\s*\"(10\.\d{4,9}/[^\"]+)\"", text)
        if m and metadata.clean_doi(m.group(1)):
            out["doi"] = metadata.clean_doi(m.group(1))
    a = metadata.clean_arxiv(metas.get("citation_arxiv_id", ""))
    if a:
        out["arxiv"] = a
    t = metas.get("citation_title") or metas.get("dc.title") or ""
    if t:
        out["title"] = " ".join(t.split())
    return out


def _by_title(title: str) -> str:
    try:
        rec = _resolver().crossref_search(title)
    except Exception:
        return ""
    return metadata.clean_doi((rec or {}).get("DOI") or "")


def _by_volume(clue: dict, h: str) -> str:
    f = f"biblio.volume:{clue['volume']},biblio.first_page:{clue['page']}"
    if clue.get("issue", "").isdigit():
        f += f",biblio.issue:{clue['issue']}"
    rows = _openalex({"filter": f, "select": "doi", "per-page": 200})
    dois = [metadata.clean_doi(r.get("doi") or "") for r in rows]
    dois = [d for d in dois if d and d.startswith(PREFIXES.get(h, ("",)))]
    j = clue.get("journal") or ""
    dois.sort(key=lambda d: 0 if j and j in d else 1)  # 10.1093/mnras/... for academic.oup.com/mnras/...
    return dois[0] if len(dois) == 1 or (dois and j and j in dois[0]) else ""


def enabled() -> bool:
    return metadata.enabled()


def find(url: str, *, fetch_page: bool = True, online: bool | None = None) -> dict:
    """The paper behind a link: {"doi"} or {"arxiv"} or {"s2"} (plus "how": what found it), or {}.
    Offline (online lookups turned off) only what the link itself says."""
    u = _clean(url)
    clue = read(u)
    for k in ("doi", "arxiv", "pmid", "pmcid", "osti", "ntrs"):
        if clue.get(k):
            return {k: clue[k], "how": "the link"}
    if clue.get("s2"):
        return {"s2": clue["s2"], "how": "the link"}
    if not (enabled() if online is None else online):
        return {}
    if clue.get("doi_guess") and _crossref_has(clue["doi_guess"]):
        return {"doi": clue["doi_guess"], "how": "the link"}
    if clue.get("pii"):
        for it in _crossref({"filter": f"alternative-id:{clue['pii']}", "rows": 2, "select": "DOI"})[:1]:
            d = metadata.clean_doi(it.get("DOI") or "")
            if d:
                return {"doi": d, "how": "Crossref"}
    if clue.get("volume"):
        d = _by_volume(clue, host(u))
        if d:
            return {"doi": d, "how": "OpenAlex"}
    page = from_page(_page(u)) if fetch_page and u.lower().startswith("https://") else {}
    for k in ("doi", "arxiv"):
        if page.get(k):
            return {k: page[k], "how": "the page"}
    for t in (page.get("title"), clue.get("title")):
        if t:
            d = _by_title(t)
            if d:
                return {"doi": d, "how": "its title"}
    return {}


def as_query(found: dict) -> str:
    """What "Add by DOI / arXiv / title" takes for it."""
    if found.get("doi"):
        return found["doi"]
    if found.get("arxiv"):
        return "arXiv:" + found["arxiv"]
    return ""


def blocked_message(url: str) -> str:
    h = host(url)
    if h.endswith("ieeexplore.ieee.org"):
        return ("IEEE Xplore doesn't let apps read its pages. Add it by its DOI (on the page, under the title), or "
                "drop in the PDF.")
    return ("Couldn't tell which paper that link is: the site doesn't let apps read it. Add it by its DOI or title, "
            "or drop in the PDF.")

"""A free, legal copy of a paywalled paper, and a note on what that copy is.

For a paper Find papers proposes (app/finder.py) that isn't free to read, the app looks for another copy in
the open-access indexes, best version first:

    OpenAlex locations     every open location of the work: repositories, institutional archives, PubMed
                           Central, preprint servers (with the version each holds)
    Unpaywall              by DOI, when you set a contact email in Settings (Unpaywall requires one)
    Semantic Scholar       the paper's openAccessPdf link
    arXiv                  a preprint whose title matches the paper's exactly (after normalising)

Only these aggregators of legal open-access copies are used: no shadow libraries, and never your
credentials, cookies or institutional access. The result is a note for you, because a free copy is often not
the published version: an accepted manuscript has the reviewed content but not the publisher's page numbers;
a preprint may predate peer review. When you add the paper, the note goes into its body and the app tries to
download that copy as its PDF (app/finder_api.py).

Tests replace SOURCES.
"""
from __future__ import annotations


import requests

from . import openalex, scout

VERSIONS = {"publishedVersion": "published", "acceptedVersion": "accepted", "submittedVersion": "preprint"}
RANK = {"published": 0, "accepted": 1, "unknown": 2, "preprint": 3}
TIMEOUT = 15


from .metadata import norm_title as _norm  # noqa: E402  (one rule for matching titles)


def from_openalex(p: dict, email: str = "") -> list[dict]:
    path = openalex._work_path(p.get("doi") or "", "")
    if not path:
        return []
    w = openalex._get(path, {"select": "locations"}) or {}
    out = []
    for loc in w.get("locations") or []:
        if not loc.get("is_oa") or not loc.get("pdf_url"):
            continue
        src = (loc.get("source") or {}).get("display_name") or ""
        out.append({"url": loc["pdf_url"], "page": loc.get("landing_page_url") or "", "host": src,
                    "version": VERSIONS.get(loc.get("version") or "", "unknown"), "via": "OpenAlex"})
    return out


def from_unpaywall(p: dict, email: str = "") -> list[dict]:
    doi = p.get("doi") or ""
    if not (doi and email):
        return []
    from . import acquire
    rec = acquire._unpaywall(doi, email) or {}
    out = []
    for loc in rec.get("oa_locations") or []:
        if loc.get("url_for_pdf"):
            out.append({"url": loc["url_for_pdf"], "page": loc.get("url_for_landing_page") or "",
                        "host": loc.get("repository_institution") or loc.get("host_type") or "",
                        "version": VERSIONS.get(loc.get("version") or "", "unknown"), "via": "Unpaywall"})
    return out


def from_semantic_scholar(p: dict, email: str = "") -> list[dict]:
    from . import scholar
    doi = p.get("doi") or ""
    if not doi:
        return []
    s2 = scholar.s2_paper("DOI:" + doi) or {}
    out = []
    if s2.get("oa_pdf"):
        out.append({"url": s2["oa_pdf"], "page": "", "host": "", "version": "unknown", "via": "Semantic Scholar"})
    if s2.get("arxiv"):
        out.append(_arxiv(s2["arxiv"]))
    return out


def from_arxiv(p: dict, email: str = "") -> list[dict]:
    title = _norm(p.get("title") or "")
    if len(title.split()) < 4:  # a short title matches too much
        return []
    import datetime as dt
    words = " ".join([w for w in title.split() if len(w) > 3][:8])
    for hit in scout.search_arxiv(words, dt.date(1990, 1, 1)):
        if _norm(hit.get("title") or "") == title and hit.get("arxiv"):
            return [_arxiv(hit["arxiv"])]
    return []


def _arxiv(aid: str) -> dict:
    return {"url": f"https://arxiv.org/pdf/{aid}", "page": f"https://arxiv.org/abs/{aid}", "host": "arXiv",
            "version": "preprint", "via": "arXiv"}


SOURCES = [from_openalex, from_unpaywall, from_semantic_scholar, from_arxiv]


def find(p: dict, email: str = "", sources=None) -> dict | None:
    """The best free copy of paper p ({title, doi, arxiv, ...}) with a note, or None. Never raises."""
    if p.get("arxiv"):
        found = [_arxiv(p["arxiv"])]
    else:
        found = []
        for fn in (sources or SOURCES):
            try:
                found += [c for c in fn(p, email) if str(c.get("url") or "").startswith(("http://", "https://"))]
            except (openalex.OpenAlexError, requests.RequestException, ValueError, KeyError):
                continue
            except Exception as e:  # one source failing never stops the others
                print(f"[research_brain] free copy lookup ({getattr(fn, '__name__', fn)}) failed: {e!r}")
                continue
            if any(c["version"] == "published" for c in found):
                break  # nothing beats the published version
    if not found:
        return None
    best = sorted(found, key=lambda c: RANK.get(c["version"], 2))[0]
    return {**best, "note": note_for(best, p)}


def note_for(c: dict, p: dict) -> str:
    where = f" on {c['host']}" if c.get("host") else ""
    cite = f" (DOI {p['doi']})" if p.get("doi") else ""
    link = c.get("page") or c["url"]
    v = c.get("version")
    if v == "published":
        return (f"Free copy of the published version{where}: {link}. It should match the paywalled one, so it can "
                f"be read and cited as the published paper{cite}.")
    if v == "accepted":
        return (f"Free copy: the authors' accepted manuscript{where}: {link}. The content is the peer-reviewed "
                f"version, but the publisher's layout, page numbers and final copy-edits differ. Quote and cite "
                f"the published version{cite}, not this copy's page numbers.")
    if v == "preprint":
        return (f"Free copy: a preprint{where}: {link}. It may predate peer review and differ from the published "
                f"paper. Check results, figures and equations against the published version{cite} before you "
                f"rely on or cite them.")
    return (f"Free copy found{where}: {link}. Which version it is isn't known (published, accepted manuscript or "
            f"preprint): compare it with the published version{cite} before citing details or page numbers.")

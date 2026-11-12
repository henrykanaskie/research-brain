"""How far a question may reach, and the "scout" that searches published papers for it.

Every question has a reach, chosen by the user:

    private   stays on this computer. Evidence comes from your own library through the local search index
              (BM25 plus local embeddings over sections and paragraphs). No AI agent and no outside service
              ever sees the question. The default.
    library   the same local matching, and AI features may read the question (summarising the evidence,
              Ask/Research about it).
    scout     also searches published papers: about once a day while the app is open, the question's
              key words go to OpenAlex and arXiv (public paper indexes, no AI). New papers that match it
              closely, by the same local similarity as library evidence, arrive as evidence to triage, with
              "Add to library". The query leaves your computer, so a novel idea you haven't published
              should stay private or library.

Nothing here calls an AI agent: ranking is local, so a scouting question costs no AI usage. The reach is
the note's `reach` frontmatter property; private also sets the note's privacy flag (app/privacy.py), so a private
question is private everywhere (exports, Ask, Research).
"""
from __future__ import annotations

import datetime as dt
import re
from urllib.parse import quote

import requests

REACHES = ("private", "library", "scout", "agent")  # agent: Scout plus a weekly AI agent search (app/agent_scout.py)
SCOUTING = ("scout", "agent")
EVERY = dt.timedelta(hours=24)
LOOKBACK_DAYS = 3 * 365   # the first scout looks this far back; later ones only at what's new since
PER_SCAN = 5              # new papers per question per scout
FETCH = 25                # results asked of each source
TIMEOUT = 15
UA = {"User-Agent": "research_brain/1.0 (local research library; question scout)"}
# similarity floors on title + abstract, which is denser than a passage: a little stricter than library
THRESHOLDS = {"tfidf": 0.14, "dense": 0.45}
MIN_OVERLAP = 2           # words a paper must share with the question when ranking is TF-IDF


def reach(q: dict) -> str:
    """The question's reach. The privacy flag wins: a question marked private (here, or with the lock in
    its header) never scouts, whatever its `reach` property says."""
    try:
        from . import privacy
        if privacy.is_private(q):
            return "private"
    except Exception:
        return "private"
    r = str(q.get("reach") or (q.get("_extra") or {}).get("reach") or "").strip().lower()
    return r if r in SCOUTING else "library"


# ---------------------------------------------------------------- the query
_STOP = frozenset("""a an and are as at be by can could do does for from has have how i if in into is it its may
might of on or our should so than that the their there these this those to was we what when where which while
who why will with would you your about between does do any more most much many some such vs versus""".split())


def query_for(q: dict) -> str:
    """The words sent to the paper indexes: the question's title, then the most frequent content words of
    its body, at most ~16 words. Never the user's answer or notes."""
    title = re.sub(r"\$[^$]*\$", " ", q.get("title") or "")
    words = [w for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]+", title) if w.lower() not in _STOP]
    body = re.sub(r"\$[^$]*\$|\[\[[^\]]*\]\]|https?://\S+", " ", q.get("body") or "")
    counts: dict[str, int] = {}
    for w in re.findall(r"[A-Za-z][A-Za-z0-9\-]{3,}", body):
        lw = w.lower()
        if lw not in _STOP and lw not in {x.lower() for x in words}:
            counts[lw] = counts.get(lw, 0) + 1
    extra = [w for w, _ in sorted(counts.items(), key=lambda kv: -kv[1])][:max(0, 16 - len(words))]
    return " ".join(words + extra)[:240]


# ---------------------------------------------------------------- sources
def _abstract(inv: dict | None) -> str:
    """OpenAlex keeps abstracts as {word: [positions]}."""
    if not inv:
        return ""
    pos = sorted((p, w) for w, ps in inv.items() for p in ps)
    return " ".join(w for _, w in pos)


def search_openalex(query: str, since: dt.date) -> list[dict]:
    from . import apikeys
    params = {"search": query, "per-page": FETCH,
              "filter": f"from_publication_date:{since.isoformat()},type:article|preprint|review",
              "select": "id,display_name,publication_year,publication_date,authorships,doi,ids,"
                        "abstract_inverted_index,primary_location,open_access"}
    key = apikeys.get("openalex")
    if key:
        params["api_key"] = key
    r = requests.get("https://api.openalex.org/works", params=params, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    out = []
    for w in r.json().get("results") or []:
        doi = (w.get("doi") or "").replace("https://doi.org/", "")
        arxiv = doi.split("arxiv.", 1)[-1] if doi.lower().startswith("10.48550/arxiv.") else ""
        src = ((w.get("primary_location") or {}).get("source") or {}).get("display_name") or ""
        oa = w.get("open_access") or {}
        st = (oa.get("oa_status") or "").lower()
        out.append({"title": (w.get("display_name") or "").strip(), "abstract": _abstract(w.get("abstract_inverted_index")),
                    "year": w.get("publication_year"), "date": w.get("publication_date") or "",
                    "authors": [((a.get("author") or {}).get("display_name") or "") for a in (w.get("authorships") or [])][:6],
                    "doi": doi, "arxiv": arxiv, "venue": src, "url": f"https://doi.org/{doi}" if doi else (w.get("id") or ""),
                    "access": "open" if arxiv or st in ("gold", "green", "hybrid", "diamond") else ("free" if st == "bronze" else ("closed" if st == "closed" else "")),
                    "found_via": "OpenAlex"})
    return out


def search_arxiv(query: str, since: dt.date) -> list[dict]:
    from defusedxml import ElementTree as ET
    terms = " AND ".join(f"all:{quote(w)}" for w in query.split()[:8])
    url = ("https://export.arxiv.org/api/query?search_query=" + terms +
           f"&sortBy=relevance&max_results={FETCH}")
    r = requests.get(url, headers=UA, timeout=TIMEOUT)
    r.raise_for_status()
    ns = {"a": "http://www.w3.org/2005/Atom"}
    out = []
    for e in ET.fromstring(r.content).findall("a:entry", ns):
        published = (e.findtext("a:published", "", ns) or "")[:10]
        try:
            if published and dt.date.fromisoformat(published) < since:
                continue
        except ValueError:
            pass
        aid = (e.findtext("a:id", "", ns) or "").rsplit("/abs/", 1)[-1]
        aid = re.sub(r"v\d+$", "", aid)
        out.append({"title": re.sub(r"\s+", " ", e.findtext("a:title", "", ns) or "").strip(),
                    "abstract": re.sub(r"\s+", " ", e.findtext("a:summary", "", ns) or "").strip(),
                    "year": int(published[:4]) if published[:4].isdigit() else None, "date": published,
                    "authors": [a.findtext("a:name", "", ns) for a in e.findall("a:author", ns)][:6],
                    "doi": "", "arxiv": aid, "venue": "arXiv", "url": f"https://arxiv.org/abs/{aid}" if aid else "",
                    "access": "open", "found_via": "arXiv"})
    return out


SOURCES = {"openalex": search_openalex, "arxiv": search_arxiv}


# ---------------------------------------------------------------- matching
def _norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


def library_keys(notes: list[dict]) -> set[str]:
    keys = set()
    for n in notes:
        if n.get("type") != "paper":
            continue
        if n.get("doi"):
            keys.add("doi:" + n["doi"].lower())
        if n.get("arxiv"):
            keys.add("arxiv:" + re.sub(r"v\d+$", "", n["arxiv"].lower()))
        if n.get("title"):
            keys.add("t:" + _norm_title(n["title"]))
    return keys


def paper_keys(p: dict) -> list[str]:
    ks = []
    if p.get("doi"):
        ks.append("doi:" + p["doi"].lower())
    if p.get("arxiv"):
        ks.append("arxiv:" + p["arxiv"].lower())
    if p.get("title"):
        ks.append("t:" + _norm_title(p["title"]))
    return ks


def ev_key(p: dict) -> str:
    return "external:" + paper_keys(p)[0] if paper_keys(p) else ""


def similarity(question: str, texts: list[str], dense_index=None) -> list[float]:
    """Cosine similarity of the question to each paper's title + abstract, on this computer. With sentence
    embeddings, the library's model; otherwise TF-IDF fitted on just these papers and the question, so
    words the library has never seen still count (the library's own TF-IDF vocabulary would ignore them)."""
    if not texts:
        return []
    if dense_index is not None:
        return dense_index.similarity(question, texts)
    from .textvec import TfidfVectorizer
    try:
        m = TfidfVectorizer(stop_words="english", sublinear_tf=True, ngram_range=(1, 2), min_df=1).fit_transform([question] + texts)
    except ValueError:  # nothing but stop words
        return [0.0] * len(texts)
    sims = m[1:].dot_row(m[0:1])
    return [float(max(0.0, min(1.0, x))) for x in sims]


def due(data: dict, now: dt.datetime | None = None) -> bool:
    now = now or dt.datetime.now(dt.timezone.utc)
    last = (data.get("scout") or {}).get("last")
    if not last:
        return True
    try:
        return now - dt.datetime.fromisoformat(last) >= EVERY
    except (TypeError, ValueError):
        return True

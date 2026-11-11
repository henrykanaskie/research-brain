"""Paper metadata from identifiers: an authoritative CSL-JSON record for a note, identifiers first, scraping last.

    resolve(ids, url=, title=, page=, s2=)  ->  {"csl": {...}, "source": "crossref", "errors": [...]} | None
    refresh(store, nid, ...)                ->  resolve the note's identifiers and apply() the record
    schedule(store, nid, wait=3, ...)       ->  the same in the background (import paths); a live event when done

Sources, in order (the first that answers wins; later ones only fill gaps):

    DOI          Crossref REST  https://api.crossref.org/works/<doi>                   -> crossref
                 (not a Crossref DOI) DOI content negotiation on https://doi.org/<doi>
                 with Accept: application/vnd.citationstyles.csl+json (DataCite, ...)  -> datacite | crossref
    arXiv id     arXiv API (Atom)  https://export.arxiv.org/api/query?id_list=<id>      -> arxiv
                 (the arXiv record names a journal DOI) that DOI's Crossref record wins; the arXiv id is kept
    PMID/PMCID   NCBI E-utilities esummary (pubmed / pmc), then Crossref if it names a DOI -> pubmed
    ISBN         Open Library  https://openlibrary.org/api/books?bibkeys=ISBN:<isbn>    -> openlibrary
    OSTI id      https://www.osti.gov/api/v1/records/<id>                               -> osti
    NTRS id      https://ntrs.nasa.gov/api/citations/<id>                               -> ntrs
    page URL     Zotero translation-server on 127.0.0.1 (optional, only if it is running) -> translation-server
    title        Crossref bibliographic search, accepted only on a near-exact title match -> crossref
    fallback     Semantic Scholar (DOI / arXiv / title)                                  -> s2
    last         what the page or PDF said (the extension's citation_* tags)              -> page

Every request goes through app/urlfetch.fetch (https only, the hosts in ALLOWED_HOSTS only, public addresses,
pinned connections, size and time caps); only identifiers (a DOI, an arXiv id, a PMID, an ISBN, an OSTI or NTRS
id, a title for the title search, the page URL for a local translation-server) are sent. Requests carry a
User-Agent naming the app and, when you set a contact email (Library settings, the Unpaywall field), a
`mailto:` (Crossref's "polite pool"; NCBI's `email` parameter). Answers are cached in
.index/metadata_cache.json (RB_METADATA_CACHE_DAYS, default 30; "not found" for a day) and requests are spaced
per host (RateLimiter). RB_METADATA=0 turns all of it off.

The note keeps the record in `csl` (CSL-JSON; app/cite.py's csl_for() reads it), where it came from in
`meta_source` and when in `meta_checked`. The legacy fields (title, authors as "Given Family", year, venue,
doi, arxiv, url, abstract) are kept in step from the record (legacy_from_csl), so the rest of the app works
unchanged. Merge rules (apply()): a better source replaces a worse one (RANK); Zotero's own metadata is never
overwritten (gaps are filled); fields you edited in the app (tracked in csl.custom.rb_edited) are kept unless a
refresh is asked to replace them; on a paper that was created long ago without a record only empty fields
are filled unless you ask for a refresh.
"""
from __future__ import annotations

import datetime as dt
import difflib
import html
import json
import os
import re
import threading
import time
import xml.etree.ElementTree as ET
from html.parser import HTMLParser
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote, urlencode, urlsplit, unquote

from . import urlfetch

# ---------------------------------------------------------------------------------------------- settings
CACHE_DAYS = float(os.environ.get("RB_METADATA_CACHE_DAYS", "30"))
NEG_CACHE_HOURS = 24.0
WAIT_ON_IMPORT = float(os.environ.get("RB_METADATA_WAIT", "3"))
RECHECK_DAYS = 30.0  # a background pass skips notes resolved more recently than this (refresh always runs)

CROSSREF = "https://api.crossref.org/works/"
DOI_ORG = "https://doi.org/"
ARXIV_API = "https://export.arxiv.org/api/query"
EUTILS = "https://eutils.ncbi.nlm.nih.gov/entrez/eutils/esummary.fcgi"
OPENLIBRARY = "https://openlibrary.org/api/books"
OSTI_API = "https://www.osti.gov/api/v1/records/"
NTRS_API = "https://ntrs.nasa.gov/api/citations/"
S2_API = "https://api.semanticscholar.org/graph/v1/paper/"
S2_FIELDS = "paperId,title,year,authors,abstract,externalIds,venue,url,publicationVenue,journal,publicationDate"

# the only hosts a metadata lookup may contact (redirect targets included)
ALLOWED_HOSTS = frozenset({
    "api.crossref.org", "data.crossref.org", "doi.org", "api.datacite.org", "data.datacite.org",
    "export.arxiv.org", "eutils.ncbi.nlm.nih.gov", "openlibrary.org", "www.osti.gov", "ntrs.nasa.gov",
    "api.semanticscholar.org",
})

# seconds between two requests to one host (arXiv asks for 3 s; NCBI allows 3/s without a key)
INTERVALS = {"api.crossref.org": 0.25, "doi.org": 0.25, "export.arxiv.org": 3.0, "eutils.ncbi.nlm.nih.gov": 0.4,
             "openlibrary.org": 0.5, "www.osti.gov": 0.5, "ntrs.nasa.gov": 0.5, "api.semanticscholar.org": 1.1,
             "127.0.0.1": 0.0}

# how much each source is trusted; a record only replaces one from a source ranked the same or lower
RANK = {"zotero": 100, "crossref": 80, "datacite": 80, "pubmed": 75, "openlibrary": 70, "osti": 70, "ntrs": 70,
        "translation-server": 60, "arxiv": 50, "s2": 40, "page": 20, "": 0}
SOURCES = tuple(RANK)
LEGACY = ("title", "authors", "year", "venue", "abstract", "url")


def enabled() -> bool:
    return os.environ.get("RB_METADATA", "1") != "0"


def contact_email() -> str:
    try:
        from . import paths
        e = str((paths.load_settings() or {}).get("unpaywall_email") or "").strip()
    except Exception:
        e = ""
    e = e or os.environ.get("RB_UNPAYWALL_EMAIL", "").strip()
    return e if re.fullmatch(r"[^@\s]{1,64}@[^@\s]{1,190}\.[A-Za-z]{2,}", e) else ""


def user_agent(email: str = "") -> str:
    from . import __version__
    return f"research_brain/{__version__} (local reference manager" + (f"; mailto:{email})" if email else ")")


def translation_server_port() -> int:
    """RB_TRANSLATION_SERVER_PORT (default 1969); 0 turns the translation-server lookup off."""
    try:
        p = int(os.environ.get("RB_TRANSLATION_SERVER_PORT", "1969"))
    except ValueError:
        return 0
    return p if 0 < p < 65536 else 0


class MetaError(RuntimeError):
    pass


# ---------------------------------------------------------------------------------------------- cache, rate limit
class Cache:
    """{key: {"at": unix time, "status": int, "data": ...}} in one JSON file (derived data: safe to delete)."""

    def __init__(self, path: Path | None, days: float = CACHE_DAYS, neg_hours: float = NEG_CACHE_HOURS,
                 max_entries: int = 5000, clock: Callable[[], float] = time.time):
        self.path, self.ttl, self.neg_ttl = (Path(path) if path else None), days * 86400, neg_hours * 3600
        self.max_entries, self.clock = max_entries, clock
        self.lock = threading.Lock()
        self._data: dict | None = None

    def _load(self) -> dict:
        if self._data is None:
            self._data = {}
            if self.path and self.path.exists():
                try:
                    d = json.loads(self.path.read_text(encoding="utf-8"))
                    self._data = d if isinstance(d, dict) else {}
                except (OSError, ValueError):
                    self._data = {}
        return self._data

    def get(self, key: str):
        """(True, (status, data)) for a fresh entry, else (False, None)."""
        with self.lock:
            rec = self._load().get(key)
            if not isinstance(rec, dict):
                return False, None
            ttl = self.ttl if rec.get("status") == 200 else self.neg_ttl
            if self.clock() - float(rec.get("at") or 0) > ttl:
                return False, None
            return True, (int(rec.get("status") or 0), rec.get("data"))

    def put(self, key: str, status: int, data) -> None:
        with self.lock:
            d = self._load()
            d[key] = {"at": self.clock(), "status": status, "data": data}
            if len(d) > self.max_entries:
                for k, _ in sorted(d.items(), key=lambda kv: float((kv[1] or {}).get("at") or 0))[:len(d) - self.max_entries]:
                    d.pop(k, None)
            if self.path:
                try:
                    self.path.parent.mkdir(parents=True, exist_ok=True)
                    tmp = self.path.with_suffix(".json.tmp")
                    tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8", newline="\n")
                    tmp.replace(self.path)
                except OSError:
                    pass


class RateLimiter:
    """At most one request per `interval` seconds to each host (callers sleep their turn)."""

    def __init__(self, intervals: dict | None = None, default: float = 0.5,
                 clock: Callable[[], float] = time.monotonic, sleep: Callable[[float], None] = time.sleep):
        self.intervals, self.default = dict(INTERVALS if intervals is None else intervals), default
        self.clock, self.sleep = clock, sleep
        self._next: dict[str, float] = {}
        self.lock = threading.Lock()

    def wait(self, host: str) -> float:
        with self.lock:
            now = self.clock()
            start = max(now, self._next.get(host, 0.0))
            self._next[host] = start + self.intervals.get(host, self.default)
        delay = start - now
        if delay > 0:
            self.sleep(delay)
        return delay


# ---------------------------------------------------------------------------------------------- text cleaning
_TEX_ENC = ("application/x-tex", "tex", "latex", "application/x-latex")


class _Markup(HTMLParser):
    """HTML / JATS / MathML -> plain text. A <math> becomes $TeX$ when it carries alttext or a TeX annotation
    (otherwise its visible text); <sub>/<sup>/<i>/<b>/<scp> are dropped to their text; paragraphs become
    blank lines (abstracts)."""

    BLOCKS = {"p", "div", "sec", "section", "br", "li", "title", "jats:p", "jats:sec", "jats:title", "jats:list-item"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.math = 0
        self.alt = ""
        self.ann: list[str] = []
        self.in_ann = False
        self.mtext: list[str] = []
        self.skip = 0

    @staticmethod
    def _local(tag: str) -> str:
        return tag.split(":", 1)[-1]

    def handle_starttag(self, tag, attrs):
        loc = self._local(tag)
        if loc in ("script", "style"):
            self.skip += 1
            return
        if loc == "math":
            if self.math == 0:
                self.alt = dict(attrs).get("alttext") or ""
                self.ann, self.mtext = [], []
            self.math += 1
            return
        if self.math and loc == "annotation":
            enc = (dict(attrs).get("encoding") or "").lower()
            self.in_ann = enc in _TEX_ENC
            return
        if loc in ("tex-math",):  # JATS <tex-math>
            self.in_ann = True
            self.math += 1
            self.alt, self.ann, self.mtext = "", [], []
            return
        if tag in self.BLOCKS or loc in ("p", "sec", "title", "list-item"):
            self.out.append("\n\n")

    def handle_endtag(self, tag):
        loc = self._local(tag)
        if loc in ("script", "style"):
            self.skip = max(0, self.skip - 1)
            return
        if loc == "annotation":
            self.in_ann = False
            return
        if loc in ("math", "tex-math") and self.math:
            self.math -= 1
            if self.math == 0:
                self.in_ann = False
                t = " ".join((self.alt or "".join(self.ann)).split())
                t = re.sub(r"^\$+|\$+$", "", t).strip()
                if t:
                    self.out.append(f"${t}$")
                else:
                    self.out.append("".join(self.mtext))
            return
        if loc in ("p", "sec", "title", "list-item"):
            self.out.append("\n\n")

    def handle_data(self, data):
        if self.skip:
            return
        if self.math:
            (self.ann if self.in_ann else self.mtext).append(data)
            return
        self.out.append(data)


def clean_markup(s: str, keep_paragraphs: bool = False) -> str:
    """Strip HTML/JATS/MathML from a title or abstract. TeX already in the text ($...$) is kept as is; case is
    never changed."""
    s = str(s or "")
    if "<" in s or "&" in s:
        p = _Markup()
        try:
            p.feed(s)
            p.close()
            s = "".join(p.out)
        except Exception:
            s = html.unescape(re.sub(r"<[^>]+>", " ", s))
    s = s.replace(" ", " ").replace(" ", " ")
    if keep_paragraphs:
        paras = [" ".join(x.split()) for x in re.split(r"\n\s*\n", s)]
        return "\n\n".join(x for x in paras if x)
    return " ".join(s.split())


def clean_abstract(s: str) -> str:
    t = clean_markup(s, keep_paragraphs=True)
    return re.sub(r"^(abstract|summary)\s*[:.\-—]?\s*", "", t, flags=re.I).strip()


# ---------------------------------------------------------------------------------------------- names
PARTICLES = {"van", "von", "de", "der", "den", "del", "della", "delle", "dei", "degli", "di", "da", "das", "dos",
             "do", "du", "la", "le", "les", "lo", "ten", "ter", "te", "zu", "zur", "af", "av", "y", "e", "d'", "l'",
             "st.", "op", "het", "'t", "al", "el", "bin", "ibn", "ben"}
SUFFIX_RE = re.compile(r"^(jr|sr|jnr|snr|ii|iii|iv|v|vi|2nd|3rd)\.?$", re.I)
ORG_RE = re.compile(r"\b(collaboration|team|group|consortium|organi[sz]ation|institute|institution|laborator(y|ies)|"
                    r"universit(y|ies)|agency|council|committee|association|society|cent(er|re)|department|"
                    r"administration|corporation|company|inc\.?|ltd\.?|llc|gmbh|program(me)?|project|office|"
                    r"division|board|foundation|nasa|iter|cern|esa|doe|ieee|aiaa|national|international)\b", re.I)


def _fix_initials(given: str) -> str:
    """'J.A.' -> 'J. A.' (spacing only; nothing is added or removed)."""
    return " ".join(re.sub(r"\b([A-Z])\.(?=[A-Z])", r"\1. ", given.strip()).split())


_SUFFIX_CANON = {"jr": "Jr.", "sr": "Sr.", "jnr": "Jr.", "snr": "Sr.", "ii": "II", "iii": "III", "iv": "IV",
                 "v": "V", "vi": "VI", "2nd": "2nd", "3rd": "3rd"}


def _suffix(s: str) -> str:
    return _SUFFIX_CANON.get(s.strip().rstrip(".").lower(), s.strip())


def _caps(s: str) -> str:
    """'SMITH' -> 'Smith', 'MCDONALD-JONES' -> 'Mcdonald-Jones' (only for names given all in capitals)."""
    return "-".join(w[:1] + w[1:].lower() for w in s.split("-")) if s.isupper() and len(s) > 1 else s


def _split_particle(family: str) -> tuple[str, str]:
    """'van der Waals' -> ('van der', 'Waals'); 'de la Cruz' -> ('de la', 'Cruz'); 'Bohm' -> ('', 'Bohm')."""
    toks = family.split()
    i = 0
    while i < len(toks) - 1 and toks[i].lower() in PARTICLES and toks[i][:1].islower():
        i += 1
    return " ".join(toks[:i]), " ".join(toks[i:])


def norm_name(a) -> dict | None:
    """A CSL name from a Crossref/CSL dict, a Zotero creator, or a string ("Last, First", "First van Last Jr.",
    "ITER Organization"). Corporate authors become {"literal": ...}."""
    if isinstance(a, dict):
        if a.get("literal") or a.get("name") and not (a.get("family") or a.get("given") or a.get("lastName")):
            lit = clean_markup(a.get("literal") or a.get("name"))
            if not a.get("literal") and "," in lit and not ORG_RE.search(lit):
                return norm_name(lit)
            return {"literal": lit} if lit else None
        family = clean_markup(a.get("family") or a.get("lastName") or "")
        given = clean_markup(a.get("given") or a.get("firstName") or "")
        suffix = clean_markup(a.get("suffix") or "")
        out: dict[str, str] = {}
        part = clean_markup(a.get("non-dropping-particle") or "")
        dpart = clean_markup(a.get("dropping-particle") or "")
        if not part:
            part, family = _split_particle(family)
        if not suffix and given:
            m = re.search(r",?\s+(jr|sr|ii|iii|iv)\.?$", given, re.I)
            if m:
                suffix, given = m.group(1), given[:m.start()]
        suffix = _suffix(suffix) if suffix else ""
        if not family and given:
            return norm_name(given)
        if family.isupper() and not given.isupper():
            family = _caps(family)
        elif family.isupper() and given.isupper():
            family, given = _caps(family), " ".join(_caps(w) for w in given.split())
        if not given and ORG_RE.search(family):
            return {"literal": family}
        out["family"] = family
        if given:
            out["given"] = _fix_initials(given)
        if part:
            out["non-dropping-particle"] = part
        if dpart:
            out["dropping-particle"] = dpart
        if suffix:
            out["suffix"] = suffix
        return out if family else None
    s = clean_markup(str(a or ""))
    s = re.sub(r"\s*\[[^\]]*\]\s*", " ", s).strip()  # OSTI: "Last, First [Affiliation]" / "[ORCID]"
    s = re.sub(r"\s*\(\s*ORCID[^)]*\)", "", s).strip()
    if not s:
        return None
    if ORG_RE.search(s) and "," not in s or len(s.split()) > 6:
        return {"literal": s}
    if "," in s:  # "Last, First" or "Last, First, Jr." or "First Last, Jr."
        parts = [p.strip() for p in s.split(",") if p.strip()]
        suffix = ""
        if len(parts) >= 2 and SUFFIX_RE.match(parts[-1]):
            suffix = parts.pop()
        if len(parts) == 1:
            n = norm_name(parts[0]) or {}
            if suffix and "family" in n:
                n["suffix"] = suffix
            return n or None
        return norm_name({"family": parts[0], "given": " ".join(parts[1:]), "suffix": suffix})
    toks = s.split()
    suffix = ""
    if len(toks) > 1 and SUFFIX_RE.match(toks[-1]):
        suffix = toks.pop()
    if len(toks) == 1:
        return {"family": _caps(toks[0]), **({"suffix": suffix} if suffix else {})}
    # PubMed style: "Smith JA" (family first, initials last)
    if len(toks) == 2 and re.fullmatch(r"[A-Z]{1,3}", toks[1]) and not re.fullmatch(r"[A-Z]{1,3}", toks[0]):
        return {"family": toks[0], "given": " ".join(c + "." for c in toks[1])}
    i = 1
    while i < len(toks) - 1 and not (toks[i].lower() in PARTICLES and toks[i][:1].islower()):
        i += 1
    if i < len(toks) - 1:  # a particle starts the family name
        given, family = " ".join(toks[:i]), " ".join(toks[i:])
    else:
        given, family = " ".join(toks[:-1]), toks[-1]
    return norm_name({"family": family, "given": given, "suffix": suffix})


def display_name(n: dict) -> str:
    """'Johannes D. van der Waals', 'Martin L. King Jr.', 'ITER Organization'."""
    if not isinstance(n, dict):
        return str(n or "")
    if n.get("literal"):
        return n["literal"]
    bits = [n.get("given", ""), n.get("dropping-particle", ""), n.get("non-dropping-particle", ""), n.get("family", "")]
    s = " ".join(b for b in bits if b)
    return f"{s} {n['suffix']}" if n.get("suffix") else s


def names(lst) -> list[dict]:
    out = []
    for a in lst or []:
        n = norm_name(a)
        if n:
            out.append(n)
    return out[:500]


# ---------------------------------------------------------------------------------------------- identifiers
DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s\"<>{}]+)", re.I)
ARXIV_DOI_RE = re.compile(r"^10\.48550/arxiv\.(.+)$", re.I)
ARXIV_ID_RE = re.compile(r"^(\d{4}\.\d{4,5}|[a-z\-]+(\.[A-Z]{2})?/\d{7})(v\d+)?$", re.I)


def clean_doi(d: str) -> str:
    d = unquote(str(d or "")).strip()
    d = re.sub(r"(?i)^(https?://)?(dx\.)?doi\.org/", "", d)
    d = re.sub(r"(?i)^doi:\s*", "", d).strip().rstrip(".,;)]}'\"")
    for suffix in (".pdf", "/full", "/abstract", "/epdf", "/pdf", "/meta"):
        if d.lower().endswith(suffix):
            d = d[: -len(suffix)]
    m = DOI_RE.match(d)
    return m.group(1).lower() if m else ""


def clean_arxiv(a: str) -> str:
    a = re.sub(r"(?i)^(arxiv:\s*|https?://(export\.)?arxiv\.org/(abs|pdf|html)/)", "", str(a or "").strip())
    a = re.sub(r"\.pdf$", "", a).strip("/ ")
    m = ARXIV_ID_RE.match(a)
    return re.sub(r"v\d+$", "", m.group(0)) if m else ""


def clean_isbn(s: str) -> str:
    d = re.sub(r"[^0-9Xx]", "", str(s or "")).upper()
    if len(d) == 13 and d.isdigit():
        return d if sum(int(c) * (1 if i % 2 == 0 else 3) for i, c in enumerate(d)) % 10 == 0 else ""
    if len(d) == 10 and d[:9].isdigit():
        tot = sum((10 - i) * (10 if c == "X" else int(c)) for i, c in enumerate(d))
        return d if tot % 11 == 0 else ""
    return ""


def ids_from_url(url: str) -> dict:
    """Identifiers a publisher / repository URL names: DOI paths, arXiv, PubMed, PMC, OSTI, NTRS."""
    out: dict[str, str] = {}
    u = unquote(str(url or ""))
    low = u.lower()
    m = re.search(r"arxiv\.org/(?:abs|pdf|html)/([^?#\s]+?)(?:v\d+)?(?:\.pdf)?(?:[?#]|/?$)", u, re.I)
    if m and clean_arxiv(m.group(1)):
        out["arxiv"] = clean_arxiv(m.group(1))
    m = re.search(r"osti\.gov/(?:pages/)?(?:biblio|servlets/purl)/(\d{3,10})", low)
    if m:
        out["osti"] = m.group(1)
    m = re.search(r"ntrs\.nasa\.gov/(?:api/)?citations/(\d{6,14})", low)
    if m:
        out["ntrs"] = m.group(1)
    m = re.search(r"pubmed\.ncbi\.nlm\.nih\.gov/(\d{1,9})", low) or re.search(r"ncbi\.nlm\.nih\.gov/pubmed/(\d{1,9})", low)
    if m:
        out["pmid"] = m.group(1)
    m = re.search(r"/pmc/articles/(pmc\d+)", low) or re.search(r"pmc\.ncbi\.nlm\.nih\.gov/articles/(pmc\d+)", low)
    if m:
        out["pmcid"] = m.group(1).upper()
    if "arxiv.org" not in low:
        m = DOI_RE.search(u)
        if m:
            d = clean_doi(re.split(r"[?#]", m.group(1))[0])
            if d:
                out["doi"] = d
    return out


def normalize_ids(ids: dict | None) -> dict:
    ids = dict(ids or {})
    out = {}
    d = clean_doi(ids.get("doi") or "")
    if d:
        m = ARXIV_DOI_RE.match(d)
        if m and not ids.get("arxiv"):
            ids["arxiv"] = m.group(1)
        if not m:
            out["doi"] = d
    a = clean_arxiv(ids.get("arxiv") or "")
    if a:
        out["arxiv"] = a
    p = re.sub(r"\D", "", str(ids.get("pmid") or ""))
    if p:
        out["pmid"] = p
    pc = str(ids.get("pmcid") or "").strip().upper()
    if re.fullmatch(r"(PMC)?\d{3,10}", pc):
        out["pmcid"] = pc if pc.startswith("PMC") else "PMC" + pc
    i = clean_isbn(ids.get("isbn") or "")
    if i:
        out["isbn"] = i
    for k in ("osti", "ntrs"):
        v = re.sub(r"\D", "", str(ids.get(k) or ""))
        if v:
            out[k] = v
    return out


def ids_from_note(n: dict) -> dict:
    ids = {"doi": n.get("doi") or "", "arxiv": n.get("arxiv") or ""}
    csl = n.get("csl") or {}
    if isinstance(csl, dict):
        ids.setdefault("doi", "")
        ids["doi"] = ids["doi"] or csl.get("DOI") or ""
        ids["pmid"] = csl.get("PMID") or ""
        ids["pmcid"] = csl.get("PMCID") or ""
        ids["isbn"] = (str(csl.get("ISBN") or "").split(",")[0])
        cu = csl.get("custom") if isinstance(csl.get("custom"), dict) else {}
        for k in ("arxiv", "osti", "ntrs"):
            if cu.get(k) and not ids.get(k):
                ids[k] = cu[k]
    for k, v in ids_from_url(n.get("url") or "").items():
        if not ids.get(k):
            ids[k] = v
    return normalize_ids(ids)


# ---------------------------------------------------------------------------------------------- CSL helpers
CROSSREF_TYPES = {
    "journal-article": "article-journal", "proceedings-article": "paper-conference", "posted-content": "article",
    "book": "book", "monograph": "book", "edited-book": "book", "reference-book": "book", "book-set": "book",
    "book-chapter": "chapter", "book-section": "chapter", "book-part": "chapter", "book-track": "chapter",
    "reference-entry": "entry", "report": "report", "report-component": "report", "report-series": "report",
    "dissertation": "thesis", "dataset": "dataset", "standard": "standard", "peer-review": "review",
    "proceedings": "book", "journal-issue": "periodical", "journal": "periodical", "component": "article",
    "other": "article", "grant": "article", "database": "dataset",
}
CSL_TYPES = {"article", "article-journal", "article-magazine", "article-newspaper", "bill", "book", "broadcast",
             "chapter", "classic", "collection", "dataset", "document", "entry", "entry-dictionary",
             "entry-encyclopedia", "event", "figure", "graphic", "hearing", "interview", "legal_case",
             "legislation", "manuscript", "map", "motion_picture", "musical_score", "pamphlet", "paper-conference",
             "patent", "performance", "periodical", "personal_communication", "post", "post-weblog", "regulation",
             "report", "review", "review-book", "software", "song", "speech", "standard", "thesis", "treaty",
             "webpage"}


def date_parts(*vals) -> dict | None:
    """{"date-parts": [[y, m, d]]} from Crossref date objects, ISO strings, '2020/06/04', '2020 Jun 4', a year."""
    months = {m: i + 1 for i, m in enumerate(("jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep",
                                              "oct", "nov", "dec"))}
    for v in vals:
        if not v:
            continue
        if isinstance(v, dict):
            dp = v.get("date-parts")
            if isinstance(dp, list) and dp and isinstance(dp[0], list):
                nums = [int(x) for x in dp[0][:3] if isinstance(x, (int, float)) or (isinstance(x, str) and x.isdigit())]
                if nums and 1000 <= nums[0] <= 2999:
                    return {"date-parts": [nums]}
            if v.get("raw") or v.get("date-time"):
                d = date_parts(v.get("raw") or v.get("date-time"))
                if d:
                    return d
            continue
        if isinstance(v, int):
            return {"date-parts": [[v]]} if 1000 <= v <= 2999 else None
        s = str(v).strip()
        m = re.match(r"^(\d{4})\s+([A-Za-z]{3})[a-z]*\.?(?:\s+(\d{1,2}))?", s)
        if m:
            nums = [int(m.group(1))] + ([months[m.group(2).lower()]] if m.group(2).lower() in months else [])
            if m.group(3) and len(nums) == 2:
                nums.append(int(m.group(3)))
            return {"date-parts": [nums]}
        m = re.match(r"^(\d{4})(?:[-/.](\d{1,2})(?:[-/.](\d{1,2}))?)?", s)
        if m:
            nums = [int(x) for x in m.groups() if x]
            if len(nums) > 1 and not 1 <= nums[1] <= 12:
                nums = nums[:1]
            return {"date-parts": [nums]}
        m = re.search(r"\b(1[5-9]\d\d|2[01]\d\d)\b", s)
        if m:
            return {"date-parts": [[int(m.group(1))]]}
    return None


def _first(v) -> str:
    if isinstance(v, list):
        v = next((x for x in v if x), "")
    return str(v or "").strip()


def _custom(csl: dict) -> dict:
    c = csl.get("custom")
    if not isinstance(c, dict):
        c = {}
        csl["custom"] = c
    return c


def finalize(csl: dict) -> dict:
    """Clean a CSL record: type, title (markup stripped, TeX kept, case untouched), names, DOI lower-case,
    numbers as strings, ISSN/ISBN as one string, empty values dropped."""
    out: dict[str, Any] = {}
    for k, v in (csl or {}).items():
        if v in (None, "", [], {}):
            continue
        out[k] = v
    t = str(out.get("type") or "article")
    out["type"] = t if t in CSL_TYPES else CROSSREF_TYPES.get(t, "article")
    for k in ("title", "container-title", "container-title-short", "collection-title", "event-title", "publisher",
              "publisher-place", "event-place", "genre", "title-short", "original-title"):
        if k in out:
            out[k] = clean_markup(_first(out[k]))
            if not out[k]:
                del out[k]
    if "abstract" in out:
        out["abstract"] = clean_abstract(out["abstract"])[:20000] or None
        if not out["abstract"]:
            del out["abstract"]
    for k in ("author", "editor", "translator", "container-author", "collection-editor", "director"):
        if k in out:
            out[k] = names(out[k])
            if not out[k]:
                del out[k]
    for k in ("issued", "accessed", "event-date", "original-date", "submitted"):
        if k in out:
            d = date_parts(out[k])
            if d:
                out[k] = d
            else:
                del out[k]
    if out.get("DOI"):
        d = clean_doi(out["DOI"])
        if d:
            out["DOI"] = d
        else:
            del out["DOI"]
    for k in ("ISSN", "ISBN"):
        if k in out:
            vals = out[k] if isinstance(out[k], list) else re.split(r"[,;\s]+", str(out[k]))
            vals = list(dict.fromkeys(str(x).strip() for x in vals if str(x).strip()))
            if vals:
                out[k] = ", ".join(vals)
            else:
                del out[k]
    for k in ("volume", "issue", "page", "number", "number-of-pages", "edition", "PMID", "PMCID", "language",
              "URL", "source", "archive", "archive_location", "call-number", "medium", "version", "section",
              "collection-number", "chapter-number", "status", "note", "keyword"):
        if k in out:
            v = out[k]
            out[k] = ", ".join(str(x) for x in v) if isinstance(v, list) else str(v).strip()
            if k == "page":
                out[k] = out[k].replace("–", "-").replace("—", "-")
            if not out[k]:
                del out[k]
    if isinstance(out.get("custom"), dict):
        out["custom"] = {str(k): v for k, v in out["custom"].items() if v not in (None, "", [], {})}
        if not out["custom"]:
            del out["custom"]
    elif "custom" in out:
        del out["custom"]
    return out


def merge(primary: dict, *others: dict) -> dict:
    """primary's values win; others fill keys it lacks (custom ids merged)."""
    out = dict(primary or {})
    for o in others:
        for k, v in (o or {}).items():
            if k in ("id", "type"):
                continue
            if k == "custom" and isinstance(v, dict):
                c = dict(out.get("custom") or {})
                for ck, cv in v.items():
                    c.setdefault(ck, cv)
                out["custom"] = c
            elif out.get(k) in (None, "", [], {}):
                out[k] = v
    return out


def legacy_from_csl(csl: dict | None) -> dict:
    """The app's legacy fields from a CSL record: title, authors ("Given Family"), year, venue, doi, arxiv,
    url, abstract."""
    csl = csl or {}
    people = csl.get("author") or csl.get("editor") or []
    year = None
    dp = (csl.get("issued") or {}).get("date-parts") if isinstance(csl.get("issued"), dict) else None
    if dp and dp[0]:
        try:
            year = int(dp[0][0])
        except (TypeError, ValueError):
            year = None
    custom = csl.get("custom") if isinstance(csl.get("custom"), dict) else {}
    arxiv = custom.get("arxiv") or ""
    if not arxiv and re.search(r"arxiv", str(csl.get("archive") or "") + str(csl.get("publisher") or ""), re.I):
        arxiv = clean_arxiv(csl.get("number") or "")
    doi = clean_doi(csl.get("DOI") or "")
    if ARXIV_DOI_RE.match(doi or ""):
        arxiv = arxiv or ARXIV_DOI_RE.match(doi).group(1)
        doi = ""
    t = csl.get("type")
    venue = csl.get("container-title") or ""
    if not venue and t in ("thesis", "report", "book") and csl.get("publisher"):
        venue = csl["publisher"]
    if not venue and arxiv and t == "article":
        venue = "arXiv"
    return {"title": csl.get("title") or "", "authors": [display_name(a) for a in people if display_name(a)],
            "year": year, "venue": venue, "doi": doi, "arxiv": clean_arxiv(arxiv) if arxiv else "",
            "url": csl.get("URL") or "", "abstract": csl.get("abstract") or ""}


def csl_from_legacy(n: dict) -> dict:
    """A rough CSL record from a note's legacy fields (Zotero's values, a page, an S2 lookup)."""
    venue = str(n.get("venue") or "")
    t = n.get("csl_type") or ("article" if not venue or re.match(r"(?i)^arxiv\b", venue) else "article-journal")
    csl = {"type": t, "title": n.get("title") or "", "author": names(n.get("authors") or []),
           "issued": date_parts(n.get("year")), "DOI": n.get("doi") or "", "URL": n.get("url") or "",
           "abstract": n.get("abstract") or ""}
    if venue and not re.match(r"(?i)^arxiv\b", venue):
        csl["container-title"] = venue
    if n.get("arxiv"):
        csl["custom"] = {"arxiv": clean_arxiv(n["arxiv"])}
    return finalize(csl)


# ---------------------------------------------------------------------------------------------- source mappers
def csl_from_crossref(m: dict) -> dict:
    """Crossref REST `message` -> CSL-JSON."""
    title = _first(m.get("title"))
    sub = _first(m.get("subtitle"))
    if sub and sub.lower() not in title.lower():
        title = f"{title}: {sub}" if title and not title.endswith((":", "?", "!")) else f"{title} {sub}"
    ctype = str(m.get("type") or "")
    csl: dict[str, Any] = {
        "type": CROSSREF_TYPES.get(ctype, "article"),
        "title": title,
        "container-title": _first(m.get("container-title")),
        "container-title-short": _first(m.get("short-container-title")),
        "author": m.get("author") or [],
        "editor": m.get("editor") or [],
        "issued": date_parts(m.get("issued"), m.get("published-print"), m.get("published-online"),
                             m.get("published"), m.get("created")),
        "volume": m.get("volume"), "issue": m.get("issue") or (m.get("journal-issue") or {}).get("issue"),
        "page": m.get("page"), "publisher": m.get("publisher"),
        "publisher-place": m.get("publisher-location"),
        "ISSN": m.get("ISSN") or [], "ISBN": m.get("ISBN") or [],
        "DOI": m.get("DOI"), "URL": f"https://doi.org/{clean_doi(m.get('DOI') or '')}" if m.get("DOI") else m.get("URL"),
        "abstract": m.get("abstract"), "language": m.get("language"), "source": "Crossref",
    }
    art = m.get("article-number")
    if art:
        csl["number"] = art
        if not csl.get("page"):
            csl["page"] = art  # APS/AIP/IOP article numbers (065001): CSL processors print `page`
    if ctype == "posted-content":
        csl["genre"] = "Preprint" if (m.get("subtype") or "preprint") == "preprint" else m.get("subtype")
        if not csl["container-title"]:
            csl["publisher"] = csl.get("publisher") or _first(m.get("institution", [{}])[0].get("name") if m.get("institution") else "")
    ev = m.get("event") or {}
    if isinstance(ev, dict) and ev.get("name"):
        csl["event-title"] = ev.get("name")
        csl["event-place"] = ev.get("location")
    if ctype == "dissertation":
        inst = m.get("institution") or []
        if inst and isinstance(inst[0], dict):
            csl["publisher"] = inst[0].get("name") or csl.get("publisher")
        csl["genre"] = m.get("degree") and _first(m.get("degree"))
    if ctype in ("book-chapter", "book-section", "book-part") and csl["container-title"]:
        pass
    if ctype == "proceedings-article" and not csl["container-title"] and ev.get("name"):
        csl["container-title"] = ev["name"]
    return finalize(csl)


def csl_from_negotiated(d: dict) -> dict:
    """A CSL-JSON answer from DOI content negotiation (Crossref transform or DataCite)."""
    csl = {k: v for k, v in (d or {}).items() if k not in ("id", "reference", "license", "link", "funder",
                                                           "deposited", "indexed", "relation", "assertion")}
    if csl.get("type") and csl["type"] not in CSL_TYPES:
        csl["type"] = CROSSREF_TYPES.get(csl["type"], "article")
    if csl.get("DOI") and not csl.get("URL"):
        csl["URL"] = f"https://doi.org/{clean_doi(csl['DOI'])}"
    for k in ("title", "container-title", "container-title-short", "publisher"):
        if isinstance(csl.get(k), list):
            csl[k] = _first(csl[k])
    if isinstance(csl.get("issued"), dict) or isinstance(csl.get("issued"), str):
        csl["issued"] = date_parts(csl["issued"])
    return finalize(csl)


ATOM = {"a": "http://www.w3.org/2005/Atom", "arxiv": "http://arxiv.org/schemas/atom"}


def _safe_xml(text: str):
    if re.search(r"<!(DOCTYPE|ENTITY)", text[:4000], re.I):
        raise MetaError("refusing XML with a DOCTYPE/ENTITY declaration")
    return ET.fromstring(text)


def csl_from_arxiv(xml_text: str, want: str = "") -> tuple[dict, str] | None:
    """arXiv API Atom feed -> (CSL record, journal DOI or ''). None if the feed has no such entry."""
    try:
        root = _safe_xml(xml_text)
    except (ET.ParseError, MetaError):
        return None
    for e in root.findall("a:entry", ATOM):
        title = " ".join((e.findtext("a:title", "", ATOM) or "").split())
        eid = (e.findtext("a:id", "", ATOM) or "").strip()
        aid = clean_arxiv(eid)
        if not title or title.lower() == "error" or not aid:
            continue
        if want and aid.lower() != want.lower():
            continue
        jdoi = clean_doi(e.findtext("arxiv:doi", "", ATOM) or "")
        cat = e.find("arxiv:primary_category", ATOM)
        csl = {
            "type": "article", "genre": "Preprint", "title": title,
            "author": [{"literal": " ".join((a.findtext("a:name", "", ATOM) or "").split())} for a in e.findall("a:author", ATOM)],
            "issued": date_parts(e.findtext("a:published", "", ATOM)),
            "abstract": e.findtext("a:summary", "", ATOM), "publisher": "arXiv", "archive": "arXiv",
            "number": aid, "DOI": f"10.48550/arXiv.{aid}", "URL": f"https://arxiv.org/abs/{aid}",
            "source": "arXiv.org", "custom": {"arxiv": aid},
        }
        # arXiv author names are display strings: parse them like any "Given Family" string
        csl["author"] = [norm_name(a["literal"]) or a for a in csl["author"] if a["literal"]]
        if cat is not None and cat.get("term"):
            csl["custom"]["arxiv_category"] = cat.get("term")
        jref = " ".join((e.findtext("arxiv:journal_ref", "", ATOM) or "").split())
        if jref:
            csl["custom"]["journal_ref"] = jref
        return finalize(csl), jdoi
    return None


def _pubmed_name(s: str):
    """PubMed's "Family IN" ("Smith JA", "de Vries H", "King ML Jr") -> a CSL name."""
    toks = str(s or "").split()
    suffix = toks.pop() if len(toks) > 2 and SUFFIX_RE.match(toks[-1]) else ""
    if len(toks) >= 2 and re.fullmatch(r"[A-Z]{1,4}", toks[-1]):
        return {"family": " ".join(toks[:-1]), "given": " ".join(c + "." for c in toks[-1]),
                **({"suffix": suffix} if suffix else {})}
    return s


def csl_from_pubmed(doc: dict, uid: str) -> dict | None:
    """E-utilities esummary JSON (db=pubmed or pmc) -> CSL."""
    res = (doc or {}).get("result") or {}
    rec = res.get(str(uid)) or (res.get((res.get("uids") or [None])[0]) if res.get("uids") else None)
    if not isinstance(rec, dict) or rec.get("error") or not rec.get("title"):
        return None
    aids = {str(a.get("idtype") or "").lower(): str(a.get("value") or "") for a in rec.get("articleids") or []
            if isinstance(a, dict)}
    pmid = aids.get("pubmed") or aids.get("pmid") or (str(rec.get("uid")) if "pmcid" not in aids else "")
    pmcid = aids.get("pmc") or aids.get("pmcid") or ""
    m = re.search(r"PMC\d+", pmcid)
    csl = {
        "type": "article-journal", "title": str(rec.get("title") or "").rstrip("."),
        "container-title": rec.get("fulljournalname") or rec.get("source"),
        "container-title-short": rec.get("source"),
        "author": [_pubmed_name(a.get("name")) for a in rec.get("authors") or [] if isinstance(a, dict)
                   and (a.get("authtype") or "Author") == "Author" and a.get("name")],
        "issued": date_parts(rec.get("sortpubdate"), rec.get("pubdate"), rec.get("epubdate")),
        "volume": rec.get("volume"), "issue": rec.get("issue"), "page": rec.get("pages"),
        "ISSN": [x for x in (rec.get("issn"), rec.get("essn")) if x], "DOI": aids.get("doi") or rec.get("doi"),
        "PMID": re.sub(r"\D", "", pmid) if pmid else "", "PMCID": m.group(0) if m else "",
        "source": "PubMed",
    }
    if csl["DOI"]:
        csl["URL"] = f"https://doi.org/{clean_doi(csl['DOI'])}"
    elif csl["PMID"]:
        csl["URL"] = f"https://pubmed.ncbi.nlm.nih.gov/{csl['PMID']}/"
    return finalize(csl)


def csl_from_openlibrary(doc: dict, isbn: str) -> dict | None:
    rec = (doc or {}).get(f"ISBN:{isbn}")
    if not isinstance(rec, dict) or not rec.get("title"):
        return None
    title = rec["title"] + (f": {rec['subtitle']}" if rec.get("subtitle") else "")
    idents = rec.get("identifiers") or {}
    isbns = list(idents.get("isbn_13") or []) + list(idents.get("isbn_10") or []) or [isbn]
    csl = {"type": "book", "title": title,
           "author": [a.get("name") for a in rec.get("authors") or [] if isinstance(a, dict) and a.get("name")],
           "publisher": _first([p.get("name") for p in rec.get("publishers") or [] if isinstance(p, dict)]),
           "publisher-place": _first([p.get("name") for p in rec.get("publish_places") or [] if isinstance(p, dict)]),
           "issued": date_parts(rec.get("publish_date")), "ISBN": isbns,
           "number-of-pages": rec.get("number_of_pages"), "URL": rec.get("url"), "source": "Open Library"}
    return finalize(csl)


OSTI_TYPES = {"journal article": "article-journal", "technical report": "report", "conference": "paper-conference",
              "thesis/dissertation": "thesis", "book": "book", "dataset": "dataset", "software": "software",
              "patent": "patent", "program document": "report", "miscellaneous": "document"}


def csl_from_osti(doc) -> dict | None:
    rec = doc[0] if isinstance(doc, list) and doc else doc
    if not isinstance(rec, dict) or not rec.get("title"):
        return None
    ptype = str(rec.get("product_type") or "").lower()
    csl = {"type": OSTI_TYPES.get(ptype, "report"), "title": rec.get("title"), "author": rec.get("authors") or [],
           "issued": date_parts(rec.get("publication_date")), "DOI": rec.get("doi"),
           "container-title": rec.get("journal_name"), "volume": rec.get("journal_volume"),
           "issue": rec.get("journal_issue"), "publisher": rec.get("publisher"),
           "number": rec.get("report_number"), "abstract": rec.get("description"),
           "URL": f"https://www.osti.gov/biblio/{rec.get('osti_id')}" if rec.get("osti_id") else "",
           "source": "OSTI.GOV", "custom": {"osti": str(rec.get("osti_id") or "")}}
    if csl["type"] == "report" and not csl.get("publisher"):
        csl["publisher"] = _first(rec.get("research_orgs")) or _first(rec.get("research_org"))
    if csl["type"] == "article-journal" and not csl["container-title"]:
        csl["type"] = "report"
    return finalize(csl)


NTRS_TYPES = {"journal article": "article-journal", "conference paper": "paper-conference",
              "technical publication (tp)": "report", "technical memorandum (tm)": "report",
              "contractor report (cr)": "report", "thesis/dissertation": "thesis", "book": "book",
              "book chapter": "chapter", "presentation": "speech", "preprint (draft being sent to journal)": "article"}


def csl_from_ntrs(rec: dict) -> dict | None:
    if not isinstance(rec, dict) or not rec.get("title"):
        return None
    kind = str(rec.get("stiTypeDetails") or rec.get("stiType") or "").lower().replace("_", " ")
    t = NTRS_TYPES.get(kind) or ("report" if "report" in kind or "technical" in kind or "memorandum" in kind
                                 else "paper-conference" if "conference" in kind else "report")
    people = []
    for a in sorted(rec.get("authorAffiliations") or [], key=lambda x: (x or {}).get("sequence") or 0):
        nm = (((a or {}).get("meta") or {}).get("author") or {}).get("name")
        if nm:
            people.append(nm)
    pub = (rec.get("publications") or [{}])[0] or {}
    doi = next((i.get("number") for i in rec.get("sourceIdentifiers") or []
                if isinstance(i, dict) and str(i.get("type")).upper() == "DOI"), "")
    meet = (rec.get("meetings") or [{}])[0] or {}
    csl = {"type": t, "title": rec.get("title"), "author": people,
           "issued": date_parts(pub.get("publicationDate"), rec.get("distributionDate"), rec.get("submittedDate")),
           "container-title": pub.get("publicationName"), "volume": pub.get("volume"), "issue": pub.get("issue"),
           "DOI": doi, "abstract": "" if rec.get("abstract") == "No abstract available" else rec.get("abstract"),
           "publisher": "NASA" if t == "report" else "",
           "number": _first(rec.get("otherReportNumbers")) if t == "report" else "",
           "event-title": meet.get("name"), "event-place": meet.get("location"),
           "URL": f"https://ntrs.nasa.gov/citations/{rec.get('id')}" if rec.get("id") else "",
           "source": "NASA NTRS", "custom": {"ntrs": str(rec.get("id") or "")}}
    if t == "paper-conference" and not csl["container-title"]:
        csl["container-title"] = meet.get("name")
    return finalize(csl)


def csl_from_s2(p: dict) -> dict | None:
    """A Semantic Scholar paper (raw API JSON, or app/scholar.py's lookup() dict) -> CSL."""
    if not isinstance(p, dict) or not p.get("title"):
        return None
    ext = p.get("externalIds") or {}
    authors = [a.get("name") if isinstance(a, dict) else a for a in p.get("authors") or []]
    doi = ext.get("DOI") or p.get("doi") or ""
    arxiv = ext.get("ArXiv") or p.get("arxiv") or ""
    venue = p.get("venue") or ((p.get("journal") or {}).get("name") if isinstance(p.get("journal"), dict) else "")
    j = p.get("journal") if isinstance(p.get("journal"), dict) else {}
    pre = not venue or re.match(r"(?i)^arxiv", venue or "")
    csl = {"type": "article" if pre else "article-journal", "title": p.get("title"), "author": authors,
           "issued": date_parts(p.get("publicationDate"), p.get("year")), "DOI": doi,
           "container-title": "" if pre else venue, "volume": j.get("volume"),
           "page": (j.get("pages") or "").strip() or None, "abstract": p.get("abstract"),
           "URL": f"https://doi.org/{clean_doi(doi)}" if doi else (f"https://arxiv.org/abs/{arxiv}" if arxiv else p.get("url")),
           "source": "Semantic Scholar", "custom": {"arxiv": clean_arxiv(arxiv)} if arxiv else {}}
    if arxiv and pre:
        csl.update(publisher="arXiv", archive="arXiv", number=clean_arxiv(arxiv))
    return finalize(csl)


ZOTERO_TYPES = {"journalArticle": "article-journal", "conferencePaper": "paper-conference", "book": "book",
                "bookSection": "chapter", "thesis": "thesis", "report": "report", "preprint": "article",
                "webpage": "webpage", "magazineArticle": "article-magazine", "newspaperArticle": "article-newspaper",
                "patent": "patent", "dataset": "dataset", "computerProgram": "software", "presentation": "speech",
                "manuscript": "manuscript", "document": "document", "standard": "standard",
                "encyclopediaArticle": "entry-encyclopedia", "dictionaryEntry": "entry-dictionary"}


def csl_from_zotero_item(it: dict) -> dict | None:
    """A Zotero API JSON item (translation-server /web) -> CSL."""
    if not isinstance(it, dict) or not it.get("title"):
        return None
    t = ZOTERO_TYPES.get(it.get("itemType") or "", "article")
    cr = [c for c in it.get("creators") or [] if isinstance(c, dict)]
    auth = [c for c in cr if (c.get("creatorType") or "author") == "author"]
    eds = [c for c in cr if c.get("creatorType") == "editor"]
    to = lambda c: ({"literal": c["name"]} if c.get("name") else {"family": c.get("lastName") or "", "given": c.get("firstName") or ""})
    extra = str(it.get("extra") or "")
    pm = re.search(r"(?im)^PMID:\s*(\d+)", extra)
    pmc = re.search(r"(?im)^PMCID:\s*(PMC\d+)", extra)
    ax = re.search(r"(?im)^arXiv:\s*(\S+)", extra) or re.search(r"arxiv\.org/abs/(\S+)", it.get("url") or "")
    csl = {"type": t, "title": it.get("title"), "author": [to(c) for c in auth], "editor": [to(c) for c in eds],
           "issued": date_parts(it.get("date")), "DOI": it.get("DOI"),
           "container-title": it.get("publicationTitle") or it.get("proceedingsTitle") or it.get("bookTitle")
           or it.get("websiteTitle"),
           "container-title-short": it.get("journalAbbreviation"), "volume": it.get("volume"),
           "issue": it.get("issue"), "page": it.get("pages"), "ISSN": it.get("ISSN"), "ISBN": it.get("ISBN"),
           "publisher": it.get("publisher") or it.get("university") or it.get("institution"),
           "publisher-place": it.get("place"), "event-title": it.get("conferenceName"),
           "number": it.get("reportNumber") or it.get("number"), "genre": it.get("thesisType") or it.get("reportType"),
           "abstract": it.get("abstractNote"), "URL": it.get("url"), "language": it.get("language"),
           "PMID": pm.group(1) if pm else "", "PMCID": pmc.group(1) if pmc else "",
           "source": "Zotero translation-server", "custom": {"arxiv": clean_arxiv(ax.group(1))} if ax else {}}
    if t == "article" and ax:
        csl.update(publisher=csl.get("publisher") or "arXiv", archive="arXiv", number=clean_arxiv(ax.group(1)))
    return finalize(csl)


def csl_from_page(page: dict) -> dict | None:
    """What a page (the extension's citation_* reading) or a PDF said, as CSL."""
    if not isinstance(page, dict) or not (page.get("title") or page.get("doi")):
        return None
    venue = str(page.get("venue") or "")
    genre = str(page.get("genre") or page.get("csl_type") or "")
    t = genre if genre in CSL_TYPES else ("article" if not venue or re.match(r"(?i)^arxiv\b", venue)
                                          else "article-journal")
    pages = str(page.get("pages") or "")
    if not pages and page.get("first_page"):
        pages = str(page["first_page"]) + (f"-{page['last_page']}" if page.get("last_page") else "")
    csl = {"type": t, "title": page.get("title"), "author": page.get("authors") or [],
           "issued": date_parts(page.get("date"), page.get("year")), "DOI": page.get("doi"),
           "container-title": "" if re.match(r"(?i)^arxiv\b", venue) else venue,
           "container-title-short": page.get("journal_abbrev"), "volume": page.get("volume"),
           "issue": page.get("issue"), "page": pages, "ISSN": page.get("issn"), "ISBN": page.get("isbn"),
           "publisher": page.get("publisher"), "abstract": page.get("abstract"), "URL": page.get("url"),
           "PMID": page.get("pmid"), "event-title": page.get("conference"), "source": "page",
           "custom": {k: v for k, v in (("arxiv", clean_arxiv(page.get("arxiv") or "")),
                                        ("osti", page.get("osti")), ("ntrs", page.get("ntrs"))) if v}}
    if t == "article" and csl["custom"].get("arxiv"):
        csl.update(publisher="arXiv", archive="arXiv", number=csl["custom"]["arxiv"])
    return finalize(csl)


# ---------------------------------------------------------------------------------------------- resolver
def _same_title(a: str, b: str) -> float:
    norm = lambda s: re.sub(r"[^a-z0-9]+", " ", clean_markup(s or "").lower()).strip()
    return difflib.SequenceMatcher(None, norm(a), norm(b)).ratio()


class Resolver:
    """Looks identifiers up. `fetch` / `fetch_local` default to app/urlfetch; tests pass fakes or replace
    urlfetch._request."""

    def __init__(self, cache: Cache | None = None, limiter: RateLimiter | None = None,
                 email: str | None = None, ts_port: int | None = None):
        self.cache = cache or Cache(None)
        self.limiter = limiter or RateLimiter()
        self._email = email
        self._ts_port = ts_port
        self._ts_down_until = 0.0
        self.calls: list[str] = []  # URLs actually requested (not served from the cache), for tests and logs

    @property
    def email(self) -> str:
        return contact_email() if self._email is None else self._email

    @property
    def ts_port(self) -> int:
        return translation_server_port() if self._ts_port is None else self._ts_port

    # ------------------------------------------------------------ HTTP
    def http(self, url: str, *, accept: str = "application/json", parse: str = "json",
             headers: dict | None = None):
        """(status, parsed body) for a GET; cached. 404/410 -> (404, None). Raises MetaError when the host
        can't be reached or answers 429/5xx (not cached, so a later try can succeed)."""
        key = f"GET {url} {accept}"
        hit, val = self.cache.get(key)
        if hit:
            return val
        host = (urlsplit(url).hostname or "").lower()
        self.limiter.wait(host)
        hdrs = {"User-Agent": user_agent(self.email), **(headers or {})}
        self.calls.append(url)
        try:
            r = urlfetch.fetch(url, allowed_hosts=ALLOWED_HOSTS, accept=accept, headers=hdrs)
        except urlfetch.FetchError as e:
            raise MetaError(str(e))
        if r.status in (404, 410, 400):
            self.cache.put(key, 404, None)
            return 404, None
        if r.status != 200:
            raise MetaError(f"{host} answered HTTP {r.status}")
        text = r.body.decode("utf-8", "replace")
        if parse == "json":
            try:
                data = json.loads(text)
            except ValueError:
                raise MetaError(f"{host} sent something that isn't JSON")
        else:
            data = text
        self.cache.put(key, 200, data)
        return 200, data

    # ------------------------------------------------------------ sources
    def crossref(self, doi: str) -> dict | None:
        doi = clean_doi(doi)
        if not doi:
            return None
        st, data = self.http(CROSSREF + quote(doi, safe="/:;()"))
        if st != 200 or not isinstance(data, dict):
            return None
        m = data.get("message") if isinstance(data.get("message"), dict) else None
        if not m or not m.get("title"):
            return None
        return csl_from_crossref(m)

    def negotiate(self, doi: str) -> tuple[dict, str] | None:
        """DOI content negotiation (any registration agency that supports CSL-JSON; DataCite for datasets,
        theses in repositories, Zenodo, arXiv's own DOIs)."""
        doi = clean_doi(doi)
        if not doi:
            return None
        url = DOI_ORG + quote(doi, safe="/:;()")
        key = f"GET {url} csl"
        hit, val = self.cache.get(key)
        if hit:
            st, data = val
        else:
            self.limiter.wait("doi.org")
            self.calls.append(url)
            try:
                r = urlfetch.fetch(url, allowed_hosts=ALLOWED_HOSTS, accept="application/vnd.citationstyles.csl+json",
                                   headers={"User-Agent": user_agent(self.email)})
            except urlfetch.FetchError as e:
                raise MetaError(str(e))
            if r.status in (404, 406, 400):
                self.cache.put(key, 404, None)
                return None
            if r.status != 200:
                raise MetaError(f"doi.org answered HTTP {r.status}")
            try:
                d = json.loads(r.body.decode("utf-8", "replace"))
            except ValueError:
                return None
            data = {"csl": d, "host": (urlsplit(r.url).hostname or "").lower()}
            st = 200
            self.cache.put(key, 200, data)
        if st != 200 or not isinstance(data, dict) or not isinstance(data.get("csl"), dict):
            return None
        csl = csl_from_negotiated(data["csl"])
        if not csl.get("title"):
            return None
        return csl, ("datacite" if "datacite" in (data.get("host") or "") else "crossref")

    def arxiv(self, aid: str) -> tuple[dict, str] | None:
        aid = clean_arxiv(aid)
        if not aid:
            return None
        st, text = self.http(f"{ARXIV_API}?{urlencode({'id_list': aid})}", accept="application/atom+xml", parse="text")
        if st != 200 or not text:
            return None
        return csl_from_arxiv(text, aid)

    def pubmed(self, pmid: str = "", pmcid: str = "") -> dict | None:
        db, uid = ("pubmed", pmid) if pmid else ("pmc", re.sub(r"\D", "", pmcid))
        if not uid:
            return None
        q = {"db": db, "id": uid, "retmode": "json", "tool": "research_brain"}
        if self.email:
            q["email"] = self.email
        st, data = self.http(f"{EUTILS}?{urlencode(q)}")
        if st != 200:
            return None
        csl = csl_from_pubmed(data, uid)
        if csl and db == "pmc" and not csl.get("PMCID"):
            csl["PMCID"] = "PMC" + uid
        return csl

    def openlibrary(self, isbn: str) -> dict | None:
        isbn = clean_isbn(isbn)
        if not isbn:
            return None
        st, data = self.http(f"{OPENLIBRARY}?{urlencode({'bibkeys': f'ISBN:{isbn}', 'format': 'json', 'jscmd': 'data'})}")
        return csl_from_openlibrary(data, isbn) if st == 200 else None

    def osti(self, oid: str) -> dict | None:
        oid = re.sub(r"\D", "", str(oid or ""))
        if not oid:
            return None
        st, data = self.http(OSTI_API + oid)
        return csl_from_osti(data) if st == 200 else None

    def ntrs(self, nid: str) -> dict | None:
        nid = re.sub(r"\D", "", str(nid or ""))
        if not nid:
            return None
        st, data = self.http(NTRS_API + nid)
        return csl_from_ntrs(data) if st == 200 else None

    def s2(self, doi: str = "", arxiv: str = "", title: str = "") -> dict | None:
        from . import apikeys
        hdrs = {"x-api-key": apikeys.get("s2")} if apikeys.get("s2") else {}
        if doi or arxiv:
            pid = f"DOI:{doi}" if doi else f"ARXIV:{arxiv}"
            st, data = self.http(f"{S2_API}{quote(pid, safe=':/.')}?{urlencode({'fields': S2_FIELDS})}", headers=hdrs)
            return csl_from_s2(data) if st == 200 else None
        if title:
            st, data = self.http(f"{S2_API}search/match?{urlencode({'query': title[:300], 'fields': S2_FIELDS})}", headers=hdrs)
            cand = ((data or {}).get("data") or [None])[0] if st == 200 and isinstance(data, dict) else None
            if cand and _same_title(cand.get("title") or "", title) >= 0.9:
                return csl_from_s2(cand)
        return None

    def crossref_search(self, title: str, author: str = "") -> dict | None:
        title = " ".join(str(title or "").split())
        if len(title) < 12:
            return None
        q = {"query.bibliographic": title[:300], "rows": 3}
        if author:
            q["query.author"] = author[:100]
        st, data = self.http(f"{CROSSREF.rstrip('/')}?{urlencode(q)}")
        if st != 200 or not isinstance(data, dict):
            return None
        for it in ((data.get("message") or {}).get("items") or [])[:3]:
            if _same_title(_first(it.get("title")), title) >= 0.93:
                return csl_from_crossref(it)
        return None

    def translation_server(self, url: str) -> dict | None:
        """Zotero translation-server on 127.0.0.1 (optional): POST /web with the page URL."""
        port = self.ts_port
        if not port or not url or time.monotonic() < self._ts_down_until:
            return None
        try:
            urlfetch.check_url(url)  # only public pages are handed to it
        except urlfetch.FetchError:
            return None
        key = f"TS {url}"
        hit, val = self.cache.get(key)
        if hit:
            st, items = val
        else:
            try:
                r = urlfetch.fetch_local(port, "/web", method="POST", data=url.encode("utf-8"),
                                         headers={"Content-Type": "text/plain"})
            except urlfetch.FetchError:
                self._ts_down_until = time.monotonic() + 300  # not running: don't ask again for 5 minutes
                return None
            if r.status != 200:
                return None  # 300 (several items on the page), 501 (no translator), 500 ...
            try:
                items = json.loads(r.body.decode("utf-8", "replace"))
            except ValueError:
                return None
            st = 200
            self.cache.put(key, 200, items)
        if st != 200 or not isinstance(items, list):
            return None
        for it in items:
            csl = csl_from_zotero_item(it)
            if csl:
                return csl
        return None

    # ------------------------------------------------------------ the order
    def resolve(self, ids: dict | None = None, *, url: str = "", title: str = "", page: dict | None = None,
                s2: dict | None = None) -> dict | None:
        """The best record for these identifiers, merged with what the other sources add. `page` is the page's
        own CSL (csl_from_page), `s2` a Semantic Scholar result the caller already has (not fetched again)."""
        ids = normalize_ids({**ids_from_url(url), **{k: v for k, v in (ids or {}).items() if v}})
        errors: list[str] = []
        found: list[tuple[dict, str]] = []

        def attempt(fn, *a):
            try:
                return fn(*a)
            except MetaError as e:
                errors.append(f"{fn.__name__}: {e}")
            except Exception as e:  # a malformed answer must not break an import
                errors.append(f"{fn.__name__}: {e.__class__.__name__}: {e}")
            return None

        def by_doi(doi: str):
            rec = attempt(self.crossref, doi)
            if rec:
                return rec, "crossref"
            neg = attempt(self.negotiate, doi)
            return neg if neg else None

        primary = None
        if ids.get("doi"):
            primary = by_doi(ids["doi"])
        if ids.get("arxiv"):
            ax = attempt(self.arxiv, ids["arxiv"])
            if ax:
                rec, jdoi = ax
                if not primary and jdoi and jdoi != ids.get("doi"):
                    primary = by_doi(jdoi)
                found.append((rec, "arxiv"))
        if not primary and (ids.get("pmid") or ids.get("pmcid")):
            pm = attempt(self.pubmed, ids.get("pmid", ""), ids.get("pmcid", ""))
            if pm:
                if pm.get("DOI") and not ids.get("doi"):
                    primary = by_doi(pm["DOI"])
                found.append((pm, "pubmed"))
        if not primary and ids.get("isbn"):
            ol = attempt(self.openlibrary, ids["isbn"])
            if ol:
                primary = (ol, "openlibrary")
        if not primary and ids.get("osti"):
            o = attempt(self.osti, ids["osti"])
            if o:
                primary = (by_doi(o["DOI"]) if o.get("DOI") and not ids.get("doi") else None) or (o, "osti")
                found.append((o, "osti"))
        if not primary and ids.get("ntrs"):
            nt = attempt(self.ntrs, ids["ntrs"])
            if nt:
                primary = (by_doi(nt["DOI"]) if nt.get("DOI") and not ids.get("doi") else None) or (nt, "ntrs")
                found.append((nt, "ntrs"))
        if not primary and not found and url:
            ts = attempt(self.translation_server, url)
            if ts:
                if ts.get("DOI") and not ids.get("doi"):
                    primary = by_doi(ts["DOI"])
                found.append((ts, "translation-server"))
        if not primary and not found and title and not ids:
            cr = attempt(self.crossref_search, title)
            if cr:
                primary = (cr, "crossref")
        if not primary and not found:
            s2rec = csl_from_s2(s2) if s2 else None
            if not s2rec and (ids.get("doi") or ids.get("arxiv") or (title and not ids)):
                s2rec = attempt(self.s2, ids.get("doi", ""), ids.get("arxiv", ""), "" if ids else title)
            if s2rec:
                primary = (s2rec, "s2")
        if not primary and found:
            found.sort(key=lambda fs: -RANK.get(fs[1], 0))
            primary = found.pop(0)
        page_csl = finalize(page) if page else None
        if not primary and page_csl and page_csl.get("title"):
            primary = (page_csl, "page")
            page_csl = None
        if not primary:
            return {"csl": None, "source": "", "errors": errors, "ids": ids} if errors else None
        csl, source = primary
        csl = merge(csl, *[f for f, _ in found], page_csl or {})
        cu = dict(csl.get("custom") or {})
        for k in ("arxiv", "osti", "ntrs"):
            if ids.get(k) and not cu.get(k):
                cu[k] = ids[k]
        if cu:
            csl["custom"] = cu
        if ids.get("pmid") and not csl.get("PMID"):
            csl["PMID"] = ids["pmid"]
        if ids.get("pmcid") and not csl.get("PMCID"):
            csl["PMCID"] = ids["pmcid"]
        return {"csl": finalize(csl), "source": source, "errors": errors, "ids": ids}


_resolvers: dict[str, Resolver] = {}
_resolvers_lock = threading.Lock()


def resolver_for(store) -> Resolver:
    """One Resolver per library, caching in <vault>/.index/metadata_cache.json."""
    key = str(Path(store.index_dir).resolve())
    with _resolvers_lock:
        r = _resolvers.get(key)
        if r is None:
            r = _resolvers[key] = Resolver(Cache(Path(store.index_dir) / "metadata_cache.json"))
        return r


# ---------------------------------------------------------------------------------------------- applying a record
def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _csl_set(csl: dict, k: str, v) -> None:
    """Write one legacy field's value into the CSL record (the user edited it in the app)."""
    if k == "title":
        csl["title"] = v
    elif k == "authors":
        csl["author"] = names(v or [])
    elif k == "year":
        old = ((csl.get("issued") or {}).get("date-parts") or [[None]])[0]
        if v:
            csl["issued"] = {"date-parts": [[int(v)] + (list(old[1:]) if old and old[0] == int(v) else [])]}
        else:
            csl.pop("issued", None)
    elif k == "venue":
        if csl.get("type") in ("thesis", "report") and not csl.get("container-title"):
            csl["publisher"] = v
        else:
            csl["container-title"] = v
            if csl.get("container-title-short") and v != csl.get("container-title"):
                pass
    elif k == "abstract":
        csl["abstract"] = v
    elif k == "url":
        csl["URL"] = v
    for kk in [x for x, val in csl.items() if val in (None, "", [])]:
        csl.pop(kk, None)


def sync_edit(note: dict, changed) -> None:
    """Store.update hook: the user edited legacy fields of a note that has a CSL record. The record follows the
    edit, and the field is marked as edited so a background lookup won't undo it."""
    csl = note.get("csl")
    if not isinstance(csl, dict) or not csl:
        return
    keys = [k for k in changed if k in LEGACY]
    if not keys:
        return
    csl = dict(csl)
    cu = dict(csl.get("custom") or {})
    edited = list(cu.get("rb_edited") or [])
    before = legacy_from_csl(csl)
    for k in keys:
        if note.get(k) == before.get(k):
            continue
        _csl_set(csl, k, note.get(k))
        if k not in edited:
            edited.append(k)
    if edited:
        cu["rb_edited"] = edited
        csl["custom"] = cu
    note["csl"] = csl


def apply(store, nid: str, res: dict, *, force: bool = False, keep_edits: bool = True, fresh: bool = False) -> bool:
    """Write a resolved record to a note (see the module docstring for the rules). True if anything changed."""
    new = finalize((res or {}).get("csl") or {})
    src = (res or {}).get("source") or "page"
    if not new.get("title"):
        return False
    with store.lock:
        try:
            n = store.get(nid)
        except KeyError:
            return False
        if n.get("_yaml_error"):
            return False
        old = n.get("csl") if isinstance(n.get("csl"), dict) and n.get("csl") else None
        cu_old = (old or {}).get("custom") or {}
        edited = set(cu_old.get("rb_edited") or []) if keep_edits else set()
        zot = n.get("meta_source") == "zotero" or bool(n.get("zotero_key") and not n.get("meta_source"))
        derived = legacy_from_csl(new)
        writes: dict[str, Any] = {}
        if zot:
            mine = csl_from_legacy(n)
            csl = merge(mine, old or {}, new)  # Zotero's values first, then what Crossref adds
            csl["type"] = new.get("type") or csl.get("type")
            for k in ("title", "author", "issued", "container-title"):
                if mine.get(k):
                    csl[k] = mine[k]
            source = "zotero"
            for k in LEGACY:
                if not n.get(k) and derived.get(k) not in (None, "", []):
                    writes[k] = derived[k]
        else:
            old_rank = RANK.get(n.get("meta_source") or "", 0)
            replace = force or not old or RANK.get(src, 0) >= old_rank
            prev = legacy_from_csl(old) if old else None
            if replace:
                csl = merge(new, {"custom": {k: v for k, v in cu_old.items() if k != "rb_edited"}})
                source = src
            else:
                csl = merge(old, new)
                source = n.get("meta_source") or src
            for k in LEGACY:
                v, cur = derived.get(k), n.get(k)
                if v in (None, "", []) or v == cur:
                    continue
                if cur in (None, "", []):
                    writes[k] = v
                elif replace and k not in edited and (force or fresh or (prev is not None and cur == prev.get(k))):
                    writes[k] = v
        for k in ("doi", "arxiv"):
            v = derived.get(k)
            if v and not n.get(k):
                other = store.find_duplicate(v if k == "doi" else "", v if k == "arxiv" else "", "")
                if other is None or other.get("id") == nid:
                    writes[k] = v
        if edited:
            for k in edited:
                if k in LEGACY and k not in writes:
                    _csl_set(csl, k, n.get(k))
            csl.setdefault("custom", {})
            csl["custom"] = {**csl["custom"], "rb_edited": sorted(edited)}
        csl = finalize(csl)
        changed = bool(writes) or csl != old or source != n.get("meta_source")
        n.update(writes)
        if not (n.get("title") or "").strip():
            n["title"] = derived["title"]
        n["csl"], n["meta_source"], n["meta_checked"] = csl, source, now_iso()
        store.save(n)
        return changed


# ---------------------------------------------------------------------------------------------- refresh, background
def page_record(page: dict | None) -> dict | None:
    return csl_from_page(page) if page else None


def refresh(store, nid: str, *, ids: dict | None = None, page: dict | None = None, s2: dict | None = None,
            url: str = "", force: bool = False, keep_edits: bool = True, fresh: bool = False,
            title_search: bool = True, resolver: Resolver | None = None) -> dict:
    """Resolve one note now (blocking) and apply the record. Returns {"found", "source", "changed", "errors"}."""
    try:
        n = store.get(nid)
    except KeyError:
        return {"found": False, "source": "", "changed": False, "errors": ["no such note"]}
    if n.get("type") == "fragment":
        return {"found": False, "source": "", "changed": False, "errors": ["fragments have no metadata"]}
    if not force and n.get("meta_checked") and n.get("csl") and not fresh:
        try:
            age = dt.datetime.now().astimezone() - dt.datetime.fromisoformat(n["meta_checked"])
            if age.total_seconds() < RECHECK_DAYS * 86400:
                return {"found": True, "source": n.get("meta_source") or "", "changed": False, "errors": [], "skipped": True}
        except ValueError:
            pass
    all_ids = {**ids_from_note(n), **normalize_ids(ids)}
    page_csl = page_record(page)
    title = ""
    if title_search and not all_ids:
        t = (n.get("title") or "").strip()
        if t and t != nid and not re.match(r"(?i)^(untitled|web clip)", t):
            title = t
    r = resolver or resolver_for(store)
    res = r.resolve(all_ids, url=url or n.get("url") or "", title=title, page=page_csl, s2=s2)
    if not res or not res.get("csl"):
        return {"found": False, "source": "", "changed": False, "errors": (res or {}).get("errors") or []}
    changed = apply(store, nid, res, force=force, keep_edits=keep_edits, fresh=fresh)
    return {"found": True, "source": res["source"], "changed": changed, "errors": res.get("errors") or []}


class _Worker:
    """A small queue of background refreshes: two threads, one job per note (a second request for a note that
    is still waiting merges into it)."""

    THREADS = 2

    def __init__(self):
        self.lock = threading.Lock()
        self.pending: dict[str, dict] = {}
        self.order: list[str] = []
        self.cv = threading.Condition(self.lock)
        self.threads: list[threading.Thread] = []

    def submit(self, store, nid: str, publish, kw: dict) -> threading.Event:
        with self.cv:
            job = self.pending.get(nid)
            if job is None:
                job = self.pending[nid] = {"store": store, "publish": publish, "kw": dict(kw), "done": threading.Event()}
                self.order.append(nid)
            else:
                for k, v in kw.items():
                    if v not in (None, "", {}, False):
                        job["kw"][k] = v
                job["publish"] = job["publish"] or publish
            while len(self.threads) < self.THREADS:
                t = threading.Thread(target=self._run, daemon=True, name=f"metadata-{len(self.threads)}")
                self.threads.append(t)
                t.start()
            self.cv.notify()
            return job["done"]

    def _run(self):
        while True:
            with self.cv:
                while not self.order:
                    self.cv.wait()
                nid = self.order.pop(0)
                job = self.pending.get(nid)
            try:
                out = refresh(job["store"], nid, **job["kw"])
                if out.get("changed") and job["publish"]:
                    job["publish"]({"type": "notes-changed", "ids": [nid], "reason": "metadata",
                                    "source": out.get("source") or ""})
            except Exception as e:
                print(f"[research_brain] metadata lookup for {nid} failed: {e!r}")
            finally:
                with self.cv:
                    self.pending.pop(nid, None)
                job["done"].set()

    def busy(self) -> set[str]:
        with self.lock:
            return set(self.pending)


_worker = _Worker()


def schedule(store, nid: str, *, wait: float = 0.0, publish=None, **kw) -> bool:
    """Resolve `nid` in the background (refresh(**kw)); wait up to `wait` seconds for it. True when it finished
    within the wait. Never raises; does nothing with RB_METADATA=0."""
    if not enabled():
        return False
    try:
        ev = _worker.submit(store, nid, publish, kw)
        return ev.wait(wait) if wait > 0 else False
    except Exception as e:
        print(f"[research_brain] metadata lookup for {nid} not started: {e!r}")
        return False


def running() -> set[str]:
    return _worker.busy()


# ---------------------------------------------------------------------------------------------- PDFs
def ids_from_pdf_info(info: dict | None) -> dict:
    """Identifiers in a PDF's document info / XMP (pdftext.extract(info=...)): prism:doi, dc:identifier,
    crossmark DOI, /doi, an arXiv id in the subject."""
    out = {}
    for k in ("xmp_doi", "info_doi", "subject"):
        v = str((info or {}).get(k) or "")
        m = DOI_RE.search(v)
        if m and clean_doi(m.group(1)):
            out["doi"] = clean_doi(m.group(1))
            break
    for k in ("xmp_arxiv", "subject", "title"):
        m = re.search(r"arXiv:\s*(\d{4}\.\d{4,5})", str((info or {}).get(k) or ""), re.I)
        if m:
            out["arxiv"] = m.group(1)
            break
    return out


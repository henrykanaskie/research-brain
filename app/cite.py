"""Citation data: one CSL-JSON record per note, stable citation keys, and formatted citations/bibliographies.

    csl_for(note)            the note's bibliographic record as a CSL-JSON item (dict). A note's own `csl`
                             frontmatter is authoritative (normalised here); otherwise the record is built
                             from the older fields (title, authors "First Last", year, venue, doi, arxiv, url).
    assign_keys(notes)       {id: citekey} for a whole library, Better-BibTeX style (see below)
    ensure_keys(store, ids)  the same, and writes each key it handed out to the note's `citekey` property,
                             so a key never changes once it has been exported or copied
    format_items(...)        citations and bibliographies in a CSL style (citeproc-py, styles in app/csl)

Citation keys: a note's `citekey` property wins (imported from Zotero / Better BibTeX, or edited by hand).
Otherwise:  <first author's family name><year><first title word that isn't a stopword>, lower-case ASCII,
e.g. boeuf2018kinetic. Two notes with the same key get a, b, c... (smith2020, smith2020a, smith2020b) in a
fixed order (oldest note first, then id), never reusing a key another note already owns.

Formatting is offline: the styles are vendored in app/csl/styles (official CSL repository, CC BY-SA 3.0, see
app/csl/LICENSE.md) and users may add their own .csl files to <vault>/styles/. Style files are parsed with
DTDs, entities and network access refused (see load_user_style / SECURITY.md).
"""
from __future__ import annotations

import html
import re
import threading
import unicodedata
import warnings
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

CSL_DIR = Path(__file__).resolve().parent / "csl"
STYLES_DIR = CSL_DIR / "styles"
LOCALES_DIR = CSL_DIR / "locales"
MAX_STYLE_BYTES = 1 << 20  # 1 MB; the largest official independent styles are ~200 KB
DEFAULT_STYLE = "american-physics-society"

# ---------------------------------------------------------------------------------------------- CSL schema
CSL_TYPES = {
    "article", "article-journal", "article-magazine", "article-newspaper", "bill", "book", "broadcast",
    "chapter", "classic", "collection", "dataset", "document", "entry", "entry-dictionary",
    "entry-encyclopedia", "event", "figure", "graphic", "hearing", "interview", "legal_case", "legislation",
    "manuscript", "map", "motion_picture", "musical_score", "pamphlet", "paper-conference", "patent",
    "performance", "periodical", "personal_communication", "post", "post-weblog", "regulation", "report",
    "review", "review-book", "software", "song", "speech", "standard", "thesis", "treaty", "webpage",
}
TYPE_ALIASES = {
    # Crossref / Zotero / BibTeX words people (and resolvers) use for CSL types
    "journal-article": "article-journal", "journalarticle": "article-journal", "journal": "article-journal",
    "proceedings-article": "paper-conference", "conferencepaper": "paper-conference", "conference": "paper-conference",
    "inproceedings": "paper-conference", "book-chapter": "chapter", "booksection": "chapter",
    "incollection": "chapter", "book-section": "chapter", "posted-content": "article", "preprint": "article",
    "dissertation": "thesis", "phdthesis": "thesis", "mastersthesis": "thesis", "techreport": "report",
    "report-component": "report", "monograph": "book", "edited-book": "book", "reference-book": "book",
    "web": "webpage", "website": "webpage", "misc": "article", "computerprogram": "software",
}
NAME_VARS = ("author", "editor", "translator", "container-author", "collection-editor", "composer",
             "director", "interviewer", "illustrator", "original-author", "recipient", "reviewed-author",
             "editorial-director")
DATE_VARS = ("issued", "accessed", "original-date", "event-date", "submitted")
NAME_PARTS = ("family", "given", "literal", "suffix", "non-dropping-particle", "dropping-particle")
MAX_VALUE = 20000

ARXIV_ID = r"(\d{4}\.\d{4,5}(?:v\d+)?|[a-z\-]+(?:\.[A-Z]{2})?/\d{7}(?:v\d+)?)"
_ARXIV_IN_TEXT = re.compile(r"arxiv\s*[:.]?\s*(?:abs/)?" + ARXIV_ID, re.I)
_ARXIV_DOI = re.compile(r"10\.48550/arxiv\." + ARXIV_ID, re.I)
_ARXIV_URL = re.compile(r"arxiv\.org/(?:abs|pdf|html)/" + ARXIV_ID, re.I)

_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "di", "da", "dos", "du", "la", "le", "ten",
              "ter", "zu", "bin", "al", "el", "st."}
_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
_ORG_WORDS = re.compile(r"\b(collaboration|consortium|team|group|organi[sz]ation|institute|laboratory|"
                        r"university|council|committee|society|association|agency|project|working)\b", re.I)


def parse_name(s: str) -> dict:
    """"Jean-Pierre Boeuf", "Boeuf, Jean-Pierre", "Ludwig van Beethoven", "Martin Luther King Jr.",
    "ATLAS Collaboration" -> a CSL name {family, given, non-dropping-particle?, suffix?} or {literal}."""
    s = " ".join(str(s or "").split()).strip(" ,;")
    if not s:
        return {}
    if _ORG_WORDS.search(s) and "," not in s:
        return {"literal": s}
    suffix = ""
    if "," in s:
        parts = [p.strip() for p in s.split(",") if p.strip()]
        if len(parts) >= 3 and parts[2].lower() in _SUFFIXES:
            suffix = parts[2]
        if len(parts) >= 2 and parts[1].lower() in _SUFFIXES:  # "King Jr., Martin" is rare; "King, Jr." -> no given
            suffix, parts = parts[1], [parts[0]] + parts[2:]
        family = parts[0]
        given = parts[1] if len(parts) > 1 else ""
        out = _split_particle(family)
        if given:
            out["given"] = given
        if suffix:
            out["suffix"] = suffix
        return out
    toks = s.split(" ")
    if len(toks) > 1 and toks[-1].lower().strip(",") in _SUFFIXES:
        suffix = toks.pop().strip(",")
    if len(toks) == 1:
        out = {"family": toks[0]}
    else:
        i = len(toks) - 1  # family = last token, plus any lower-case particles just before it
        while i > 1 and toks[i - 1].lower() in _PARTICLES:
            i -= 1
        given, family = " ".join(toks[:i]), " ".join(toks[i:])
        out = _split_particle(family)
        if given:
            out["given"] = given
    if suffix:
        out["suffix"] = suffix
    return out


def _split_particle(family: str) -> dict:
    toks = family.split(" ")
    i = 0
    while i < len(toks) - 1 and toks[i].lower() in _PARTICLES:
        i += 1
    if i and toks[0][:1].islower():
        return {"family": " ".join(toks[i:]), "non-dropping-particle": " ".join(toks[:i])}
    return {"family": family}


def name_text(n: dict) -> str:
    """A CSL name back to "Given Family" (for search, display and the legacy authors list)."""
    if n.get("literal"):
        return n["literal"]
    fam = " ".join(x for x in (n.get("non-dropping-particle"), n.get("family")) if x)
    return " ".join(x for x in (n.get("given"), n.get("dropping-particle"), fam, n.get("suffix")) if x)


def _clean_str(v) -> str:
    return re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f]", "", str(v)).strip()[:MAX_VALUE]


def _norm_names(v) -> list[dict]:
    if isinstance(v, (str, dict)):
        v = [v]
    if not isinstance(v, (list, tuple)):
        return []
    out = []
    for x in v[:2000]:
        if isinstance(x, str):
            n = parse_name(x)
        elif isinstance(x, dict):
            n = {k: _clean_str(x[k]) for k in NAME_PARTS if x.get(k) not in (None, "")}
            if not n.get("family") and not n.get("literal") and n.get("given"):
                n = {"literal": n["given"]}
            if n.get("literal"):
                n = {"literal": n["literal"]}
        else:
            continue
        if n.get("family") or n.get("literal"):
            out.append(n)
    return out


def _int(x) -> int | None:
    try:
        return int(str(x).strip())
    except (TypeError, ValueError):
        return None


def _norm_date(v) -> dict | None:
    if v in (None, "", [], {}):
        return None
    if isinstance(v, int):
        return {"date-parts": [[v]]}
    if isinstance(v, str):
        m = re.match(r"^\s*(\d{4})(?:-(\d{1,2}))?(?:-(\d{1,2}))?", v)
        if m:
            return {"date-parts": [[int(g) for g in m.groups() if g]]}
        return {"literal": _clean_str(v)}
    if isinstance(v, dict):
        out: dict = {}
        parts = v.get("date-parts")
        if isinstance(parts, (list, tuple)):
            dp = []
            for p in parts[:2]:
                p = p if isinstance(p, (list, tuple)) else [p]
                nums = [_int(x) for x in p[:3]]
                nums = [x for x in nums if x is not None]
                if nums:
                    dp.append(nums)
            if dp:
                out["date-parts"] = dp
        for k in ("literal", "raw", "season"):
            if v.get(k) not in (None, ""):
                out[k] = _clean_str(v[k])
        if v.get("circa"):
            out["circa"] = True
        if "date-parts" not in out and out.get("raw"):
            d = _norm_date(out["raw"])
            if d and "date-parts" in d:
                out["date-parts"] = d["date-parts"]
        return out or None
    return None


def norm_type(t) -> str:
    t = str(t or "").strip()
    if t in CSL_TYPES:
        return t
    low = t.lower().replace("_", "-").replace(" ", "")
    if low in CSL_TYPES:
        return low
    return TYPE_ALIASES.get(low, TYPE_ALIASES.get(low.replace("-", ""), "article"))


def normalize_csl(item: dict, nid: str = "") -> dict:
    """A CSL-JSON item as read from frontmatter (hand-edited, from a resolver, from YAML) made well-typed:
    names are lists of {family, given, ...}, dates {date-parts: [[y, m, d]]}, everything else a string."""
    out: dict[str, Any] = {}
    for k, v in (item or {}).items():
        k = str(k)
        if k in ("id", "type") or v in (None, "", [], {}):
            continue
        if k in NAME_VARS:
            names = _norm_names(v)
            if names:
                out[k] = names
        elif k in DATE_VARS:
            d = _norm_date(v)
            if d:
                out[k] = d
        elif isinstance(v, (str, int, float)) and not isinstance(v, bool):
            s = _clean_str(v)
            if s:
                out[k] = s
        elif k in ("categories", "keyword") and isinstance(v, (list, tuple)):
            out[k] = ", ".join(_clean_str(x) for x in v if x)
    out["type"] = norm_type(item.get("type") if isinstance(item, dict) else "")
    out["id"] = nid or _clean_str((item or {}).get("id") or "")
    return out


# ---------------------------------------------------------------------------------------------- legacy -> CSL
_CONF = re.compile(r"\b(proc(eedings|\.)?|conference|conf\.|symposium|workshop|meeting|forum|congress|"
                   r"neurips|nips|icml|iclr|cvpr|iccv|eccv|aaai|ijcai|acl|emnlp|iros|icra|corl|rss)\b", re.I)
_THESIS = re.compile(r"\b(thesis|dissertation)\b", re.I)
_REPORT = re.compile(r"\b(technical report|tech\. rep\.?|report no\.?)\b", re.I)
_PREPRINT_VENUE = re.compile(r"^\s*(arxiv|biorxiv|medrxiv|chemrxiv|ssrn|preprint)\b", re.I)


def _extra(note: dict, key: str):
    if note.get(key) not in (None, "", {}):
        return note.get(key)
    return (note.get("_extra") or {}).get(key)


def legacy_csl(note: dict) -> dict:
    """CSL-JSON from the fields every note has had since v1 (no `csl` property)."""
    venue = str(note.get("venue") or "").strip()
    arxiv = str(note.get("arxiv") or "").strip()
    if note.get("type") != "paper":
        t = "article" if not str(note.get("url") or "").startswith("http") else "webpage"
    elif _THESIS.search(venue):
        t = "thesis"
    elif _REPORT.search(venue):
        t = "report"
    elif venue and _CONF.search(venue):
        t = "paper-conference"
    elif venue and not _PREPRINT_VENUE.match(venue):
        t = "article-journal"
    else:
        t = "article"  # preprints (CSL 1.0.1 has no "preprint" type; Zotero exports them as "article")
    item: dict[str, Any] = {"type": t, "title": str(note.get("title") or "").strip()}
    names = _norm_names(note.get("authors") or [])
    if names:
        item["author"] = names
    if note.get("year"):
        item["issued"] = {"date-parts": [[int(note["year"])]]}
    if venue and not _PREPRINT_VENUE.match(venue):
        if t == "thesis":
            item["publisher"] = venue
        else:
            item["container-title"] = venue
    if note.get("doi"):
        item["DOI"] = str(note["doi"]).strip()
    url = str(note.get("url") or "").strip()
    if arxiv:
        item["number"] = arxiv
        item["archive"] = "arXiv"
        if not url:
            url = f"https://arxiv.org/abs/{arxiv}"
    if url.startswith(("http://", "https://")):
        item["URL"] = url
    if note.get("abstract"):
        item["abstract"] = str(note["abstract"]).strip()[:MAX_VALUE]
    return normalize_csl(item, note.get("id", ""))


def csl_for(note: dict) -> dict:
    """The note's CSL-JSON item, `id` = the note id. Its `csl` property (written by the metadata
    resolvers or by hand) is authoritative; without one, the record is built from the legacy fields."""
    raw = _extra(note, "csl")
    if isinstance(raw, dict) and raw:
        item = normalize_csl(raw, note.get("id", ""))
        if not item.get("title") and note.get("title"):
            item["title"] = str(note["title"])
        return item
    return legacy_csl(note)


def arxiv_id(item: dict) -> str:
    """The arXiv id in a CSL item (number with archive arXiv, the DOI, URL or note), without a version."""
    cands = []
    if re.search(r"arxiv", str(item.get("archive", "")) + str(item.get("publisher", "")) +
                 str(item.get("container-title", "")), re.I) or str(item.get("number", "")).lower().startswith("arxiv"):
        cands.append(re.sub(r"^arxiv\s*:\s*", "", str(item.get("number", "")), flags=re.I))
    for k, rx in (("DOI", _ARXIV_DOI), ("URL", _ARXIV_URL), ("note", _ARXIV_IN_TEXT), ("number", _ARXIV_IN_TEXT)):
        m = rx.search(str(item.get(k, "")))
        if m:
            cands.append(m.group(1))
    for c in cands:
        m = re.fullmatch(ARXIV_ID, c.strip(), re.I)
        if m:
            return re.sub(r"v\d+$", "", m.group(1))
    return ""


def year_of(item: dict) -> int | None:
    d = item.get("issued") or {}
    try:
        return int(d["date-parts"][0][0])
    except (KeyError, IndexError, TypeError, ValueError):
        m = re.search(r"\b(1[5-9]\d\d|20\d\d)\b", str(d.get("literal") or d.get("raw") or ""))
        return int(m.group(1)) if m else None


# ---------------------------------------------------------------------------------------------- citation keys
# Better BibTeX's skip words (title words that never make a key)
STOPWORDS = set("""a ab aboard about above across after against al along amid among an and anti around as at
before behind below beneath beside besides between beyond but by d da das de del dell dello dei degli della dell
delle dem den der des despite die do down du during ein eine einem einen einer eines el en et except for from gli
i il in inside into is l la las le les like lo los near nor of off on onto or over past per plus round save
since so some sur than the through to toward towards un una unas under underneath une unlike uno unos until up
upon versus via von while with within without yet zu zum""".split())
KEY_RE = re.compile(r"^[A-Za-z0-9_:.+/\-]{1,100}$")


def ascii_fold(s: str) -> str:
    s = str(s or "").replace("ß", "ss").replace("æ", "ae").replace("Æ", "AE").replace("ø", "o").replace("Ø", "O") \
        .replace("œ", "oe").replace("Œ", "OE").replace("ł", "l").replace("Ł", "L").replace("đ", "d").replace("þ", "th")
    return unicodedata.normalize("NFKD", s).encode("ascii", "ignore").decode()


def _word(s: str) -> str:
    return re.sub(r"[^a-z0-9]", "", ascii_fold(s).lower())


def base_key(item: dict) -> str:
    """authoryearword (no disambiguation letter): boeuf2018kinetic."""
    auth = ""
    for var in ("author", "editor"):
        names = item.get(var) or []
        if names:
            n = names[0]
            if n.get("literal"):
                auth = _word(n["literal"].split(" ")[0])
            else:
                auth = _word((n.get("non-dropping-particle") or "") + (n.get("family") or ""))
            break
    year = year_of(item)
    word = ""
    title = re.sub(r"<[^>]+>|\$[^$]*\$", " ", str(item.get("title") or ""))
    for w in re.split(r"[\s\-–—:;,./()\[\]{}\"'“”‘’!?]+", title):
        lw = _word(w)
        if lw and lw not in STOPWORDS and not lw.isdigit():
            word = lw
            break
    key = f"{auth}{year or ''}{word}"
    return key or "ref"


def stored_key(note: dict) -> str:
    """The note's own `citekey` (persisted earlier, imported from Zotero, or typed by the user), cleaned."""
    k = _extra(note, "citekey")
    if not isinstance(k, str):
        k = "" if k is None else str(k)
    k = k.strip()
    if not k:
        return ""
    if KEY_RE.match(k):
        return k
    k = re.sub(r"[^A-Za-z0-9_:.+/\-]", "", ascii_fold(k))[:100]
    return k


def citable(note: dict) -> bool:
    return note.get("type") != "fragment"


def _suffix(i: int) -> str:
    """0 -> "", 1 -> a, ... 26 -> z, 27 -> aa ..."""
    if i == 0:
        return ""
    s, i = "", i - 1
    while True:
        s = chr(ord("a") + i % 26) + s
        i = i // 26 - 1
        if i < 0:
            return s


def assign_keys(notes: list[dict]) -> dict[str, tuple[str, bool]]:
    """{id: (key, persisted)} for every citable note. Persisted keys are honoured (the oldest note wins a
    duplicate); the rest get base_key + a/b/c..., deterministically, never colliding with a taken key."""
    order = sorted((n for n in notes if citable(n)), key=lambda n: (str(n.get("created") or ""), n["id"]))
    out: dict[str, tuple[str, bool]] = {}
    taken: set[str] = set()
    for n in order:
        k = stored_key(n)
        if k and k.lower() not in taken:
            out[n["id"]] = (k, True)
            taken.add(k.lower())
    for n in order:
        if n["id"] in out:
            continue
        base = base_key(csl_for(n))
        i = 0
        while (base + _suffix(i)).lower() in taken:
            i += 1
        out[n["id"]] = (base + _suffix(i), False)
        taken.add((base + _suffix(i)).lower())
    return out


def set_citekey(store, nid: str, key: str) -> dict:
    """Write `citekey` into the note's frontmatter without bumping its `updated` time."""
    from .store import FIELDS_DEFAULTS
    with store.lock:
        note = store.get(nid)
        if note.get("_yaml_error"):
            return note
        if "citekey" in FIELDS_DEFAULTS:
            note["citekey"] = key
        else:
            note.setdefault("_extra", {})["citekey"] = key
        try:
            return store.save(note, touch=False)
        except TypeError:  # an older Store.save without `touch`
            return store.save(note)


def ensure_keys(store, ids: list[str], notes: list[dict] | None = None) -> dict[str, str]:
    """Keys for `ids`, persisting any that were only computed so far. Ids that aren't notes are skipped."""
    with store.lock:
        notes = store.all() if notes is None else notes
        keys = assign_keys(notes)
        out = {}
        for nid in ids:
            if nid not in keys:
                continue
            k, persisted = keys[nid]
            if not persisted:
                try:
                    set_citekey(store, nid, k)
                except (KeyError, OSError) as e:
                    print(f"[research_brain] couldn't save the citation key of {nid}: {e!r}")
            out[nid] = k
        return out


# ---------------------------------------------------------------------------------------------- styles
class StyleError(ValueError):
    """A style can't be read, or citeproc-py can't format with it."""


_style_cache: dict[tuple, Any] = {}
_fmt_lock = threading.RLock()  # citeproc style objects keep the formatter on themselves: one at a time


def _style_info(raw: bytes) -> dict:
    """title, short title, class, numeric?, dependent? from a style's <info>, read with safe parsing."""
    root = safe_xml(raw)
    ns = {"cs": "http://purl.org/net/xbiblio/csl"}
    if root.tag != "{http://purl.org/net/xbiblio/csl}style":
        raise StyleError("This isn't a CSL style (the root element must be <style> in the CSL namespace).")
    title = root.findtext("cs:info/cs:title", default="", namespaces=ns).strip()
    short = root.findtext("cs:info/cs:title-short", default="", namespaces=ns).strip()
    parent = root.find("cs:info/cs:link[@rel='independent-parent']", ns)
    fmt = root.find("cs:info/cs:category[@citation-format]", ns)
    has_bib = root.find("cs:bibliography", ns) is not None
    return {"title": title, "short": short, "class": root.get("class", ""),
            "format": fmt.get("citation-format") if fmt is not None else "",
            "dependent": parent is not None and root.find("cs:citation", ns) is None,
            "parent": parent.get("href") if parent is not None else "", "has_bibliography": has_bib}


def safe_xml(raw: bytes):
    """Parse XML with no DTD, no entities, no network (defusedxml), returning an ElementTree element.
    Raises StyleError for anything else (XXE, billion laughs, malformed)."""
    if len(raw) > MAX_STYLE_BYTES:
        raise StyleError(f"The style file is larger than {MAX_STYLE_BYTES >> 20} MB.")
    try:
        from defusedxml import ElementTree as DET
        from defusedxml.common import DefusedXmlException
    except ImportError:  # pragma: no cover - fallback with lxml, entities off
        from lxml import etree
        if re.search(rb"<!DOCTYPE|<!ENTITY", raw, re.I):
            raise StyleError("Style files may not contain a DOCTYPE or entity declarations.")
        parser = etree.XMLParser(resolve_entities=False, no_network=True, load_dtd=False, huge_tree=False)
        try:
            return etree.fromstring(raw, parser)
        except etree.XMLSyntaxError as e:
            raise StyleError(f"The style file isn't valid XML: {e}") from None
    try:
        return DET.fromstring(raw, forbid_dtd=True, forbid_entities=True, forbid_external=True)
    except DefusedXmlException:
        raise StyleError("Style files may not contain a DOCTYPE or entity declarations.") from None
    except Exception as e:  # xml.etree.ElementTree.ParseError
        raise StyleError(f"The style file isn't valid XML: {e}") from None


def user_styles_dir(vault: Path | None) -> Path | None:
    return Path(vault) / "styles" if vault else None


def _style_files(vault: Path | None) -> dict[str, tuple[Path, str]]:
    """style id -> (path, "builtin" | "user"). A user style with a built-in's id doesn't replace it."""
    out: dict[str, tuple[Path, str]] = {}
    for p in sorted(STYLES_DIR.glob("*.csl")):
        out[p.stem] = (p, "builtin")
    ud = user_styles_dir(vault)
    if ud and ud.is_dir():
        for p in sorted(ud.glob("*.csl")):
            sid = "user:" + p.stem
            if re.fullmatch(r"user:[A-Za-z0-9._-]{1,100}", sid) and p.is_file() and not p.is_symlink():
                out[sid] = (p, "user")
    return out


def list_styles(vault: Path | None = None) -> list[dict]:
    out = []
    for sid, (p, kind) in _style_files(vault).items():
        try:
            raw = p.read_bytes()
            info = _style_info(raw)
            err = "" if not info["dependent"] else (
                "This is a dependent style (it only points to another style). Add the independent parent style instead.")
        except (OSError, StyleError) as e:
            info, err = {"title": p.stem, "short": "", "class": "", "format": "", "has_bibliography": False}, str(e)
        out.append({"id": sid, "title": info["title"] or p.stem, "short": info.get("short", ""), "kind": kind,
                    "class": info.get("class", ""), "format": info.get("format", ""),
                    "bibliography": bool(info.get("has_bibliography")), "error": err})
    out.sort(key=lambda s: (s["kind"] != "builtin", s["title"].lower()))
    return out


def save_user_style(vault: Path, filename: str, raw: bytes) -> dict:
    """Validate a .csl file and write it to <vault>/styles/. Returns its list_styles() entry."""
    stem = re.sub(r"\.csl$", "", Path(str(filename or "")).name, flags=re.I)
    stem = re.sub(r"[^A-Za-z0-9._-]+", "-", stem).strip("-.")[:100]
    if not stem:
        raise StyleError("Give the style file a name, e.g. journal-of-plasma-physics.csl.")
    info = _style_info(raw)
    if info["dependent"]:
        raise StyleError("This is a dependent style: it only points to another style "
                         f"({info['parent'] or 'its parent'}). Download and add that independent style instead.")
    _load_style(raw, "upload")  # citeproc-py must be able to read it
    d = user_styles_dir(vault)
    d.mkdir(parents=True, exist_ok=True)
    dest = d / f"{stem}.csl"
    if dest.is_symlink():
        raise StyleError("A link with that name is in the styles folder; remove it first.")
    tmp = d / f".{stem}.csl.tmp"
    tmp.write_bytes(raw)
    tmp.replace(dest)
    return next(s for s in list_styles(vault) if s["id"] == "user:" + stem)


def delete_user_style(vault: Path, sid: str) -> None:
    files = _style_files(vault)
    if sid not in files or files[sid][1] != "user":
        raise KeyError(sid)
    files[sid][0].unlink()


def _patch_locales():
    """citeproc-py reads locales from its own package; use the vendored en-US when it is there."""
    import citeproc.frontend as fe
    if (LOCALES_DIR / "locales-en-US.xml").exists() and getattr(fe, "LOCALES_PATH", None) != str(LOCALES_DIR):
        fe.LOCALES_PATH = str(LOCALES_DIR)


def _load_style(raw: bytes, label: str):
    safe_xml(raw)  # refuse DTDs/entities before lxml sees the bytes
    try:
        from citeproc import CitationStylesStyle
    except ImportError:
        raise StyleError("Formatting needs citeproc-py (pip install citeproc-py).") from None
    _patch_locales()
    import io
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            return CitationStylesStyle(io.BytesIO(raw), locale="en-US", validate=False)
    except Exception as e:
        raise StyleError(f"citeproc-py can't read the style {label}: {e}") from None


def get_style(sid: str, vault: Path | None = None):
    files = _style_files(vault)
    if sid not in files:
        raise KeyError(sid)
    p, _ = files[sid]
    st = p.stat()
    ck = (str(p), st.st_mtime_ns, st.st_size)
    with _fmt_lock:
        if ck not in _style_cache:
            raw = p.read_bytes()
            info = _style_info(raw)
            if info["dependent"]:
                raise StyleError("This is a dependent style. Add its independent parent style instead.")
            if len(_style_cache) > 32:
                _style_cache.clear()
            _style_cache[ck] = (_load_style(raw, sid), info)
        return _style_cache[ck]


# ---------------------------------------------------------------------------------------------- formatting
_CITEPROC_VARS: set[str] | None = None


def _citeproc_vars() -> set[str]:
    global _CITEPROC_VARS
    if _CITEPROC_VARS is None:
        from citeproc import VARIABLES
        _CITEPROC_VARS = {v.replace("_", "-") for v in VARIABLES} | {"shortTitle"}
    return _CITEPROC_VARS


def for_citeproc(item: dict) -> dict:
    """A CSL item adjusted for citeproc-py: only variables it knows, arXiv as Zotero exports it
    (number "arXiv:<id>", publisher "arXiv"), CSL 1.0.2 names mapped to 1.0.1 ones."""
    it = dict(item)
    if it.get("event-title") and not it.get("event"):
        it["event"] = it["event-title"]
    ax = arxiv_id(it)
    if ax and it.get("type") in ("article", "report", "manuscript", "document") and not it.get("container-title"):
        it["number"] = f'<span class="nocase">arXiv</span>:{ax}'  # keeps "arXiv" from becoming "ArXiv"
        it.setdefault("publisher", '<span class="nocase">arXiv</span>')
        if str(it.get("archive", "")).lower() == "arxiv":
            it.pop("archive")
        if not it.get("URL"):
            it["URL"] = f"https://arxiv.org/abs/{ax}"
    if it.get("type") not in CSL_TYPES or it.get("type") in ("document", "standard", "software", "event"):
        it["type"] = {"software": "book", "standard": "report", "event": "speech"}.get(it.get("type"), "article")
    allowed = _citeproc_vars()
    out = {"id": it["id"], "type": it["type"]}
    for k, v in it.items():
        if k in ("id", "type") or k not in allowed:
            continue
        if k in NAME_VARS:
            v = [{"literal": n["literal"]} if n.get("literal") else {p: n[p] for p in NAME_PARTS if n.get(p)}
                 for n in v if n.get("family") or n.get("literal")]
        if k in DATE_VARS and "date-parts" not in v and "literal" not in v:
            continue
        out[k] = v
    return out


_LABEL_GLUE = re.compile(r"^((?:<[^>]+>)*)(\[\d+\]|\d+\.)(?=[^\s<])")


def _tidy_entry(s: str) -> str:
    """citeproc-py drops the left-margin column of numeric styles: "[1]J. Doe" -> "[1] J. Doe"."""
    s = re.sub(r"\s+", " ", str(s)).strip()
    return _LABEL_GLUE.sub(lambda m: m.group(1) + m.group(2) + " ", s)


class _Sanitizer(HTMLParser):
    """Keep a small set of inline tags from citeproc's HTML; everything else becomes text."""
    TAGS = {"i", "b", "em", "strong", "sup", "sub", "span", "br"}
    STYLES = {"font-variant:small-caps", "font-style:normal", "font-weight:normal", "font-style:italic",
              "font-weight:bold", "text-decoration:underline", "font-variant:normal", "vertical-align:baseline"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.stack: list[str] = []

    def handle_starttag(self, tag, attrs):
        if tag not in self.TAGS:
            return
        if tag == "br":
            self.out.append("<br>")
            return
        attr = ""
        if tag == "span":
            style = "".join((dict(attrs).get("style") or "").split()).rstrip(";").lower()
            attr = f' style="{style}"' if style in self.STYLES else ""
        self.out.append(f"<{tag}{attr}>")
        self.stack.append(tag)

    def handle_endtag(self, tag):
        if tag in self.stack:
            while self.stack:
                t = self.stack.pop()
                self.out.append(f"</{t}>")
                if t == tag:
                    break

    def handle_data(self, data):
        self.out.append(html.escape(data, quote=False))

    def result(self) -> str:
        while self.stack:
            self.out.append(f"</{self.stack.pop()}>")
        return "".join(self.out)


def sanitize_html(s: str) -> str:
    p = _Sanitizer()
    p.feed(str(s))
    p.close()
    return p.result()


def html_to_text(s: str) -> str:
    return html.unescape(re.sub(r"<[^>]+>", "", s))


def format_items(items: list[dict], style: str = DEFAULT_STYLE, mode: str = "bibliography",
                 vault: Path | None = None) -> dict:
    """Format CSL items. mode "citation" = one in-text citation for all items ("[1-3]", "(Doe, 2024; ...)");
    "bibliography" = the reference list. Returns {html, text, entries: [{id, html, text}], style}.
    Raises StyleError if the style can't be used; KeyError for an unknown style id."""
    if mode not in ("citation", "bibliography"):
        raise ValueError('mode must be "citation" or "bibliography"')
    if not items:
        return {"html": "", "text": "", "entries": [], "style": style}
    sty, info = get_style(style, vault)
    from citeproc import Citation, CitationItem, CitationStylesBibliography, formatter
    from citeproc.source.json import CiteProcJSON
    data = [for_citeproc(it) for it in items]
    ids = [d["id"].lower() for d in data]
    with _fmt_lock, warnings.catch_warnings():
        warnings.simplefilter("ignore")
        try:
            src = CiteProcJSON(data)
            bib = CitationStylesBibliography(sty, src, formatter.html)
            if mode == "citation":
                cit = Citation([CitationItem(i) for i in ids])
                bib.register(cit)
                bib.sort()
                h = _tidy_entry(str(bib.cite(cit, lambda _x: None)))
                h = re.sub(r"\s+(?=[,;\]\)])", "", h)  # "[1] , [2]" -> "[1], [2]"
                h = sanitize_html(h)
                return {"html": h, "text": html_to_text(h), "entries": [], "style": style}
            if sty.root.bibliography is None:
                raise StyleError("This style has no bibliography (it only formats citations or notes).")
            for i in ids:  # one citation per item, in the order given (numeric styles number them so)
                bib.register(Citation([CitationItem(i)]))
            bib.sort()
            rendered = [sanitize_html(_tidy_entry(str(e))) for e in bib.bibliography()]
            order = list(bib.keys)
        except StyleError:
            raise
        except Exception as e:  # citeproc-py doesn't support everything in CSL 1.0.2
            raise StyleError(f"citeproc-py couldn't format with this style ({type(e).__name__}: {e}). "
                             "Pick another style; the exports (.bib, RIS, CSL-JSON) still work.") from None
    by_low = {d["id"].lower(): d["id"] for d in data}
    entries = [{"id": by_low.get(k, k), "html": h, "text": html_to_text(h)} for k, h in zip(order, rendered)]
    body = "".join(f'<div class="csl-entry">{e["html"]}</div>' for e in entries)
    return {"html": f'<div class="csl-bib-body">{body}</div>', "text": "\n".join(e["text"] for e in entries),
            "entries": entries, "style": style}

"""BibTeX, BibLaTeX, RIS and CSL-JSON from CSL-JSON items (app/cite.py: csl_for).

    to_bibtex(entries, flavor="bibtex"|"biblatex")   entries = [(citekey, csl_item)]
    to_ris(entries)
    to_csl_json(entries)                             CSL-JSON with id = citekey (what pandoc expects)

BibTeX ("bibtex" flavor) is ASCII: accented letters become LaTeX accents ({\\'e}), Greek letters math.
BibLaTeX keeps UTF-8 (biber reads it). In both, titles keep their capitals where they matter: acronyms,
words with inner capitals (McDonald, LaTeX), chemical formulas and, in sentence-case titles, proper nouns
are wrapped in braces so a style that lower-cases titles leaves them alone. $math$ is passed through.
"""
from __future__ import annotations

import json
import re
import unicodedata

from .cite import arxiv_id, year_of

HEADER = "% Written by research brain"


# ---------------------------------------------------------------------------------------------- LaTeX text
_ACCENTS = {"́": "'", "̀": "`", "̂": "^", "̈": '"', "̃": "~", "̄": "=",
            "̇": ".", "̧": "c", "̌": "v", "̆": "u", "̋": "H", "̨": "k",
            "̊": "r", "̣": "d", "̱": "b"}
_LETTER_ACCENT = {"c", "v", "u", "H", "k", "r", "d", "b"}  # need {\c{c}} rather than {\'e}
_SPECIAL = {"ß": r"{\ss}", "æ": r"{\ae}", "Æ": r"{\AE}", "œ": r"{\oe}", "Œ": r"{\OE}", "ø": r"{\o}",
            "Ø": r"{\O}", "ł": r"{\l}", "Ł": r"{\L}", "å": r"{\aa}", "Å": r"{\AA}", "ı": r"{\i}", "đ": r"{\dj}",
            "Đ": r"{\DJ}", "þ": r"{\th}", "Þ": r"{\TH}", "ð": r"{\dh}", "Ð": r"{\DH}",
            "–": "--", "—": "---", "‘": "`", "’": "'", "“": "``", "”": "''", "…": r"{\ldots}", " ": "~",
            "×": r"$\times$", "±": r"$\pm$", "°": r"{\textdegree}", "µ": r"$\mu$", "Å": r"{\AA}", "€": r"{\texteuro}",
            "§": r"{\S}", "©": r"{\textcopyright}", "®": r"{\textregistered}", "™": r"{\texttrademark}",
            "≤": r"$\leq$", "≥": r"$\geq$", "≈": r"$\approx$", "→": r"$\rightarrow$", "∞": r"$\infty$",
            "·": r"$\cdot$", "′": "'", "−": "-", " ": r"\,", "¿": r"{?`}", "¡": r"{!`}"}
_GREEK = {"α": "alpha", "β": "beta", "γ": "gamma", "δ": "delta", "ε": "epsilon", "ζ": "zeta", "η": "eta",
          "θ": "theta", "ι": "iota", "κ": "kappa", "λ": "lambda", "μ": "mu", "ν": "nu", "ξ": "xi", "π": "pi",
          "ρ": "rho", "σ": "sigma", "τ": "tau", "υ": "upsilon", "φ": "phi", "χ": "chi", "ψ": "psi", "ω": "omega",
          "Γ": "Gamma", "Δ": "Delta", "Θ": "Theta", "Λ": "Lambda", "Ξ": "Xi", "Π": "Pi", "Σ": "Sigma",
          "Υ": "Upsilon", "Φ": "Phi", "Ψ": "Psi", "Ω": "Omega", "ϕ": "varphi", "ϵ": "varepsilon"}
_SUPS = {"⁰": "0", "¹": "1", "²": "2", "³": "3", "⁴": "4", "⁵": "5", "⁶": "6", "⁷": "7", "⁸": "8", "⁹": "9",
         "⁺": "+", "⁻": "-"}
_SUBS = {"₀": "0", "₁": "1", "₂": "2", "₃": "3", "₄": "4", "₅": "5", "₆": "6", "₇": "7", "₈": "8", "₉": "9"}
_ESC = {"\\": r"\textbackslash{}", "{": r"\{", "}": r"\}", "&": r"\&", "%": r"\%", "$": r"\$", "#": r"\#",
        "_": r"\_", "~": r"\textasciitilde{}", "^": r"\textasciicircum{}"}


def _to_ascii_latex(ch: str) -> str:
    if ch in _SPECIAL:
        return _SPECIAL[ch]
    if ch in _GREEK:
        return f"$\\{_GREEK[ch]}$"
    if ch in _SUPS:
        return f"\\textsuperscript{{{_SUPS[ch]}}}"
    if ch in _SUBS:
        return f"\\textsubscript{{{_SUBS[ch]}}}"
    d = unicodedata.normalize("NFD", ch)
    if len(d) >= 2 and d[0].isascii() and all(c in _ACCENTS for c in d[1:]):
        base = d[0]
        out = base if base not in "ij" else "\\" + base
        for mark in d[1:]:
            a = _ACCENTS[mark]
            out = f"\\{a}{{{out}}}" if a in _LETTER_ACCENT else f"\\{a}{out}"
        return "{" + out + "}"
    return ch  # unknown: leave it (bibtex passes bytes through)


def _plain_escape(s: str, ascii_only: bool) -> str:
    out = []
    for ch in s:
        if ch in _ESC:
            out.append(_ESC[ch])
        elif ord(ch) < 128:
            out.append(ch)
        elif ascii_only:
            out.append(_to_ascii_latex(ch))
        elif ch in (" ",):
            out.append("~")
        else:
            out.append(ch)
    return "".join(out)


_MATH = re.compile(r"(?<!\\)\$[^$\n]+?(?<!\\)\$")
_TAG = re.compile(r"<(/?)(i|b|em|strong|sup|sub|sc|span)(?:\s+class=\"nocase\")?\s*>", re.I)


def latex_text(s: str, ascii_only: bool, protect=None) -> str:
    """Escape text for a BibTeX field. Keeps $math$, turns CSL rich text (<i>, <b>, <sup>, <sub>,
    <span class="nocase">) into LaTeX; `protect(word) -> bool` braces words."""
    s = str(s or "")
    parts, pos = [], 0
    for m in _MATH.finditer(s):
        parts.append(("t", s[pos:m.start()]))
        parts.append(("m", m.group(0)))
        pos = m.end()
    parts.append(("t", s[pos:]))
    out = []
    for kind, chunk in parts:
        if kind == "m":
            out.append(chunk)
            continue
        out.append(_rich(chunk, ascii_only, protect))
    return "".join(out)


_OPEN = {"i": r"\textit{", "em": r"\emph{", "b": r"\textbf{", "strong": r"\textbf{", "sup": r"\textsuperscript{",
         "sub": r"\textsubscript{", "sc": r"\textsc{", "nocase": "{", "span": "{"}


def _rich(s: str, ascii_only: bool, protect) -> str:
    out, pos, depth = [], 0, 0
    for m in _TAG.finditer(s):
        out.append(_words(s[pos:m.start()], ascii_only, protect if depth == 0 else None))
        closing, tag = m.group(1), m.group(2).lower()
        if closing:
            if depth:
                out.append("}")
                depth -= 1
        else:
            out.append(_OPEN.get(tag, "{"))
            depth += 1
        pos = m.end()
    out.append(_words(s[pos:], ascii_only, protect if depth == 0 else None))
    out.append("}" * depth)
    return "".join(out)


def _words(s: str, ascii_only: bool, protect) -> str:
    if not protect:
        return _plain_escape(s, ascii_only)
    out = []
    for tok in re.split(r"(\s+)", s):
        if tok and not tok.isspace() and protect(tok):
            m = re.match(r"^([\"'(\[“‘]*)(.*?)([\"').,:;!?\]”’]*)$", tok)
            lead, core, trail = m.groups() if m else ("", tok, "")
            out.append(_plain_escape(lead, ascii_only) + "{" + _plain_escape(core, ascii_only) + "}"
                       + _plain_escape(trail, ascii_only))
        else:
            out.append(_plain_escape(tok, ascii_only))
    return "".join(out)


def _title_protector(title: str):
    """Which words of a title need braces. Sentence-case titles: every capitalised word after the first
    (a proper noun or an acronym); Title Case titles: only acronyms, inner capitals, formulas."""
    plain = _TAG.sub(" ", _MATH.sub(" ", title))
    words = [w for w in re.split(r"[\s\-–—/]+", plain) if re.search(r"[A-Za-z]", w)]
    small = {"a", "an", "the", "and", "or", "of", "in", "on", "for", "to", "with", "by", "at", "from", "via",
             "vs", "vs.", "as", "into", "is", "are", "its", "via"}
    content = [w for w in words[1:] if w.lower().strip(".,:;()") not in small]
    capped = sum(1 for w in content if w[:1].isupper())
    sentence_case = not content or capped / len(content) < 0.5
    first = words[0] if words else ""
    seen_first = [False]

    def protect(tok: str) -> bool:
        core = re.sub(r"^[\"'(\[“‘]+|[\"').,:;!?\]”’]+$", "", tok)
        letters = re.sub(r"[^A-Za-z]", "", core)
        if not letters:
            return False
        is_first = not seen_first[0] and core and first and first.startswith(core[:1])
        seen_first[0] = True
        if any(c.isupper() for c in core[1:]):
            return True  # McDonald, LaTeX, pH, DNA, CO2, TiO2, H-mode
        if re.search(r"\d", core) and re.search(r"[A-Z]", core):
            return True  # 2D, H2O
        if sentence_case and core[:1].isupper() and not is_first:
            return True
        if len(letters) == 1 and core.isupper() and not is_first and core not in ("A", "I"):
            return True  # single capital letters: "the E field"
        return False
    return protect


def _verbatim(s: str) -> str:
    """url / doi / eprint: verbatim fields; only \\ { } % # need care."""
    s = str(s or "").strip()
    s = re.sub(r"[{}\\\s]", "", s)
    return s.replace("%", r"\%").replace("#", r"\#")


# ---------------------------------------------------------------------------------------------- names
def _bib_name(n: dict, ascii_only: bool) -> str:
    if n.get("literal"):
        return "{" + latex_text(n["literal"], ascii_only) + "}"
    fam = " ".join(x for x in (n.get("non-dropping-particle"), n.get("family")) if x)
    fam = latex_text(fam, ascii_only)
    if " " in (n.get("family") or "") and not n.get("non-dropping-particle"):
        fam = "{" + fam + "}"  # a two-word family name ("Garcia Lopez") must stay together
    given = latex_text(" ".join(x for x in (n.get("given"), n.get("dropping-particle")) if x), ascii_only)
    parts = [fam]
    if n.get("suffix"):
        parts.append(latex_text(n["suffix"], ascii_only))
    if given:
        parts.append(given)
    return ", ".join(parts)


def _names(names: list[dict], ascii_only: bool) -> str:
    return " and ".join(_bib_name(n, ascii_only) for n in names if n.get("family") or n.get("literal"))


# ---------------------------------------------------------------------------------------------- types
_MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def _is_masters(item: dict) -> bool:
    return bool(re.search(r"master|\bm\.?\s?s\.?c?\b|\bm\.?a\.?\b|\bmeng\b|\bmaîtrise", str(item.get("genre", "")), re.I))


def entry_type(item: dict, flavor: str) -> str:
    t = item.get("type", "article")
    bl = flavor == "biblatex"
    if t in ("article-journal", "article-magazine", "article-newspaper", "review", "review-book"):
        return "article"
    if t == "paper-conference":
        return "inproceedings"
    if t in ("book", "classic", "collection", "periodical"):
        return "book"
    if t in ("chapter", "entry", "entry-dictionary", "entry-encyclopedia"):
        return "incollection"
    if t == "thesis":
        return "mastersthesis" if _is_masters(item) else "phdthesis"
    if t == "report":
        return "report" if bl else "techreport"
    if t in ("webpage", "post", "post-weblog") and bl:
        return "online"
    if t == "dataset" and bl:
        return "dataset"
    if t == "software" and bl:
        return "software"
    if t == "patent" and bl:
        return "patent"
    if t == "manuscript":
        return "unpublished"
    if t == "pamphlet":
        return "booklet"
    return "misc"


def _pages(p: str) -> str:
    return re.sub(r"\s*[-–—]+\s*", "--", str(p or "").strip())


def _date_parts(item: dict, var: str = "issued") -> list[int]:
    d = item.get(var) or {}
    try:
        return [int(x) for x in d["date-parts"][0]][:3]
    except (KeyError, IndexError, TypeError, ValueError):
        y = year_of(item) if var == "issued" else None
        return [y] if y else []


def bib_fields(item: dict, flavor: str = "bibtex") -> list[tuple[str, str, str]]:
    """[(field, value, kind)] in a fixed order. kind: "text" (escaped), "raw" (already LaTeX), "verb"."""
    bl = flavor == "biblatex"
    ascii_only = not bl
    et = entry_type(item, flavor)
    f: list[tuple[str, str, str]] = []

    def add(name, value, kind="raw"):
        if value not in (None, ""):
            f.append((name, value, kind))

    title = str(item.get("title") or "")
    add("title", latex_text(title, ascii_only, _title_protector(title)))
    if item.get("author"):
        add("author", _names(item["author"], ascii_only))
    if item.get("editor"):
        add("editor", _names(item["editor"], ascii_only))
    if item.get("translator") and bl:
        add("translator", _names(item["translator"], ascii_only))
    cont = str(item.get("container-title") or "")
    if cont:
        if et == "article":
            add("journaltitle" if bl else "journal", latex_text(cont, ascii_only))
            if bl and item.get("container-title-short"):
                add("shortjournal", latex_text(item["container-title-short"], ascii_only))
        elif et in ("inproceedings", "incollection"):
            add("booktitle", latex_text(cont, ascii_only))
        elif et == "online":
            add("organization", latex_text(cont, ascii_only))
        elif et == "misc":
            add("howpublished", latex_text(cont, ascii_only))
        elif et == "book":
            add("series", latex_text(cont, ascii_only))
        else:
            add("booktitle", latex_text(cont, ascii_only))
    if item.get("collection-title") and not any(k == "series" for k, *_ in f):
        add("series", latex_text(item["collection-title"], ascii_only))
    if item.get("event") or item.get("event-title"):
        if bl and et == "inproceedings":
            add("eventtitle", latex_text(item.get("event-title") or item.get("event"), ascii_only))
    pub = str(item.get("publisher") or "")
    ax = arxiv_id(item)
    if ax and re.fullmatch(r"arxiv", pub.strip(), re.I):
        pub = ""  # arXiv is the eprint archive, not a publisher
    if et in ("phdthesis", "mastersthesis"):
        add("institution" if bl else "school", latex_text(pub, ascii_only))
    elif et in ("techreport", "report"):
        add("institution", latex_text(pub, ascii_only))
        if bl:
            add("type", "techreport")
    else:
        add("publisher", latex_text(pub, ascii_only))
    place = item.get("publisher-place") or item.get("event-place") or ""
    add("location" if bl else "address", latex_text(place, ascii_only))
    if item.get("edition"):
        add("edition", latex_text(item["edition"], ascii_only))
    add("volume", latex_text(item.get("volume", ""), ascii_only))
    if et in ("techreport", "report"):
        add("number", latex_text(item.get("number", ""), ascii_only) if not ax else "")
    else:
        add("number", latex_text(item.get("issue", ""), ascii_only))
    add("pages", latex_text(_pages(item.get("page", "")), ascii_only))
    dp = _date_parts(item)
    if dp:
        if bl:
            add("date", "-".join(f"{x:04d}" if i == 0 else f"{x:02d}" for i, x in enumerate(dp)))
        else:
            add("year", str(dp[0]))
            if len(dp) > 1 and 1 <= dp[1] <= 12:
                f.append(("month", _MONTHS[dp[1] - 1], "macro"))
    elif (item.get("issued") or {}).get("literal"):
        add("year" if not bl else "date", latex_text(item["issued"]["literal"], ascii_only))
    if item.get("DOI"):
        add("doi", _verbatim(item["DOI"]), "verb")
    if ax:
        add("eprint", _verbatim(ax), "verb")
        add("archiveprefix" if not bl else "eprinttype", "arXiv" if not bl else "arxiv")
        cls = item.get("arxiv-class") or item.get("primaryClass") or ""
        if not cls:
            m = re.search(r"\[([a-z\-]+(?:\.[A-Za-z\-]+)?)\]", str(item.get("note", "")))
            cls = m.group(1) if m else ""
        if cls:
            add("primaryclass" if not bl else "eprintclass", latex_text(cls, ascii_only))
        if not bl and et == "misc" and not any(k == "howpublished" for k, *_ in f):
            add("howpublished", f"arXiv:{_plain_escape(ax, True)}")  # plain.bst ignores eprint
    url = str(item.get("URL") or "")
    if url and not (item.get("DOI") and "doi.org/" in url):
        add("url", _verbatim(url), "verb")
        if item.get("accessed") and bl:
            ad = _date_parts(item, "accessed")
            if ad:
                add("urldate", "-".join(f"{x:04d}" if i == 0 else f"{x:02d}" for i, x in enumerate(ad)))
    add("isbn", latex_text(item.get("ISBN", ""), ascii_only))
    add("issn", latex_text(item.get("ISSN", ""), ascii_only))
    if item.get("language") and bl:
        lang = str(item["language"]).lower()
        add("langid", {"en": "english", "en-us": "american", "en-gb": "british", "de": "german", "fr": "french"}.get(lang, ""))
    if item.get("abstract"):
        add("abstract", latex_text(" ".join(str(item["abstract"]).split()), ascii_only))
    return f


def to_bibtex(entries: list[tuple[str, dict]], flavor: str = "bibtex", header: str = "") -> str:
    if flavor not in ("bibtex", "biblatex"):
        raise ValueError('flavor must be "bibtex" or "biblatex"')
    out = []
    if header:
        out.append(header.rstrip() + "\n")
    for key, item in entries:
        et = entry_type(item, flavor)
        lines = [f"@{et}{{{key},"]
        for name, value, kind in bib_fields(item, flavor):
            if kind == "macro":
                lines.append(f"  {name} = {value},")
            else:
                lines.append(f"  {name} = {{{_balance(value)}}},")
        lines[-1] = lines[-1].rstrip(",")
        lines.append("}")
        out.append("\n".join(lines))
    return "\n\n".join(out) + ("\n" if out else "")


def _balance(s: str) -> str:
    """A field value must have balanced braces or it breaks the whole .bib file."""
    depth, out = 0, []
    i = 0
    while i < len(s):
        ch = s[i]
        if ch == "\\" and i + 1 < len(s):
            out.append(s[i:i + 2])
            i += 2
            continue
        if ch == "{":
            depth += 1
        elif ch == "}":
            if depth == 0:
                out.append(r"\}")
                i += 1
                continue
            depth -= 1
        out.append(ch)
        i += 1
    return "".join(out) + "}" * depth


# ---------------------------------------------------------------------------------------------- RIS
RIS_TYPES = {"article-journal": "JOUR", "article-magazine": "MGZN", "article-newspaper": "NEWS",
             "paper-conference": "CPAPER", "book": "BOOK", "chapter": "CHAP", "thesis": "THES", "report": "RPRT",
             "webpage": "ELEC", "post": "ELEC", "post-weblog": "BLOG", "dataset": "DATA", "software": "COMP",
             "patent": "PAT", "manuscript": "UNPB", "article": "UNPB", "speech": "SOUND", "map": "MAP",
             "standard": "STAND", "legislation": "STAT", "bill": "BILL", "legal_case": "CASE", "review": "JOUR"}


def _ris_name(n: dict) -> str:
    if n.get("literal"):
        return n["literal"]
    fam = " ".join(x for x in (n.get("non-dropping-particle"), n.get("family")) if x)
    given = " ".join(x for x in (n.get("given"), n.get("dropping-particle")) if x)
    return ", ".join(x for x in (fam, given, n.get("suffix")) if x)


def to_ris(entries: list[tuple[str, dict]]) -> str:
    recs = []
    for key, it in entries:
        t = RIS_TYPES.get(it.get("type", ""), "GEN")
        ax = arxiv_id(it)
        lines = [("TY", t), ("ID", key)]
        for n in it.get("author") or []:
            lines.append(("AU", _ris_name(n)))
        for n in it.get("editor") or []:
            lines.append(("ED" if t in ("BOOK", "CHAP") else "A2", _ris_name(n)))
        lines.append(("TI", it.get("title", "")))
        if it.get("container-title"):
            lines.append(("T2", it["container-title"]))
            if t == "JOUR":
                lines.append(("JF", it["container-title"]))
        if it.get("container-title-short"):
            lines.append(("J2", it["container-title-short"]))
        dp = _date_parts(it)
        if dp:
            lines.append(("PY", str(dp[0])))
            parts = [f"{dp[0]:04d}"] + [f"{x:02d}" for x in dp[1:]]
            lines.append(("DA", "/".join(parts + [""] * (3 - len(parts))) + "/"))  # 2018/06/03/ or 2024///
        for tag, var in (("VL", "volume"), ("IS", "issue"), ("PB", "publisher"), ("CY", "publisher-place"),
                         ("ET", "edition"), ("SN", "ISSN"), ("SN", "ISBN"), ("DO", "DOI"), ("UR", "URL"),
                         ("LA", "language")):
            if it.get(var):
                lines.append((tag, it[var]))
        page = str(it.get("page") or "")
        if page:
            parts = re.split(r"\s*[-–—]+\s*", page, maxsplit=1)
            lines.append(("SP", parts[0]))
            if len(parts) > 1:
                lines.append(("EP", parts[1]))
        if t in ("RPRT",) and it.get("number") and not ax:
            lines.append(("M1", it["number"]))
        if ax:
            lines.append(("AN", f"arXiv:{ax}"))
            if not it.get("URL"):
                lines.append(("UR", f"https://arxiv.org/abs/{ax}"))
        if it.get("abstract"):
            lines.append(("AB", " ".join(str(it["abstract"]).split())))
        lines.append(("ER", ""))
        recs.append("\r\n".join(f"{tag}  - {_ris_value(v)}".rstrip() if tag == "ER" else f"{tag}  - {_ris_value(v)}"
                                for tag, v in lines if v not in (None,) and (tag == "ER" or str(v).strip())))
    return "\r\n\r\n".join(recs) + ("\r\n" if recs else "")


def _ris_value(v) -> str:
    return " ".join(str(v).split())


# ---------------------------------------------------------------------------------------------- CSL-JSON
def to_csl_json(entries: list[tuple[str, dict]]) -> str:
    out = []
    for key, it in entries:
        d = dict(it)
        d["id"] = key
        d["citation-key"] = key
        out.append(d)
    return json.dumps(out, ensure_ascii=False, indent=2) + "\n"

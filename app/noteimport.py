"""Bring notes written elsewhere into research brain, and turn pasted formatting into Markdown.

    html_to_markdown(html)      formatted text (Google Docs, Word, Apple Notes, Notion, OneNote, web pages)
    docx_to_markdown(bytes)     a Word file (Google Docs downloads as one)
    text_note(name, text)       a .md / .txt file: {title, body, tags}, frontmatter and a first heading read
    notes_from_file(name, data) any of those, or a .zip of them (an Obsidian vault, a Notion export): note dicts
    images_to_pdf(jpegs, dest)  photos of handwritten pages, one PDF, a page each
    pdf_pages_png(path)         a PDF's pages as PNG images, for an AI that reads handwriting
    transcribe(backend, pngs)   the pages written out as Markdown by the active AI, if it reads images

Nothing here runs a converter program or reaches the network; the only parsers are the standard library's
HTMLParser and defusedxml (Word files), and zip members are read with size and count limits.
"""
from __future__ import annotations

import base64
import html as html_mod
import io
import re
import struct
import zipfile
import zlib
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath

import yaml

TEXT_EXT = {".md", ".markdown", ".mdown", ".txt", ".text"}
HTML_EXT = {".html", ".htm"}
DOCX_EXT = {".docx"}
NOTE_EXT = TEXT_EXT | HTML_EXT | DOCX_EXT
MAX_FILE = 20 << 20          # one note file
MAX_ZIP_TOTAL = 200 << 20    # everything unpacked from one zip
MAX_ZIP_NOTES = 2000
IND = "    "                 # one list level, like the editor (static/notes-editor.js)


class ImportError_(ValueError):
    """A file that can't be imported, with a plain-language reason."""


# ---------------------------------------------------------------- HTML -> Markdown
_BLOCK = {"p", "div", "section", "article", "header", "footer", "main", "aside", "figure", "figcaption",
          "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "hr", "dl", "dt", "dd", "address"}
_SKIP = {"script", "style", "head", "title", "meta", "link", "noscript", "template", "svg", "button", "select", "option"}


def _style(attrs: dict) -> dict:
    out = {}
    for part in (attrs.get("style") or "").split(";"):
        if ":" in part:
            k, v = part.split(":", 1)
            out[k.strip().lower()] = v.strip().lower()
    return out


def _bold(st: dict) -> bool | None:
    w = st.get("font-weight")
    if not w:
        return None
    if w in ("bold", "bolder"):
        return True
    if w in ("normal", "lighter"):
        return False
    return int(w) >= 600 if w.isdigit() else None


def _closing(mark: str) -> str:
    """The marks that close `mark`, innermost first: "**~~" (bold, then strike) closes as "~~**"."""
    parts = re.findall(r"\*\*|~~|==|\*|`", mark)
    return "".join(reversed(parts))


class _HTML(HTMLParser):
    """A small DOM-free converter: a stack of open inline marks and lists, writing Markdown as it goes."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[list] = []      # [kind 'ul'|'ol', counter, depth]
        self.marks: list[tuple[str, str]] = []  # (tag, markdown mark or "") for every open inline element
        self.quote = 0
        self.pre = 0
        self.skip = 0
        self.href: list[str] = []
        self.table: list[list[list[str]]] | None = None
        self.cell: list[str] | None = None
        self.word_list: tuple[int, bool] | None = None  # (level, ordered) for a Word "mso-list" paragraph
        self.word_ignore = 0
        self.li_level: list[int] = []    # aria-level of open Google Docs list items

    # -- output helpers
    def _text(self) -> str:
        return "".join(self.out)

    def _nl(self, n: int = 2):
        if self.cell is not None:
            return
        raw = self._text()
        # right after a bullet ("- ", "1. ", "> - [ ] "): a <p> inside an <li> starts the item's text, not a new block
        if re.search(r"(?:^|\n)[ >]*(?:[-*+] |\d+\. )(?:\[[ x]\] )?$", raw):
            return
        text = raw.rstrip(" \t")
        if not text.strip():
            self.out = []
            return
        have = len(text) - len(text.rstrip("\n"))
        self.out = [text + "\n" * max(0, n - have)]

    def _emit(self, s: str):
        if self.cell is not None:
            self.cell.append(s)
        else:
            self.out.append(s)

    def _at_line_start(self) -> bool:
        if self.cell is not None:
            return not "".join(self.cell).strip()
        t = self._text()
        return not t or t.endswith("\n")

    def _quote_prefix(self) -> str:
        return "> " * self.quote

    def _list_item(self, ordered: bool, level: int, start: int | None = None):
        self._nl(1 if self.lists or self.word_list else 2)
        bullet = f"{start or 1}. " if ordered else "- "
        self._emit(self._quote_prefix() + IND * max(0, level) + bullet)

    # -- parser callbacks
    def handle_starttag(self, tag, attrs):
        a = {k: (v or "") for k, v in attrs}
        if self.skip or tag in _SKIP:
            if tag in _SKIP and tag not in ("meta", "link"):
                self.skip += 1
            return
        st = _style(a)
        if self.word_ignore:
            if tag == "span":
                self.word_ignore += 1
            return
        if tag == "span" and "mso-list" in st and st["mso-list"].startswith("ignore"):
            self.word_ignore = 1  # Word's hand-drawn bullet ("·", "1.") before the text of a list paragraph
            return
        if tag in ("ul", "ol"):
            self._nl(1 if self.lists else 2)
            start = a.get("start")
            self.lists.append([tag, (int(start) - 1) if start and start.isdigit() else 0])
            return
        if tag == "li":
            level = len(self.lists) - 1
            if a.get("aria-level", "").isdigit():  # Google Docs: flat lists, the depth on each item
                level = int(a["aria-level"]) - 1
            kind = self.lists[-1] if self.lists else ["ul", 0]
            kind[1] += 1
            self._list_item(kind[0] == "ol", level, kind[1])
            return
        if tag == "p" and "mso-list" in st:  # Word: a list item is a paragraph with "mso-list:l0 level2 lfo1"
            m = re.search(r"level(\d+)", st["mso-list"])
            self.word_list = (int(m.group(1)) - 1 if m else 0, False)
            self._pending_word = True
            self._nl(1)
            return
        if tag in ("table",):
            self._nl(2)
            self.table = []
            return
        if tag == "tr" and self.table is not None:
            self.table.append([])
            return
        if tag in ("td", "th") and self.table is not None:
            if not self.table:
                self.table.append([])
            self.cell = []
            return
        if tag in _BLOCK:
            self._nl(2)
            if tag == "blockquote":
                self.quote += 1
            elif tag == "pre":
                self.pre += 1
                self._emit(self._quote_prefix() + "```\n")
            elif tag == "hr":
                self._emit(self._quote_prefix() + "---")
                self._nl(2)
            elif re.fullmatch(r"h[1-6]", tag):
                self._emit(self._quote_prefix() + "#" * int(tag[1]) + " ")
                self.marks.append((tag, ""))
            return
        if tag == "br":
            if self.cell is not None:
                self._emit(" ")
            else:
                self._emit("\n" + self._quote_prefix() + (IND * (len(self.lists)) if self.lists else ""))
            return
        if tag == "input" and a.get("type", "").lower() == "checkbox":
            self._emit("[x] " if "checked" in a else "[ ] ")
            return
        if tag == "img":
            alt = (a.get("alt") or "").strip()
            src = a.get("src") or ""
            if src.startswith(("http://", "https://")):
                self._emit(f"![{alt}]({src})")
            elif alt:
                self._emit(alt)
            return
        mark = ""
        if tag in ("strong", "b"):
            mark = "**" if _bold(st) is not False else ""
        elif tag in ("em", "i", "cite", "dfn"):
            mark = "*"
        elif tag in ("s", "del", "strike"):
            mark = "~~"
        elif tag == "mark":
            mark = "=="
        elif tag == "code" and not self.pre:
            mark = "`"
        elif tag == "span":  # Google Docs, Apple Notes: formatting as inline styles
            b = _bold(st)
            if b:
                mark += "**"
            if st.get("font-style") == "italic":
                mark += "*"
            if "line-through" in st.get("text-decoration", ""):
                mark += "~~"
        elif tag == "a":
            href = a.get("href") or ""
            ok = href.startswith(("http://", "https://", "mailto:"))
            self.href.append(href if ok else "")
            self.marks.append((tag, "[" if ok else ""))
            if ok:
                self._emit("[")
            return
        self.marks.append((tag, mark))
        if mark:
            self._emit(mark)

    def _close_mark(self, mark: str):
        if not mark:
            return
        buf = self.cell if self.cell is not None else self.out
        # an emphasis that wraps nothing, or only spaces, is dropped; trailing spaces move outside the mark
        text = "".join(buf)
        if text.endswith(mark):
            del buf[:]
            buf.append(text[: -len(mark)])
            return
        stripped = text.rstrip(" ")
        trail = text[len(stripped):]
        del buf[:]
        buf.append(stripped + _closing(mark))
        buf.append(trail)

    def handle_endtag(self, tag):
        if tag in _SKIP:
            self.skip = max(0, self.skip - 1)
            return
        if self.skip:
            return
        if self.word_ignore:
            if tag == "span":
                self.word_ignore -= 1
            return
        if tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._nl(1 if self.lists else 2)
            return
        if tag == "li":
            return
        if tag == "p" and self.word_list is not None:
            self.word_list = None
            self._nl(1)
            return
        if tag in ("td", "th") and self.table is not None and self.cell is not None:
            self.table[-1].append(re.sub(r"\s+", " ", "".join(self.cell)).strip().replace("|", "\\|"))
            self.cell = None
            return
        if tag == "tr":
            return
        if tag == "table" and self.table is not None:
            rows = [r for r in self.table if r]
            self.table = None
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                lines = ["| " + " | ".join(rows[0]) + " |", "|" + "---|" * w]
                lines += ["| " + " | ".join(r) + " |" for r in rows[1:]]
                self._emit("\n".join(lines))
            self._nl(2)
            return
        # inline marks: close the innermost open element with this tag (and anything opened inside it)
        for i in range(len(self.marks) - 1, -1, -1):
            if self.marks[i][0] == tag:
                for t, m in reversed(self.marks[i:]):
                    if t == "a":
                        href = self.href.pop() if self.href else ""
                        if href:
                            text = "".join(self.cell if self.cell is not None else self.out)
                            if text.endswith("["):  # a link around nothing
                                buf = self.cell if self.cell is not None else self.out
                                del buf[:]
                                buf.append(text[:-1])
                            else:
                                self._emit(f"]({href})")
                    else:
                        self._close_mark(m)
                del self.marks[i:]
                break
        if tag in _BLOCK:
            if tag == "blockquote":
                self.quote = max(0, self.quote - 1)
            elif tag == "pre":
                self.pre = max(0, self.pre - 1)
                self._emit("\n```")
            self._nl(2)

    def handle_data(self, data):
        if self.skip:
            return
        if self.word_ignore:
            # Word's bullet: a number means an ordered list
            if self.word_list is not None and re.search(r"\d", data):
                self.word_list = (self.word_list[0], True)
            return
        if self.word_list is not None and getattr(self, "_pending_word", False):
            self._pending_word = False
            lvl, ordered = self.word_list
            self._emit(self._quote_prefix() + IND * lvl + ("1. " if ordered else "- "))
        if self.pre:
            self._emit(data)
            return
        data = re.sub(r"[ \t\r\n\f\v ]+", " ", data)
        if self._at_line_start() or "".join(self.cell if self.cell is not None else self.out[-1:]).endswith((" ", "\n", "- ", ". ")):
            data = data.lstrip(" ")
        if not data:
            return
        if self.cell is None and self._text().endswith("\n"):
            # a second paragraph inside a list item stays in the item: indented under its bullet
            data = self._quote_prefix() + (IND * len(self.lists) if self.lists else "") + data
        self._emit(data)


def html_to_markdown(src: str) -> str:
    """Formatted text as Markdown: headings, bold/italic/strike/highlight, links, nested and numbered lists
    (nested <ul>s, Google Docs' aria-level, Word's mso-list paragraphs), checkboxes, quotes, code, tables."""
    if not src or not src.strip():
        return ""
    src = re.sub(r"<!--.*?-->", "", src, flags=re.S)  # Word's <!--[if !supportLists]--> and friends
    src = re.sub(r"<!\[(?:if|endif)[^\]]*\]>", "", src)
    if "<" not in src:
        return html_mod.unescape(src).strip()
    p = _HTML()
    try:
        p.feed(src)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html_mod.unescape(src)).strip()
    return tidy("".join(p.out))


def tidy(md: str) -> str:
    md = md.replace("\r\n", "\n").replace("\r", "\n").replace(" ", " ")
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\*\*\s*\*\*", "", md)           # bold that closed and reopened at once
    md = re.sub(r"(?m)^(#{1,6} )\*\*([^*\n]+)\*\*[ \t]*$", r"\1\2", md)  # a heading is bold already
    md = re.sub(r"(?m)^(\s*(?:[-*+]|\d+\.) )\s+", r"\1", md)
    md = re.sub(r"(?m)^(\s*(?:[-*+]|\d+\.))\s*$\n(?=\s*(?:[-*+]|\d+\.) |\s*$)", "", md)  # empty bullets
    md = re.sub(r"\n{3,}", "\n\n", md)
    # one list, written tight: no blank line between its items (Google Docs wraps each item in a <p>)
    item = r"[ \t]*(?:> )*[ \t]*(?:[-*+]|\d+[.)]) "
    prev = None
    while prev != md:
        prev = md
        md = re.sub(rf"(?m)^({item}.*)\n\n(?={item})", r"\1\n", md)
    return md.strip()


# ---------------------------------------------------------------- Word (.docx) -> Markdown
W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
R = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def _xml(data: bytes):
    from defusedxml import ElementTree as ET
    return ET.fromstring(data)


def docx_to_markdown(data: bytes) -> tuple[str, str]:
    """(title, markdown) of a Word file: headings, bold/italic/strike/highlight, links, nested bullets and
    numbered lists (from numbering.xml), tables. Images and comments are left out."""
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
        doc = _xml(_read_member(z, "word/document.xml", MAX_FILE))
    except (zipfile.BadZipFile, KeyError, OSError) as e:
        raise ImportError_("That Word file couldn't be opened (is it a .docx, not an older .doc?)") from e
    except Exception as e:  # defusedxml refused it or it is not XML
        raise ImportError_("That Word file couldn't be read") from e
    rels, fmt = {}, {}
    try:
        for r in _xml(_read_member(z, "word/_rels/document.xml.rels", 2 << 20)):
            if r.get("TargetMode") == "External":
                rels[r.get("Id")] = r.get("Target") or ""
    except Exception:
        pass
    try:
        num = _xml(_read_member(z, "word/numbering.xml", 4 << 20))
        absf = {}
        for an in num.findall(W + "abstractNum"):
            levels = {}
            for lvl in an.findall(W + "lvl"):
                nf = lvl.find(W + "numFmt")
                levels[int(lvl.get(W + "ilvl") or 0)] = (nf.get(W + "val") if nf is not None else "bullet")
            absf[an.get(W + "abstractNumId")] = levels
        for n in num.findall(W + "num"):
            ref = n.find(W + "abstractNumId")
            if ref is not None:
                fmt[n.get(W + "numId")] = absf.get(ref.get(W + "val"), {})
    except Exception:
        pass
    title = ""
    try:
        core = _xml(_read_member(z, "docProps/core.xml", 1 << 20))
        t = core.find("{http://purl.org/dc/elements/1.1/}title")
        title = (t.text or "").strip() if t is not None else ""
    except Exception:
        pass

    def runs(p) -> str:
        out = []
        for el in p:
            if el.tag == W + "r":
                out.append(run(el))
            elif el.tag == W + "hyperlink":
                inner = "".join(run(r) for r in el.iter(W + "r"))
                href = rels.get(el.get(R + "id") or "", "")
                out.append(f"[{inner}]({href})" if inner.strip() and href.startswith(("http://", "https://", "mailto:")) else inner)
            elif el.tag in (W + "ins", W + "smartTag", W + "sdt", W + "sdtContent", W + "fldSimple"):
                out.append(runs(el) if el.tag != W + "sdt" else runs(el.find(W + "sdtContent") if el.find(W + "sdtContent") is not None else el))
        return "".join(out)

    def run(r) -> str:
        text = []
        for el in r:
            if el.tag == W + "t":
                text.append(el.text or "")
            elif el.tag == W + "tab":
                text.append(" ")
            elif el.tag in (W + "br", W + "cr"):
                text.append("\n")
            elif el.tag == W + "sym":
                text.append("")
        s = "".join(text)
        if not s.strip():
            return s
        pr = r.find(W + "rPr")
        on = lambda name: pr is not None and pr.find(W + name) is not None and (pr.find(W + name).get(W + "val") or "true") not in ("0", "false", "none")  # noqa: E731
        lead = s[: len(s) - len(s.lstrip())]
        trail = s[len(s.rstrip()):]
        core = s.strip()
        if on("strike") or on("dstrike"):
            core = f"~~{core}~~"
        if on("highlight"):
            core = f"=={core}=="
        if on("i"):
            core = f"*{core}*"
        if on("b"):
            core = f"**{core}**"
        return lead + core + trail

    lines: list[str] = []
    counters: dict[tuple[str, int], int] = {}
    body = doc.find(W + "body")
    for el in (body if body is not None else []):
        if el.tag == W + "tbl":
            rows = []
            for tr in el.findall(W + "tr"):
                rows.append([re.sub(r"\s+", " ", " ".join(runs(p) for p in tc.iter(W + "p"))).strip().replace("|", "\\|")
                             for tc in tr.findall(W + "tc")])
            rows = [r for r in rows if any(c for c in r)]
            if rows:
                w = max(len(r) for r in rows)
                rows = [r + [""] * (w - len(r)) for r in rows]
                lines += ["", "| " + " | ".join(rows[0]) + " |", "|" + "---|" * w] + ["| " + " | ".join(r) + " |" for r in rows[1:]] + [""]
            continue
        if el.tag != W + "p":
            continue
        pr = el.find(W + "pPr")
        style = ""
        ilvl, num_id = None, None
        if pr is not None:
            ps = pr.find(W + "pStyle")
            style = (ps.get(W + "val") or "") if ps is not None else ""
            np_ = pr.find(W + "numPr")
            if np_ is not None:
                il, ni = np_.find(W + "ilvl"), np_.find(W + "numId")
                ilvl = int(il.get(W + "val") or 0) if il is not None else 0
                num_id = ni.get(W + "val") if ni is not None else None
                if num_id == "0":
                    num_id = None
        text = runs(el).strip()
        if not text:
            lines.append("")
            continue
        m = re.match(r"(?i)heading\s?(\d)", style)
        if style.lower() == "title":
            if not title:
                title = re.sub(r"[*~=]", "", text)
                continue
            lines += ["", "# " + text, ""]
        elif m:
            lines += ["", "#" * min(6, int(m.group(1)) + (0 if title else 0)) + " " + text, ""]
        elif num_id is not None or style.lower().startswith("list"):
            lvl = ilvl or 0
            kind = fmt.get(num_id or "", {}).get(lvl, "bullet")
            ordered = kind not in ("bullet", "none", "")
            for k in [k for k in counters if k[0] == num_id and k[1] > lvl]:
                del counters[k]
            counters[(num_id, lvl)] = counters.get((num_id, lvl), 0) + 1
            if lines and lines[-1] == "" and len(lines) > 1 and re.match(r"\s*(?:- |\d+\. )", lines[-2]):
                lines.pop()  # no blank line between items of one list
            lines.append(IND * lvl + (f"{counters[(num_id, lvl)]}. " if ordered else "- ") + text)
        elif style.lower() in ("quote", "intensequote"):
            lines += ["", "> " + text, ""]
        else:
            lines += ["", text, ""]
    return title, tidy("\n".join(lines))


def _read_member(z: zipfile.ZipFile, name: str, limit: int) -> bytes:
    info = z.getinfo(name)
    if info.file_size > limit:
        raise ImportError_(f"{name} is too big")
    with z.open(info) as f:
        data = f.read(limit + 1)
    if len(data) > limit:
        raise ImportError_(f"{name} is too big")
    return data


# ---------------------------------------------------------------- text files
_NOTION_ID = re.compile(r"\s+[0-9a-f]{32}$", re.I)  # Notion export names: "Page title 1a2b...(32 hex)"


def clean_title(name: str) -> str:
    stem = PurePosixPath(name.replace("\\", "/")).stem
    stem = _NOTION_ID.sub("", stem)
    return re.sub(r"\s+", " ", stem.replace("_", " ")).strip() or "Imported note"


def text_note(name: str, text: str) -> dict:
    """A Markdown or plain-text file as {title, body, tags}. YAML frontmatter (Obsidian, Bear, Logseq exports)
    gives the title and tags; else a first-line "# Heading" becomes the title; else the file name."""
    text = text.lstrip("﻿").replace("\r\n", "\n").replace("\r", "\n")
    title, tags = "", []
    m = re.match(r"---\n(.*?)\n---[ \t]*(?:\n|$)", text, re.S)
    if m:
        try:
            fm = yaml.safe_load(m.group(1))
        except yaml.YAMLError:
            fm = None
        if isinstance(fm, dict):
            text = text[m.end():]
            t = fm.get("title")
            title = str(t).strip() if isinstance(t, (str, int, float)) else ""
            raw = fm.get("tags") or fm.get("tag") or []
            if isinstance(raw, str):
                raw = re.split(r"[,\s]+", raw)
            tags = [str(x).strip().lstrip("#") for x in raw if isinstance(x, (str, int)) and str(x).strip()]
    body = text.strip("\n")
    if not title:
        h = re.match(r"#\s+(.+?)\s*#*\s*(?:\n|$)", body)
        if h:
            title = h.group(1).strip()
            body = body[h.end():].lstrip("\n")
    if not title:
        title = clean_title(name)
    return {"title": title[:300], "body": body.rstrip() + ("\n" if body.strip() else ""), "tags": tags[:30]}


def _decode(data: bytes) -> str:
    for enc in ("utf-8-sig", "utf-16") if data[:2] in (b"\xff\xfe", b"\xfe\xff") else ("utf-8-sig",):
        try:
            return data.decode(enc)
        except UnicodeDecodeError:
            pass
    return data.decode("cp1252", "replace")


def _html_title(src: str) -> str:
    m = re.search(r"<title[^>]*>(.*?)</title>", src, re.S | re.I) or re.search(r"<h1[^>]*>(.*?)</h1>", src, re.S | re.I)
    return re.sub(r"\s+", " ", html_mod.unescape(re.sub(r"<[^>]+>", "", m.group(1)))).strip() if m else ""


def one_note(name: str, data: bytes) -> dict:
    """A single note file -> {title, body, tags, name}. Raises ImportError_ for a type it doesn't read."""
    ext = PurePosixPath(name.lower()).suffix
    if len(data) > MAX_FILE:
        raise ImportError_("bigger than 20 MB")
    if ext in TEXT_EXT:
        out = text_note(name, _decode(data))
    elif ext in HTML_EXT:
        src = _decode(data)
        title = _html_title(src) or clean_title(name)
        body = html_to_markdown(src)
        # the title is also the page's first heading (Notion, Evernote, OneNote exports): don't say it twice
        body = re.sub(r"\A#{1,6}\s+" + re.escape(title) + r"\s*\n+", "", body)
        out = {"title": title[:300], "body": body + ("\n" if body else ""), "tags": []}
    elif ext in DOCX_EXT:
        title, body = docx_to_markdown(data)
        out = {"title": (title or clean_title(name))[:300], "body": body + ("\n" if body else ""), "tags": []}
    else:
        raise ImportError_("not a kind of file research brain reads as notes")
    out["name"] = name
    return out


def notes_from_file(name: str, data: bytes) -> tuple[list[dict], list[dict]]:
    """(notes, skipped) from one uploaded file; a .zip gives every note file inside it (folders become tags
    only through their own frontmatter; nothing outside the zip is touched)."""
    if PurePosixPath(name.lower()).suffix != ".zip":
        try:
            return [one_note(name, data)], []
        except ImportError_ as e:
            return [], [{"name": name, "why": str(e)}]
    try:
        z = zipfile.ZipFile(io.BytesIO(data))
    except zipfile.BadZipFile:
        return [], [{"name": name, "why": "not a zip file that can be opened"}]
    notes, skipped, total = [], [], 0
    for info in z.infolist():
        n = info.filename
        parts = PurePosixPath(n).parts
        if info.is_dir() or not parts or any(p.startswith(".") or p == "__MACOSX" for p in parts):
            continue
        if PurePosixPath(n.lower()).suffix not in NOTE_EXT:
            continue
        if len(notes) >= MAX_ZIP_NOTES:
            skipped.append({"name": n, "why": f"more than {MAX_ZIP_NOTES} notes in one zip"})
            break
        total += info.file_size
        if total > MAX_ZIP_TOTAL or info.file_size > MAX_FILE:
            skipped.append({"name": n, "why": "too big"})
            continue
        try:
            notes.append(one_note(n, _read_member(z, n, MAX_FILE)))
        except (ImportError_, zipfile.BadZipFile, OSError, RuntimeError) as e:  # RuntimeError: encrypted member
            skipped.append({"name": n, "why": str(e) or "couldn't be read"})
    if not notes and not skipped:
        skipped.append({"name": name, "why": "no .md, .txt, .html or .docx files inside"})
    return notes, skipped


# ---------------------------------------------------------------- handwriting: photos -> PDF, PDF -> images
def images_to_pdf(jpegs: list[bytes], dest: Path) -> int:
    """One PDF page per JPEG photo (the browser turns PNG/HEIC/WebP into JPEG first), each page the photo's
    shape at 150 dpi. Returns the page count."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument.new()
    try:
        for data in jpegs:
            if data[:3] != b"\xff\xd8\xff":
                raise ImportError_("a photo isn't a JPEG image")
            img = pdfium.PdfImage.new(pdf)
            img.load_jpeg(io.BytesIO(data), inline=True)
            w, h = img.get_px_size()
            if not w or not h:
                raise ImportError_("a photo couldn't be read")
            scale = 72 / 150 if max(w, h) > 1400 else 72 / 96
            pw, ph = w * scale, h * scale
            img.set_matrix(pdfium.PdfMatrix().scale(pw, ph))
            page = pdf.new_page(pw, ph)
            page.insert_obj(img)
            page.gen_content()
        pdf.save(str(dest))
        return len(pdf)
    finally:
        pdf.close()


def _png(rgb) -> bytes:
    """A numpy HxWx3 uint8 array as PNG (no image library needed)."""
    h, w = rgb.shape[:2]
    raw = b"".join(b"\x00" + rgb[y].tobytes() for y in range(h))
    chunk = lambda t, d: struct.pack(">I", len(d)) + t + d + struct.pack(">I", zlib.crc32(t + d) & 0xFFFFFFFF)  # noqa: E731
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))


def _page_png(pdf, i: int, long_side: int) -> bytes:
    page = pdf[i]
    w, h = page.get_size()
    scale = max(0.5, min(4.0, long_side / max(w, h, 1)))
    arr = page.render(scale=scale, rev_byteorder=True).to_numpy()
    if arr.ndim == 2:  # grey
        import numpy as np
        arr = np.repeat(arr[:, :, None], 3, axis=2)
    return _png(arr[:, :, :3].copy())


def pdf_pages_png(path: Path, max_pages: int = 20, long_side: int = 1600) -> list[bytes]:
    """The first pages of a PDF as PNGs about `long_side` pixels on their long side (enough to read handwriting)."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        return [_page_png(pdf, i, long_side) for i in range(min(len(pdf), max_pages))]
    finally:
        pdf.close()


def pdf_page_png(path: Path, i: int, long_side: int = 1400) -> bytes:
    """Page i (from 0) as a PNG; IndexError past the last page."""
    import pypdfium2 as pdfium
    pdf = pdfium.PdfDocument(str(path))
    try:
        if not 0 <= i < len(pdf):
            raise IndexError(i)
        return _page_png(pdf, i, long_side)
    finally:
        pdf.close()


TRANSCRIBE_SYSTEM = (
    "You transcribe a person's own handwritten notes into Markdown, exactly as written. Keep their words, order "
    "and structure: headings for titles, nested bullets (4 spaces per level) for indented points, numbered "
    "lists, - [ ] for boxes, **bold** for underlined or emphasised words, > for quotes. Equations go in LaTeX: "
    "$...$ inline, $$...$$ on their own line. A diagram or sketch becomes one line: *[sketch: what it shows]*. "
    "A word you can't read becomes [?]. Don't add, summarise, correct or comment; no preamble. Separate pages "
    "with a line holding only ---.")


def transcribe(backend, pngs: list[bytes], hint: str = "") -> str:
    """The pages written out as Markdown by `backend`, which must read images (Backend.reads_images)."""
    if not getattr(backend, "reads_images", False):
        raise ImportError_(f"{backend.name} can't read images here. Keep the scan as it is, or choose Claude Code "
                           "or a local vision model in Settings → AI.")
    n = len(pngs)
    prompt = (f"Transcribe these {n} page{'s' if n != 1 else ''} of handwritten notes." if n
              else "Transcribe these notes.") + (f" They're titled “{hint}”." if hint else "")
    images = [("image/png", base64.b64encode(p).decode("ascii")) for p in pngs]
    md = backend.ask_images(prompt, images, system=TRANSCRIBE_SYSTEM)
    md = re.sub(r"\A```(?:markdown|md)?\s*\n(.*)\n```\s*\Z", r"\1", (md or "").strip(), flags=re.S)
    return tidy(md)

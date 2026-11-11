"""Import papers, child notes and PDF highlights from the user's local Zotero library.

Zotero keeps its data in one folder (default ~/Zotero; override with RB_ZOTERO_DIR):
    zotero.sqlite                    the database (Zotero holds it locked while running)
    storage/<attachmentKey>/<file>   stored attachment files (PDFs)

We never touch those files: the database (plus its -wal file, if any) is copied to a temp
folder and the copy is opened read-only. PDFs are copied into the vault.

Mapping:
    regular item          -> paper note (title, creators, year, DOI, url, abstract, venue, arXiv id, tags)
    child notes (HTML)    -> the paper's body, converted to simple Markdown
    PDF annotations       -> fragments under the paper (quote = highlighted text, body = comment,
                             locator = "p. <pageLabel>", page = pageIndex + 1)
    citation key          -> the paper's `citekey` (Zotero 7's field, "Citation Key:" in Extra, or Better
                             BibTeX's better-bibtex.sqlite), so LaTeX citation keys stay the same (app/cite.py)
Each imported note/fragment stores `zotero_key` (item or annotation key), so re-imports are idempotent.
"""
from __future__ import annotations

import hashlib
import html
import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import time
import traceback
import unicodedata
import uuid
from contextlib import contextmanager
from html.parser import HTMLParser
from pathlib import Path

from .pdftext import extract
from .scholar import ARXIV_URL_RE, clean_doi

SKIP_TYPES = {"attachment", "note", "annotation"}
VENUE_FIELDS = ("publicationTitle", "proceedingsTitle", "conferenceName", "bookTitle", "websiteTitle",
                "blogTitle", "forumTitle", "programTitle", "university", "repository", "publisher")
TITLE_FIELDS = ("title", "caseName", "nameOfAct", "subject")
ANNOTATION_TYPES = {1: "highlight", 2: "note", 3: "image", 4: "ink", 5: "underline", 6: "text"}
ARXIV_ID = r"(\d{4}\.\d{4,5}|[a-z\-]+(?:\.[A-Z]{2})?/\d{7})"


class ZoteroError(RuntimeError):
    pass


# ---------------------------------------------------------------- locating the library
def _prefs_data_dir() -> Path | None:
    """Zotero's custom data-directory setting, if the user moved it (Settings, Advanced, Files and Folders)."""
    roots = [Path.home() / "Library/Application Support/Zotero/Profiles",  # macOS
             Path.home() / ".zotero/zotero",                                # Linux
             Path(os.environ.get("APPDATA", "~")).expanduser() / "Zotero/Zotero/Profiles"]  # Windows
    for root in roots:
        try:
            prefs = sorted(root.glob("*/prefs.js"))
        except OSError:
            continue
        for p in prefs:
            try:
                txt = p.read_text(encoding="utf-8", errors="ignore")
            except OSError:
                continue
            if 'user_pref("extensions.zotero.useDataDir", false)' in txt:
                continue
            m = re.search(r'user_pref\("extensions\.zotero\.dataDir",\s*"((?:[^"\\]|\\.)*)"\);', txt)
            if m:
                d = Path(m.group(1).encode().decode("unicode_escape")).expanduser()
                if (d / "zotero.sqlite").exists():
                    return d
    return None


def data_dir() -> Path:
    env = os.environ.get("RB_ZOTERO_DIR", "").strip()
    if env:
        return Path(env).expanduser()
    default = Path.home() / "Zotero"
    if (default / "zotero.sqlite").exists():
        return default
    return _prefs_data_dir() or default


@contextmanager
def snapshot(zdir: Path):
    """Read-only connection to a temporary copy of zotero.sqlite (works while Zotero is open)."""
    db = zdir / "zotero.sqlite"
    if not db.exists():
        raise ZoteroError(f"No zotero.sqlite in {zdir}")
    tmp = Path(tempfile.mkdtemp(prefix="rb_zotero_"))
    conn = None
    try:
        last_err: Exception | None = None
        for attempt in range(3):  # a copy taken mid-write can be inconsistent; copy again
            for suffix in ("", "-wal"):
                src = zdir / f"zotero.sqlite{suffix}"
                dst = tmp / f"zotero.sqlite{suffix}"
                if src.exists():
                    shutil.copyfile(src, dst)
                elif dst.exists():
                    dst.unlink()
            try:
                conn = sqlite3.connect(f"file:{(tmp / 'zotero.sqlite').as_posix()}?mode=ro", uri=True,
                                       check_same_thread=False)
                conn.row_factory = sqlite3.Row
                conn.execute("SELECT count(*) FROM items").fetchone()
                break
            except sqlite3.DatabaseError as e:
                last_err = e
                if conn is not None:
                    conn.close()
                    conn = None
                time.sleep(0.3 * (attempt + 1))
        if conn is None:
            raise ZoteroError(f"Couldn't read zotero.sqlite ({last_err}). If Zotero is busy syncing, wait a moment and try again.")
        yield conn
    finally:
        if conn is not None:
            conn.close()
        shutil.rmtree(tmp, ignore_errors=True)


def _tables(conn) -> set[str]:
    return {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _ids(conn, tables: set[str], sql: str) -> set[int]:
    try:
        return {r[0] for r in conn.execute(sql)}
    except sqlite3.DatabaseError:
        return set()


# ---------------------------------------------------------------- reading
def _excluded_items(conn, tables) -> set[int]:
    out = _ids(conn, tables, "SELECT itemID FROM deletedItems") if "deletedItems" in tables else set()
    if "feedItems" in tables:
        out |= _ids(conn, tables, "SELECT itemID FROM feedItems")
    if "libraries" in tables:
        out |= _ids(conn, tables, "SELECT itemID FROM items WHERE libraryID IN (SELECT libraryID FROM libraries WHERE type='feed')")
    return out


def _type_names(conn, tables) -> dict[int, str]:
    t = "itemTypesCombined" if "itemTypesCombined" in tables else "itemTypes"
    return {r[0]: r[1] for r in conn.execute(f"SELECT itemTypeID, typeName FROM {t}")}


def _regular_items(conn, tables) -> dict[int, dict]:
    types = _type_names(conn, tables)
    excluded = _excluded_items(conn, tables)
    items = {}
    for r in conn.execute("SELECT itemID, key, itemTypeID, dateAdded, libraryID FROM items ORDER BY dateAdded, itemID"):
        tname = types.get(r["itemTypeID"], "")
        if tname in SKIP_TYPES or r["itemID"] in excluded:
            continue
        items[r["itemID"]] = {"id": r["itemID"], "key": r["key"], "item_type": tname, "added": r["dateAdded"],
                              "library_id": r["libraryID"]}
    return items


def _libraries(conn, tables, items: dict[int, dict]) -> list[dict]:
    """The user library and any group libraries (feeds left out), with how many papers each holds."""
    counts: dict[int, int] = {}
    for it in items.values():
        counts[it["library_id"]] = counts.get(it["library_id"], 0) + 1
    names = {}
    if "groups" in tables:
        try:
            names = {r["libraryID"]: r["name"] for r in conn.execute("SELECT libraryID, name FROM groups")}
        except sqlite3.DatabaseError:
            names = {}
    rows: list[tuple[int, str]] = []
    if "libraries" in tables:
        rows = [(r["libraryID"], r["type"]) for r in conn.execute("SELECT libraryID, type FROM libraries ORDER BY libraryID")]
    else:  # very old or partial schema: infer from the items
        rows = [(lid, "user" if lid == 1 else "group") for lid in sorted(counts)]
    out = []
    for lid, kind in rows:
        if kind not in ("user", "group"):
            continue  # feeds (and the old "publications" library) aren't paper libraries
        name = "My Library" if kind == "user" else (names.get(lid) or f"Group library {lid}")
        out.append({"id": lid, "name": name, "kind": kind, "items": counts.get(lid, 0)})
    out.sort(key=lambda l: (l["kind"] != "user", l["name"].lower()))
    return out


def _collections(conn, tables, items: dict[int, dict]) -> list[dict]:
    """Collections with nesting. `count` includes items in sub-collections (that's what an import of it takes)."""
    if "collections" not in tables:
        return []
    deleted = _ids(conn, tables, "SELECT collectionID FROM deletedCollections") if "deletedCollections" in tables else set()
    feed_libs = _ids(conn, tables, "SELECT libraryID FROM libraries WHERE type='feed'") if "libraries" in tables else set()
    rows = [r for r in conn.execute("SELECT collectionID, collectionName, parentCollectionID, key, libraryID FROM collections")
            if r["collectionID"] not in deleted and r["libraryID"] not in feed_libs]
    by_id = {r["collectionID"]: r for r in rows}
    direct: dict[int, set[int]] = {cid: set() for cid in by_id}
    for r in conn.execute("SELECT collectionID, itemID FROM collectionItems"):
        if r["collectionID"] in direct and r["itemID"] in items:
            direct[r["collectionID"]].add(r["itemID"])
    kids: dict[int | None, list[int]] = {}
    for cid, r in by_id.items():
        parent = r["parentCollectionID"] if r["parentCollectionID"] in by_id else None
        kids.setdefault(parent, []).append(cid)

    def members(cid: int, seen: set[int]) -> set[int]:
        if cid in seen:
            return set()
        seen.add(cid)
        out = set(direct[cid])
        for k in kids.get(cid, []):
            out |= members(k, seen)
        return out

    out: list[dict] = []

    def walk(parent: int | None, depth: int):
        for cid in sorted(kids.get(parent, []), key=lambda c: by_id[c]["collectionName"].lower()):
            r = by_id[cid]
            ids = members(cid, set())
            pkey = by_id[r["parentCollectionID"]]["key"] if r["parentCollectionID"] in by_id else None
            out.append({"key": r["key"], "name": r["collectionName"], "parentKey": pkey, "depth": depth,
                        "count": len(ids), "item_ids": ids, "library_id": r["libraryID"]})
            walk(cid, depth + 1)

    walk(None, 0)
    return out


def _field_values(conn, tables, items) -> dict[int, dict[str, str]]:
    f = "fieldsCombined" if "fieldsCombined" in tables else "fields"
    out: dict[int, dict[str, str]] = {i: {} for i in items}
    for r in conn.execute(f"SELECT d.itemID, f.fieldName, v.value FROM itemData d "
                          f"JOIN itemDataValues v ON v.valueID = d.valueID JOIN {f} f ON f.fieldID = d.fieldID"):
        if r["itemID"] in out and r["value"] is not None:
            out[r["itemID"]][r["fieldName"]] = str(r["value"])
    return out


def _creators(conn, tables, items) -> dict[int, list[tuple[str, str]]]:
    ct = "LEFT JOIN creatorTypes ct ON ct.creatorTypeID = ic.creatorTypeID" if "creatorTypes" in tables else ""
    ctype = "ct.creatorType" if ct else "''"
    out: dict[int, list[tuple[str, str]]] = {}
    for r in conn.execute(f"SELECT ic.itemID, c.firstName, c.lastName, c.fieldMode, {ctype} AS ctype "
                          f"FROM itemCreators ic JOIN creators c ON c.creatorID = ic.creatorID {ct} "
                          f"ORDER BY ic.itemID, ic.orderIndex"):
        if r["itemID"] not in items:
            continue
        first, last = (r["firstName"] or "").strip(), (r["lastName"] or "").strip()
        name = last if r["fieldMode"] == 1 or not first else f"{first} {last}".strip()
        if name:
            out.setdefault(r["itemID"], []).append((r["ctype"] or "", name))
    return out


def _tags(conn, tables, ids) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if "itemTags" not in tables:
        return out
    for r in conn.execute("SELECT it.itemID, t.name FROM itemTags it JOIN tags t ON t.tagID = it.tagID ORDER BY t.name"):
        if r["itemID"] in ids and r["name"] and r["name"].strip():
            lst = out.setdefault(r["itemID"], [])
            if r["name"].strip().lower() not in {x.lower() for x in lst}:
                lst.append(r["name"].strip())
    return out


def _resolve_attachment(zdir: Path, att_key: str, link_mode: int | None, path: str | None) -> Path | None:
    if not path:
        return None
    if link_mode in (0, 1) and path.startswith("storage:"):
        storage = (zdir / "storage" / att_key).resolve()
        p = (storage / path[len("storage:"):]).resolve()
        if storage in p.parents and p.is_file():
            return p
        return None
    if link_mode == 2 and not path.startswith("attachments:"):
        # A linked file can point anywhere on disk (and a group library's paths come from other people), so
        # only a real PDF is taken: a .pdf name, a regular file once symlinks are resolved, PDF magic bytes.
        p = Path(path).expanduser()
        if not p.is_absolute() or p.suffix.lower() != ".pdf":
            return None
        try:
            real = p.resolve()
            return real if real.is_file() and real.suffix.lower() == ".pdf" and _looks_like_pdf(real) else None
        except OSError:
            return None
    return None


def _looks_like_pdf(p: Path) -> bool:
    try:
        with open(p, "rb") as f:
            return b"%PDF" in f.read(1024)
    except OSError:
        return False


def _attachments(conn, tables, zdir: Path, items, excluded) -> tuple[dict[int, list[dict]], dict[int, int]]:
    """item -> [pdf attachments], plus attachmentID -> parent itemID (for annotations)."""
    out: dict[int, list[dict]] = {}
    parent_of: dict[int, int] = {}
    if "itemAttachments" not in tables:
        return out, parent_of
    for r in conn.execute("SELECT a.itemID, i.key, a.parentItemID, a.linkMode, a.contentType, a.path "
                          "FROM itemAttachments a JOIN items i ON i.itemID = a.itemID ORDER BY i.dateAdded, a.itemID"):
        if r["itemID"] in excluded or r["parentItemID"] not in items:
            continue
        parent_of[r["itemID"]] = r["parentItemID"]
        is_pdf = (r["contentType"] or "") == "application/pdf" or (r["path"] or "").lower().endswith(".pdf")
        if not is_pdf:
            continue
        out.setdefault(r["parentItemID"], []).append({
            "id": r["itemID"], "key": r["key"], "file": _resolve_attachment(zdir, r["key"], r["linkMode"], r["path"])})
    return out, parent_of


def _notes(conn, tables, items, excluded) -> dict[int, list[str]]:
    out: dict[int, list[str]] = {}
    if "itemNotes" not in tables:
        return out
    types = _type_names(conn, tables)
    note_type = next((k for k, v in types.items() if v == "note"), None)
    for r in conn.execute("SELECT n.itemID, n.parentItemID, n.note, i.itemTypeID FROM itemNotes n "
                          "JOIN items i ON i.itemID = n.itemID ORDER BY i.dateAdded, n.itemID"):
        if r["itemTypeID"] != note_type or r["itemID"] in excluded or r["parentItemID"] not in items:
            continue
        md = html_to_markdown(r["note"] or "")
        if md:
            out.setdefault(r["parentItemID"], []).append(md)
    return out


def _page_index(position: str | None) -> int | None:
    try:
        pos = json.loads(position or "")
        pi = pos.get("pageIndex") if isinstance(pos, dict) else None
        return int(pi) + 1 if pi is not None else None
    except (ValueError, TypeError):
        return None


def _annotations(conn, tables, parent_of: dict[int, int], excluded) -> dict[int, list[dict]]:
    out: dict[int, list[dict]] = {}
    if "itemAnnotations" not in tables:
        return out
    for r in conn.execute("SELECT a.itemID, i.key, a.parentItemID, a.type, a.text, a.comment, a.pageLabel, "
                          "a.sortIndex, a.position FROM itemAnnotations a JOIN items i ON i.itemID = a.itemID "
                          "ORDER BY a.parentItemID, a.sortIndex"):
        if r["itemID"] in excluded or r["parentItemID"] in excluded:
            continue
        item = parent_of.get(r["parentItemID"])
        if item is None:
            continue
        text = _clean_text(r["text"])
        comment = (r["comment"] or "").strip()
        label = (r["pageLabel"] or "").strip()
        out.setdefault(item, []).append({
            "key": r["key"], "type": ANNOTATION_TYPES.get(r["type"], str(r["type"])), "quote": text,
            "comment": comment, "page_label": label, "page": _page_index(r["position"]),
            "sort": r["sortIndex"] or "",
            "empty": not text and not comment})  # ink / image annotations without a comment: nothing to import
    for lst in out.values():  # one item may have several PDFs: keep each PDF's order, PDFs in order
        lst.sort(key=lambda a: (a["page"] if a["page"] is not None else 10**6, a["sort"]))
    return out


def _clean_text(t: str | None) -> str:
    t = (t or "").replace("­", "")
    t = re.sub(r"-\n(?=[a-z])", "", t)
    return re.sub(r"\s+", " ", t).strip()


def _year(date: str) -> int | None:
    m = re.search(r"(?<!\d)(1[5-9]\d{2}|20\d{2})(?!\d)", date or "")
    return int(m.group(1)) if m else None


def _arxiv(fields: dict[str, str]) -> str:
    doi = fields.get("DOI", "")
    m = re.search(r"10\.48550/arxiv\." + ARXIV_ID, doi, re.I)
    if m:
        return m.group(1)
    m = ARXIV_URL_RE.search(fields.get("url", ""))
    if m:
        return re.sub(r"v\d+$", "", m.group(1))
    for k in ("archiveID", "extra", "number", "reportNumber"):
        m = re.search(r"arxiv\s*(?:id)?\s*[:.]?\s*" + ARXIV_ID, fields.get(k, ""), re.I)
        if m:
            return m.group(1)
    return ""


def _doi(fields: dict[str, str]) -> str:
    if fields.get("DOI"):
        return clean_doi(fields["DOI"])
    m = re.search(r"^\s*DOI:\s*(10\.\d{4,9}/\S+)", fields.get("extra", ""), re.I | re.M)
    return clean_doi(m.group(1)) if m else ""


_CITEKEY_EXTRA = re.compile(r"^\s*(?:citation\s*key|bibtex)\s*:\s*(\S+)\s*$", re.I | re.M)


def _citekey(fields: dict[str, str], bbt: str = "") -> str:
    """The item's citation key: Zotero 7's own field, a Better BibTeX key pinned in Extra
    ("Citation Key: smith2020deep"), or the key Better BibTeX generated (its database)."""
    k = (fields.get("citationKey") or "").strip()
    if not k:
        m = _CITEKEY_EXTRA.search(fields.get("extra", ""))
        k = m.group(1) if m else ""
    k = k or (bbt or "").strip()
    return k if re.fullmatch(r"[A-Za-z0-9_:.+/\-]{1,100}", k) else ""


def _bbt_keys(zdir: Path) -> dict[tuple[int, str], str]:
    """{(libraryID, itemKey): citationKey} from Better BibTeX's better-bibtex.sqlite, if it is there.
    Read from a temporary copy, like zotero.sqlite; any problem just means no keys."""
    db = zdir / "better-bibtex.sqlite"
    if not db.is_file():
        return {}
    tmp = Path(tempfile.mkdtemp(prefix="rb_bbt_"))
    try:
        shutil.copyfile(db, tmp / db.name)
        conn = sqlite3.connect(f"file:{(tmp / db.name).as_posix()}?mode=ro", uri=True)
        try:
            cols = {r[1] for r in conn.execute("PRAGMA table_info(citationkey)")}
            if not {"itemKey", "citationKey"} <= cols:
                return {}
            lib = "libraryID" if "libraryID" in cols else "1"
            return {(int(r[0] or 1), str(r[1])): str(r[2]) for r in
                    conn.execute(f"SELECT {lib}, itemKey, citationKey FROM citationkey") if r[1] and r[2]}
        finally:
            conn.close()
    except (OSError, sqlite3.DatabaseError):
        return {}
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def read_library(zdir: Path) -> dict:
    """Everything the importer needs, read from a snapshot of zotero.sqlite."""
    with snapshot(zdir) as conn:
        tables = _tables(conn)
        for need in ("items", "itemData", "itemDataValues"):
            if need not in tables:
                raise ZoteroError(f"{zdir / 'zotero.sqlite'} doesn't look like a Zotero database (no {need} table)")
        items = _regular_items(conn, tables)
        excluded = _excluded_items(conn, tables)
        fields = _field_values(conn, tables, items)
        creators = _creators(conn, tables, items)
        atts, parent_of = _attachments(conn, tables, zdir, items, excluded)
        notes = _notes(conn, tables, items, excluded)
        anns = _annotations(conn, tables, parent_of, excluded)
        tags = _tags(conn, tables, set(items))
        collections = _collections(conn, tables, items)
        libraries = _libraries(conn, tables, items)
    bbt = _bbt_keys(zdir)
    out = []
    for iid, it in items.items():
        f = fields.get(iid, {})
        title = next((f[k].strip() for k in TITLE_FIELDS if f.get(k, "").strip()), "")
        cr = creators.get(iid, [])
        authors = [n for t, n in cr if t in ("author", "")] or [n for _, n in cr]
        pdfs = atts.get(iid, [])
        it.update(
            title=title or "Untitled", authors=authors, year=_year(f.get("date", "")), doi=_doi(f),
            arxiv=_arxiv(f), url=f.get("url", "").strip(), abstract=f.get("abstractNote", "").strip(),
            venue=next((f[k].strip() for k in VENUE_FIELDS if f.get(k, "").strip()), ""),
            tags=tags.get(iid, []), notes=notes.get(iid, []), annotations=anns.get(iid, []),
            pdf=next((a["file"] for a in pdfs if a["file"]), None), has_pdf_attachment=bool(pdfs),
            citekey=_citekey(f, bbt.get((it["library_id"] or 1, it["key"]), "")))
        out.append(it)
    return {"items": out, "collections": collections, "libraries": libraries}


# ---------------------------------------------------------------- HTML note -> Markdown
class _MD(HTMLParser):
    BLOCK = {"p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote", "pre", "table", "tr", "hr"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self.lists: list[list] = []   # stack of [kind, counter]
        self.href: list[str | None] = []
        self.quote = 0
        self.pre = 0

    def _nl(self, n: int = 2):
        text = "".join(self.out)
        stripped = text.rstrip(" ")
        have = len(stripped) - len(stripped.rstrip("\n"))
        if stripped.strip():
            self.out = [stripped + "\n" * max(0, n - have)]

    def _prefix(self) -> str:
        return "> " * self.quote

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag in ("ul", "ol"):
            self._nl(1 if self.lists else 2)
            self.lists.append([tag, 0])
        elif tag == "li":
            self._nl(1)
            indent = "  " * (len(self.lists) - 1)
            if self.lists and self.lists[-1][0] == "ol":
                self.lists[-1][1] += 1
                bullet = f"{self.lists[-1][1]}. "
            else:
                bullet = "- "
            self.out.append(self._prefix() + indent + bullet)
        elif tag in self.BLOCK:
            self._nl(2)
            if tag == "blockquote":
                self.quote += 1
            elif tag == "pre":
                self.pre += 1
                self.out.append(self._prefix() + "```\n")
            elif tag == "hr":
                self.out.append(self._prefix() + "---")
            elif re.fullmatch(r"h[1-6]", tag):
                self.out.append(self._prefix() + "#" * min(int(tag[1]) + 1, 6) + " ")
        elif tag == "br":
            self.out.append("\n")
        elif tag in ("strong", "b"):
            self.out.append("**")
        elif tag in ("em", "i"):
            self.out.append("*")
        elif tag == "a":
            self.href.append(a.get("href"))
            self.out.append("[" if a.get("href", "").startswith(("http://", "https://")) else "")

    def handle_endtag(self, tag):
        if tag in ("ul", "ol"):
            if self.lists:
                self.lists.pop()
            self._nl(2 if not self.lists else 1)
        elif tag == "li":
            pass
        elif tag in self.BLOCK:
            if tag == "blockquote":
                self.quote = max(0, self.quote - 1)
            if tag == "pre":
                self.pre = max(0, self.pre - 1)
                self.out.append("\n```")
            self._nl(2)
        elif tag in ("strong", "b", "em", "i"):
            mark = "**" if tag in ("strong", "b") else "*"
            if self.out and self.out[-1] == mark:
                self.out.pop()  # empty emphasis
            else:
                self.out.append(mark)
        elif tag == "a":
            href = self.href.pop() if self.href else None
            if href and href.startswith(("http://", "https://")):
                self.out.append(f"]({href})")

    def handle_data(self, data):
        if self.pre:
            self.out.append(data)
            return
        data = re.sub(r"\s+", " ", data)
        sofar = "".join(self.out)
        at_line_start = not sofar or sofar.endswith("\n")
        if at_line_start or sofar.endswith(" "):
            data = data.lstrip()
        if not data:
            return
        if at_line_start and self.quote:
            data = self._prefix() + data
        self.out.append(data)


def html_to_markdown(src: str) -> str:
    if not src or not src.strip():
        return ""
    if "<" not in src:
        return html.unescape(src).strip()
    p = _MD()
    try:
        p.feed(src)
        p.close()
    except Exception:
        return re.sub(r"<[^>]+>", " ", html.unescape(src)).strip()
    md = "".join(p.out)
    md = re.sub(r"[ \t]+\n", "\n", md)
    md = re.sub(r"\n{3,}", "\n\n", md)
    return md.strip()


# ---------------------------------------------------------------- status
def status() -> dict:
    zdir = data_dir()
    base = {"found": False, "dir": str(zdir), "items": 0, "collections": [], "libraries": [], "job": running_job()}
    if not (zdir / "zotero.sqlite").exists():
        hint = ("Set RB_ZOTERO_DIR to your Zotero data directory (Zotero, Settings, Advanced, Files and Folders, "
                "Data directory location) and restart research_brain.")
        what = (f"Couldn't find a Zotero library in {zdir}." if zdir.exists()
                else f"There's no Zotero folder at {zdir}.")
        return {**base, "message": f"{what} {hint}"}
    try:
        with snapshot(zdir) as conn:
            tables = _tables(conn)
            items = _regular_items(conn, tables)
            cols = _collections(conn, tables, items)
            libs = _libraries(conn, tables, items)
    except (ZoteroError, sqlite3.DatabaseError) as e:
        return {**base, "message": f"Found Zotero at {zdir} but couldn't read it: {e}"}
    return {**base, "found": True, "items": len(items), "message": "", "libraries": libs,
            "collections": [{k: c[k] for k in ("key", "name", "parentKey", "depth", "count", "library_id")}
                            for c in cols]}


# ---------------------------------------------------------------- import
def norm_title(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", " ", t).strip()


def _norm_quote(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "")).strip().lower()


def _frag_title(a: dict) -> str:
    words = (a["quote"] or a["comment"]).split()
    if words:
        return " ".join(words[:8]) + ("…" if len(words) > 8 else "")
    return f"p. {a['page_label']}" if a["page_label"] else "Highlight"


class _Library:
    """In-memory view of the vault for dedupe during one import run. Same matching rules as
    Store.find_duplicate (s2_id / DOI / arXiv, case-insensitive) plus zotero_key and exact normalised
    title, without re-reading every note file per Zotero item."""

    def __init__(self, store):
        self.store = store
        self.by_zkey: dict[str, str] = {}
        self.by_doi: dict[str, str] = {}
        self.by_arxiv: dict[str, str] = {}
        self.by_title: dict[str, str] = {}
        self.frag_keys: dict[str, set[str]] = {}
        self.frag_quotes: dict[str, set[str]] = {}
        for n in store.all():
            self.add(n)

    def add(self, n: dict):
        nid = n["id"]
        if n["type"] == "fragment":
            if n["parent"]:
                if n.get("zotero_key"):
                    self.frag_keys.setdefault(n["parent"], set()).add(n["zotero_key"])
                q = _norm_quote(n["quote"]) or ("body:" + _norm_quote(n["body"]))
                self.frag_quotes.setdefault(n["parent"], set()).add(q)
            return
        if n.get("zotero_key"):
            self.by_zkey.setdefault(n["zotero_key"], nid)
        if n["doi"]:
            self.by_doi.setdefault(n["doi"].lower(), nid)
        if n["arxiv"]:
            self.by_arxiv.setdefault(n["arxiv"].lower(), nid)
        t = norm_title(n["title"])
        if t and n["type"] == "paper":
            self.by_title.setdefault(t, nid)

    def find(self, it: dict) -> str | None:
        for nid in (self.by_zkey.get(it["key"]), self.by_doi.get(it["doi"].lower()) if it["doi"] else None,
                    self.by_arxiv.get(it["arxiv"].lower()) if it["arxiv"] else None,
                    self.by_title.get(norm_title(it["title"])) if norm_title(it["title"]) else None):
            if nid and self.store.exists(nid):
                return nid
        return None


MAX_PDF_BYTES = 300 << 20


def _copy_pdf(store, nid: str, src: Path) -> bool:
    if not _looks_like_pdf(src) or src.stat().st_size > MAX_PDF_BYTES:
        return False  # not a PDF (or absurdly large): leave it in Zotero
    dest = store.pdf_path(nid)
    part = dest.with_name(dest.name + ".part")
    shutil.copyfile(src, part)          # copy, never move: the Zotero file stays where it is
    part.replace(dest)
    try:
        text, _ = extract(dest)
    except Exception:
        text = ""
    if text.strip():
        store.text_path(nid).write_text(text, encoding="utf-8", newline="\n")
    return True


def import_hash(quote: str, body: str) -> str:
    """Fingerprint of a fragment's text as imported from Zotero. If the fragment's quote/note no longer
    match it, the user has edited the fragment, and a re-import must not overwrite or remove it."""
    return hashlib.sha1(f"{_norm_quote(quote)}\0{(body or '').strip()}".encode()).hexdigest()[:16]


def _locator(a: dict) -> str:
    return f"p. {a['page_label']}" if a["page_label"] else ""


def _add_fragments(store, lib: _Library, nid: str, anns: list[dict]) -> int:
    keys = lib.frag_keys.setdefault(nid, set())
    quotes = lib.frag_quotes.setdefault(nid, set())
    added = 0
    for a in anns:
        if a.get("empty"):
            continue  # ink / image annotation without a comment: nothing to import
        q = _norm_quote(a["quote"]) or ("body:" + _norm_quote(a["comment"]))
        if a["key"] in keys or q in quotes:
            continue
        frag = store.create({"type": "fragment", "parent": nid, "quote": a["quote"], "body": a["comment"],
                             "locator": _locator(a), "page": a["page"], "title": _frag_title(a),
                             "zotero_key": a["key"], "zotero_annotation": a["key"],
                             "zotero_hash": import_hash(a["quote"], a["comment"])})
        lib.add(frag)
        added += 1
    return added


def _sync_fragments(store, nid: str, anns: list[dict]) -> dict:
    """Bring fragments imported from Zotero annotations in line with Zotero (re-import):
      - quote / comment / page changed in Zotero -> updated, unless the user edited the fragment since
        import (then only the page is refreshed and it is reported as kept)
      - annotation deleted in Zotero -> fragment moved to the trash, unless the user edited it since
        import (then it is left alone and reported as "kept (edited)")
    Fragments the user made themselves (no annotation key) are never touched."""
    out = {"updated": 0, "removed": 0, "kept": []}
    live = {a["key"]: a for a in anns}
    for f in store.children(nid):
        key = f.get("zotero_annotation") or f.get("zotero_key") or ""
        if not key:
            continue
        legacy = not f.get("zotero_annotation")  # imported before sync existed: no fingerprint stored
        a = live.get(key)
        if legacy and a is not None and f["body"].strip() == a["comment"] and _norm_quote(f["quote"]) == _norm_quote(a["quote"]):
            # unchanged since import, so we can adopt it: store the fingerprint for next time
            f = store.set_fields(f["id"], zotero_annotation=key, zotero_hash=import_hash(a["quote"], a["comment"]))
            legacy = False
        edited = legacy or f.get("zotero_hash") != import_hash(f["quote"], f["body"])
        if a is None:
            if edited:
                out["kept"].append({"id": f["id"], "title": f["title"]})
            else:
                store.delete(f["id"])  # moves it to vault/.trash/
                out["removed"] += 1
            continue
        if a.get("empty"):
            continue
        text_changed = _norm_quote(f["quote"]) != _norm_quote(a["quote"]) or f["body"].strip() != a["comment"]
        page_changed = f["page"] != a["page"] or (not edited and f["locator"] != _locator(a))
        if not text_changed and not page_changed:
            continue
        fields: dict = {}
        if page_changed:
            fields["page"] = a["page"]
            if not edited or f["locator"] in ("", _locator(a)) or re.fullmatch(r"p\. \S+", f["locator"] or ""):
                fields["locator"] = _locator(a)
        if text_changed and not edited:
            old_auto = _frag_title({"quote": f["quote"], "comment": f["body"], "page_label": ""})
            fields.update(quote=a["quote"], body=a["comment"], zotero_hash=import_hash(a["quote"], a["comment"]))
            if f["title"] == old_auto or f["title"].startswith("p. "):
                fields["title"] = _frag_title(a)
        if text_changed and edited:
            out["kept"].append({"id": f["id"], "title": f["title"]})
        if fields:
            store.set_fields(f["id"], **fields)
            out["updated"] += 1
    return out


def import_item(store, lib: _Library, it: dict, include_pdfs: bool, include_annotations: bool) -> dict:
    """Create or update one paper. Returns {"result": created|updated|skipped, "fragments": n, "pdf": bool}."""
    nid = lib.find(it)
    changed, pdf_added = False, False
    if nid is None:
        fields = {"type": "paper", "title": it["title"], "authors": it["authors"], "year": it["year"],
                  "venue": it["venue"], "doi": it["doi"], "arxiv": it["arxiv"], "url": it["url"],
                  "abstract": it["abstract"], "tags": it["tags"], "body": "\n\n".join(it["notes"]),
                  "zotero_key": it["key"], "meta_source": "zotero"}  # Zotero's metadata: never overwritten (app/metadata.py)
        note = store.create({k: v for k, v in fields.items() if v not in (None, "", [])})
        lib.add(note)
        nid, result = note["id"], "created"
    else:
        note = store.get(nid)
        result = "skipped"
        # fill in only what's missing; never touch the user's summary, or a body they've written
        fill = {}
        for k in ("doi", "arxiv", "url", "abstract", "venue"):
            if not note[k] and it[k]:
                fill[k] = it[k]
        if note["year"] is None and it["year"]:
            fill["year"] = it["year"]
        if not note["authors"] and it["authors"]:
            fill["authors"] = it["authors"]
        if not note["zotero_key"]:
            fill["zotero_key"] = it["key"]
        if not note["body"].strip() and it["notes"] and note["type"] != "fragment":
            fill["body"] = "\n\n".join(it["notes"])
        new_tags = [t for t in it["tags"] if t.lower() not in {x.lower() for x in note["tags"]}]
        if new_tags:
            fill["tags"] = note["tags"] + new_tags
        if fill:
            note = store.set_fields(nid, **fill)
            lib.add(note)
            changed = "zotero_key" not in fill or len(fill) > 1
    if it.get("citekey"):  # keep Zotero's / Better BibTeX's citation key (app/cite.py), unless one is set
        from . import cite
        cur = store.get(nid)
        if not cite.stored_key(cur):
            cite.set_citekey(store, nid, it["citekey"])
            changed = changed or result != "created"
    if include_pdfs and it["pdf"] and not store.pdf_path(nid).exists():
        pdf_added = _copy_pdf(store, nid, it["pdf"])
    sync = {"updated": 0, "removed": 0, "kept": []}
    if include_annotations and result != "created":
        sync = _sync_fragments(store, nid, it["annotations"])
    frags = _add_fragments(store, lib, nid, it["annotations"]) if include_annotations else 0
    if result != "created" and (changed or pdf_added or frags or sync["updated"] or sync["removed"]):
        result = "updated"
    return {"result": result, "fragments": frags, "pdf": pdf_added, "id": nid,
            "highlights_updated": sync["updated"], "highlights_removed": sync["removed"], "kept": sync["kept"]}


# ---------------------------------------------------------------- background jobs
JOBS: dict[str, dict] = {}
_jobs_lock = threading.Lock()


def running_job() -> str | None:
    with _jobs_lock:
        return next((j["id"] for j in JOBS.values() if j["state"] == "running"), None)


def public_job(job: dict) -> dict:
    return {k: job[k] for k in ("id", "state", "done", "total", "created", "updated", "skipped", "fragments",
                                "pdfs", "errors", "current", "message", "library_id", "highlights_updated",
                                "highlights_removed", "highlights_kept", "kept")}


def get_job(job_id: str) -> dict | None:
    with _jobs_lock:
        j = JOBS.get(job_id)
        return public_job(j) if j else None


def start_import(store, collection_key: str | None, include_pdfs: bool, include_annotations: bool,
                 library_id: int | None = None, on_item=None) -> str:
    """Read the library synchronously (so a missing library or bad collection fails the request),
    then import in a background thread. `library_id` limits the import to one library (your own, or a
    group); None = every library. Raises ZoteroError, KeyError (unknown library or collection) or
    RuntimeError (busy)."""
    if running_job():
        raise RuntimeError("A Zotero import is already running")
    zdir = data_dir()
    if not (zdir / "zotero.sqlite").exists():
        raise ZoteroError(f"No Zotero library found at {zdir}. Set RB_ZOTERO_DIR to your Zotero data directory.")
    data = read_library(zdir)
    items = data["items"]
    if library_id is not None:
        if not any(l["id"] == library_id for l in data["libraries"]):
            raise KeyError(f"library {library_id}")
        items = [it for it in items if it.get("library_id") == library_id]
    if collection_key:
        col = next((c for c in data["collections"] if c["key"] == collection_key
                    and (library_id is None or c.get("library_id") == library_id)), None)
        if col is None:
            raise KeyError(collection_key)
        items = [it for it in items if it["id"] in col["item_ids"]]
    job = {"id": uuid.uuid4().hex[:12], "state": "running", "done": 0, "total": len(items), "created": 0,
           "updated": 0, "skipped": 0, "fragments": 0, "pdfs": 0, "errors": [], "current": "", "message": "",
           "library_id": library_id, "highlights_updated": 0, "highlights_removed": 0, "highlights_kept": 0,
           "kept": []}
    with _jobs_lock:
        if any(j["state"] == "running" for j in JOBS.values()):
            raise RuntimeError("A Zotero import is already running")
        JOBS[job["id"]] = job

    def run():
        try:
            lib = _Library(store)
            for it in items:
                job["current"] = it["title"]
                try:
                    r = import_item(store, lib, it, include_pdfs, include_annotations)
                    if on_item is not None:  # e.g. fill missing DOI-based fields from Crossref (app/metadata.py)
                        try:
                            on_item(r["id"], r["result"])
                        except Exception as e:
                            print(f"[research_brain] after-import hook for {r['id']} failed: {e!r}")
                    job[r["result"]] += 1
                    job["fragments"] += r["fragments"]
                    job["pdfs"] += int(r["pdf"])
                    job["highlights_updated"] += r["highlights_updated"]
                    job["highlights_removed"] += r["highlights_removed"]
                    job["highlights_kept"] += len(r["kept"])
                    job["kept"] = (job["kept"] + r["kept"])[:50]
                except Exception as e:  # one bad item shouldn't stop the rest
                    if len(job["errors"]) < 50:
                        job["errors"].append(f"{it['title'][:120]}: {e}")
                    traceback.print_exc()
                job["done"] += 1
            job["state"] = "done"
        except Exception as e:
            job["state"] = "error"
            job["message"] = str(e)
            traceback.print_exc()
        finally:
            job["current"] = ""

    threading.Thread(target=run, daemon=True, name=f"zotero-import-{job['id']}").start()
    return job["id"]

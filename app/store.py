"""Note storage: one Markdown file per note with YAML frontmatter.

Your documents (app/layout.py; the documents folder is ~/Documents/research brain for a normal install,
see app/paths.py documents_dir):
    Papers/, Notes/, Questions/   one <title>.md per note: frontmatter (id, metadata, summary) + body
    Highlights/<paper>/           a paper's highlights (fragments), one file each
    Topics/                       app/topics.py
    PDFs/<id>.pdf                 original PDFs (the papers folder; app/papers.py)
The library folder (the app's own data):
    text/<id>.txt      extracted full text (fed to the AI for critiques)
    state/             your own app state that isn't a note: review schedule and history, open counts
                       (backed up with the vault snapshot)
    .index/            derived data only (embeddings, cached explanations, meta.sqlite); safe to delete
    .trash/            deleted notes
Notes are plain Markdown, so nothing is ever locked inside the app.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import shutil
import threading
import unicodedata
from pathlib import Path
from typing import Any

import yaml

from . import fsio

FIELDS_DEFAULTS: dict[str, Any] = {
    "type": "note",
    "title": "",
    "authors": [],
    "year": None,
    "venue": "",
    "doi": "",
    "arxiv": "",
    "s2_id": "",
    "url": "",
    "abstract": "",
    "summary": "",
    "tags": [],
    "links": [],
    "cites": [],       # library papers this paper cites (from Semantic Scholar references; directed)
    "created": "",
    "updated": "",
    "critique": None,
    "critiques": [],   # every critique, oldest first: [{text, at, summary_snapshot}]; `critique` = the latest
    "parent": "",
    "quote": "",
    "locator": "",
    "page": None,
    "zotero_key": "",
    "zotero_annotation": "",  # fragments imported from a Zotero annotation: its key (for re-import sync)
    "zotero_hash": "",        # hash of the fragment's body as last imported (edited since -> never auto-removed)
    "status": "",             # reading status (STATUSES); "" = none set. New papers start as "to-read"
    "saved_because": "",      # the user's one-line "why I kept this"
    "selector": None,         # web highlights: W3C TextQuoteSelector {exact, prefix, suffix} for re-anchoring, or None
    "source": "",             # papers: "linked" = link + full text only (no PDF file kept), "pdf" = has the PDF; "" = older/none
    "csl": None,              # papers: the bibliographic record as CSL-JSON (app/metadata.py), or None
    "meta_source": "",        # where `csl` came from: crossref, datacite, arxiv, pubmed, openlibrary, osti, ntrs,
                              # translation-server, s2, page, zotero ("" = never resolved)
    "meta_checked": "",       # when the metadata was last resolved (ISO time)
    # open questions (type "question", app/questions.py); written only when set, so other notes don't carry them
    "question_status": "",    # open | answered | parked
    "answer": "",             # the user's current answer / thinking (Markdown + LaTeX)
    "last_seen": "",          # when the user last looked at the question's evidence (ISO time)
}
SOURCES = ("", "linked", "pdf")
TYPES = ("paper", "note", "fragment", "question")
STATUSES = ("to-read", "reading", "read", "later", "not-relevant")
QUESTION_STATUSES = ("open", "answered", "parked")
EDITABLE = {"title", "summary", "body", "tags", "authors", "year", "venue", "url", "abstract",
            "quote", "locator", "page", "status", "saved_because", "question_status", "answer"}


def normalize_selector(v: Any) -> dict | None:
    """A TextQuoteSelector as stored on web-highlight fragments: {exact, prefix, suffix} (strings), or None."""
    if not isinstance(v, dict):
        return None
    exact = str(v.get("exact") or "")[:5000]
    if not exact.strip():
        return None
    return {"exact": exact, "prefix": str(v.get("prefix") or "")[:200], "suffix": str(v.get("suffix") or "")[:200]}


def set_private(note: dict, value: Any) -> None:
    """Write the note's `private` property (app/privacy.py): True or False, or None to remove it (the
    type's default applies again). Kept with the other extra properties, so older versions keep it."""
    extra = dict(note.get("_extra") or {})
    if value is None or (isinstance(value, str) and not value.strip()):
        extra.pop("private", None)
    else:
        if isinstance(value, str):
            value = value.strip().lower() in ("true", "yes", "1", "on", "private")
        extra["private"] = bool(value)
    note["_extra"] = extra


class FrontmatterError(ValueError):
    """A note file's YAML frontmatter can't be parsed (hand-edited); writing it back would erase it."""


MAX_FRONTMATTER_BYTES = 1 << 20  # 1 MB of YAML is far beyond any real note (long critiques included)


MAX_FRONTMATTER_NODES = 200_000  # values after expanding YAML aliases (a large real note has a few hundred)


def _expanded_size(v, memo: dict, active: set) -> int:
    """How big `v` becomes when every alias is copied out (strings count by length). Memoised by object,
    so it runs in time linear in the parsed document even when the answer is astronomically large."""
    key = id(v)
    if key in memo:
        return memo[key]
    if isinstance(v, (dict, list, tuple)):
        if key in active:
            raise yaml.YAMLError("frontmatter refers to itself (a recursive YAML alias)")
        active.add(key)
        items = list(v.items()) if isinstance(v, dict) else [(None, x) for x in v]
        n = 1
        for k, x in items:
            n += _expanded_size(k, memo, active) if k is not None else 0
            n += _expanded_size(x, memo, active)
            if n > MAX_FRONTMATTER_NODES:
                break
        active.discard(key)
    else:
        n = 1 + (len(v) // 64 if isinstance(v, str) else 0)
    memo[key] = n
    return n


class NoAliasDumper(yaml.SafeDumper):
    """SafeDumper that writes a shared list twice instead of as &anchor/*alias (which load_frontmatter refuses)."""

    def ignore_aliases(self, data):
        return True


def load_frontmatter(text: str):
    """yaml.safe_load for note and topic frontmatter, with limits. Raises yaml.YAMLError for a file over
    1 MB, or whose aliases (&a / *a) would blow up when copied ("billion laughs") or refer to themselves.
    Plain aliases are fine: older versions wrote `critique: &id001 ...` / `critiques: [*id001]`."""
    if len(text) > MAX_FRONTMATTER_BYTES:
        raise yaml.YAMLError(f"frontmatter is larger than {MAX_FRONTMATTER_BYTES >> 20} MB")
    data = yaml.safe_load(text)
    if "*" in text and _expanded_size(data, {}, set()) > MAX_FRONTMATTER_NODES:
        raise yaml.YAMLError("frontmatter expands to too much data (nested YAML aliases)")
    return data


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def slugify(text: str, max_len: int = 60) -> str:
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()
    text = re.sub(r"[^a-zA-Z0-9]+", "-", text).strip("-").lower()
    if len(text) > max_len:
        text = text[:max_len].rsplit("-", 1)[0] or text[:max_len]
    text = text or "note"
    # Windows can't create files with these names, with or without an extension
    if text in WINDOWS_RESERVED:
        text += "-note"
    return text


WINDOWS_RESERVED = frozenset({"con", "prn", "aux", "nul", *(f"com{i}" for i in range(10)), *(f"lpt{i}" for i in range(10))})


def _plain(v):
    """YAML values → JSON-safe ones (dates become ISO strings), recursively."""
    if isinstance(v, (dt.date, dt.datetime)):
        return v.isoformat()
    if isinstance(v, dict):
        return {str(k): _plain(x) for k, x in v.items()}
    if isinstance(v, (list, tuple)):
        return [_plain(x) for x in v]
    return v


class Store:
    def __init__(self, root: Path):
        from . import paths
        from .layout import Layout
        self.root = Path(root)
        self.docs = paths.documents_dir(self.root)  # Papers/, Notes/, Questions/, ... (app/layout.py)
        paths.remember_documents_dir(self.root, self.docs)
        self.layout = Layout(self.docs)
        self.layout.ensure()
        from .papers import folder_for  # <documents>/PDFs, or the papers folder chosen in settings (app/papers.py)
        self.pdf_dir = folder_for(self.root)
        self.text_dir = self.root / "text"
        self.index_dir = self.root / ".index"
        self.trash_dir = self.root / ".trash"
        for d in (self.pdf_dir, self.text_dir, self.index_dir, self.trash_dir):
            d.mkdir(parents=True, exist_ok=True)
        self.layout.scan()
        self.lock = threading.RLock()
        self.rev = 0  # bumped on every write; part of the index signature
        self.listeners: list = []  # fn(kind, note_id) after save/delete (see listen())
        # SQLite metadata/FTS index (app/meta.py): serves all()/find_duplicate()/signature() without
        # re-reading every file. None or .ok False -> the file-reading code below is used instead.
        self.meta = None
        try:
            from .meta import MetaIndex
            self.meta = MetaIndex(self)
        except Exception as e:
            print(f"[research_brain] metadata index disabled: {e!r}")

    @property
    def notes_dir(self) -> Path:
        """The Notes folder: where a note you write outside the app (in Obsidian, a text editor) goes."""
        return self.layout.type_dir("note")

    def _meta_ok(self) -> bool:
        return self.meta is not None and self.meta.ok

    def signature(self) -> str:
        """Changes whenever any note file changes (here or edited externally, e.g. in Obsidian)."""
        if self._meta_ok():
            return self.meta.signature()
        parts = [str(self.rev)] + [f"{nid}:{st[0]}:{st[1]}" for nid, st in sorted(self.layout.scan().items())]
        return "|".join(parts)

    # ---------- paths ----------
    def note_path(self, nid: str) -> Path:
        """The note's file in the documents folder (app/layout.py); for an id with no file, a path that
        doesn't exist."""
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", nid or ""):
            raise KeyError(nid)
        return self.layout.path(nid) or self.docs / ".no-such-note" / f"{nid}.md"

    def pdf_path(self, nid: str) -> Path:
        self.note_path(nid)  # validates id
        return self.pdf_dir / f"{nid}.pdf"

    def text_path(self, nid: str) -> Path:
        self.note_path(nid)
        return self.text_dir / f"{nid}.txt"

    # ---------- read/write ----------
    def _parse(self, nid: str, raw: str) -> dict:
        meta: dict = {}
        yaml_error = ""
        body = raw
        raw = raw.replace("\r\n", "\n")
        if raw.startswith("---\n"):
            end = raw.find("\n---\n", 3)
            if end == -1 and raw.rstrip().endswith("\n---"):
                end = len(raw.rstrip()) - 4
            if end != -1:
                try:
                    loaded = load_frontmatter(raw[4:end + 1])
                    meta = loaded if isinstance(loaded, dict) else {}
                except yaml.YAMLError as e:
                    meta = {}
                    yaml_error = str(e).splitlines()[0] if str(e) else "invalid YAML"
                body = raw[end + 5:]
        note = {k: (list(v) if isinstance(v, list) else v) for k, v in FIELDS_DEFAULTS.items()}
        for k in FIELDS_DEFAULTS:
            if k in meta and meta[k] is not None:
                note[k] = meta[k]
        note["id"] = nid
        note["body"] = body.rstrip("\n")
        # properties we don't know (added in Obsidian or another tool) ride along untouched and are
        # written back on save, so editing a note here never deletes them
        note["_extra"] = {str(k): _plain(v) for k, v in meta.items()
                          if k not in FIELDS_DEFAULTS and k not in ("id", "body") and not str(k).startswith("_")}
        # normalise types (files may be hand-edited, e.g. in Obsidian: `tags: foo`, `authors: 5`, `links: [a, null]`)
        for k in ("authors", "tags", "links", "cites"):
            v = note[k]
            if isinstance(v, str):
                v = [s.strip() for s in v.split(",")] if k != "authors" else [v]
            elif not isinstance(v, (list, tuple)):
                v = [] if v is None else [v]
            note[k] = [str(x) for x in v if x is not None and str(x).strip()]
        if not isinstance(note["critique"], dict):
            note["critique"] = None
        note["selector"] = normalize_selector(note.get("selector"))
        note["csl"] = _plain(note["csl"]) if isinstance(note.get("csl"), dict) and note["csl"] else None
        hist = note["critiques"] if isinstance(note["critiques"], list) else []
        hist = [c for c in hist if isinstance(c, dict) and c.get("text")]
        # migrate: a note written before critique history has only `critique`; it becomes the first entry
        if note["critique"] and note["critique"].get("text") and not any(
                c.get("at") == note["critique"].get("at") and c.get("text") == note["critique"]["text"] for c in hist):
            hist.append(note["critique"])
        hist.sort(key=lambda c: str(c.get("at") or ""))
        note["critiques"] = [{"text": str(c["text"]), "at": str(c.get("at") or ""),
                              "summary_snapshot": str(c.get("summary_snapshot") or "")} for c in hist]
        for k in ("year", "page"):
            try:
                note[k] = int(note[k]) if note[k] not in (None, "") else None
            except (TypeError, ValueError):
                note[k] = None
        for k in ("title", "venue", "doi", "arxiv", "s2_id", "url", "abstract", "summary", "created", "updated",
                  "parent", "quote", "locator", "zotero_key", "status", "saved_because", "meta_source", "meta_checked",
                  "question_status", "answer", "last_seen"):
            v = note[k]
            # Obsidian's Date & time properties are written unquoted, so YAML reads them as dates;
            # isoformat() keeps the "T" that str() would drop.
            note[k] = "" if v is None else v.isoformat() if isinstance(v, (dt.date, dt.datetime)) else str(v)
        if note["type"] not in TYPES:
            note["type"] = "note"
        if note["status"] not in STATUSES:
            note["status"] = ""
        if note["type"] == "question":
            if note["question_status"] not in QUESTION_STATUSES:
                note["question_status"] = "open"
        else:
            note["question_status"] = ""
        note["saved_because"] = " ".join(note["saved_because"].split())  # one line
        if not note["title"]:
            note["title"] = nid
        note["has_pdf"] = self.pdf_path(nid).exists()
        note["has_text"] = self.text_path(nid).exists()
        # a PDF attached later (extension "Save PDF", open-access download, upload) upgrades a linked paper;
        # the next save writes source: pdf
        src = str(note.get("source") or "").strip().lower()
        note["source"] = "pdf" if note["has_pdf"] else (src if src in SOURCES and src != "pdf" else "")
        if yaml_error:
            note["_yaml_error"] = yaml_error  # save() refuses to overwrite the file (see FrontmatterError)
        return note

    # always written, even empty; every other property is left out while empty, so a file shows only
    # what the note has (reading fills the rest in from FIELDS_DEFAULTS)
    ALWAYS = ("type", "title", "created", "updated")

    def _dump(self, note: dict) -> str:
        meta = {"id": note["id"]}
        for k in ("type", "title", *FIELDS_DEFAULTS):
            if k in meta:
                continue
            v = note.get(k, FIELDS_DEFAULTS[k])
            if k in self.ALWAYS or not (v is None or v == "" or v == [] or v == {}):
                meta[k] = v
        for k, v in (note.get("_extra") or {}).items():
            if k not in meta:
                meta[k] = v
        # one property per line (a mapping of plain values would otherwise collapse to one {...} line);
        # lists stay compact, as before ([a, b])
        def one(k, v) -> str:
            compact = isinstance(v, list) and all(not isinstance(x, (dict, list)) for x in v)
            return yaml.dump({k: v}, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True, width=1000,
                             default_flow_style=None if compact else False)
        front = "".join(one(k, v) for k, v in meta.items())
        body = note.get("body", "") or ""
        return f"---\n{front}---\n{body}\n"

    def get(self, nid: str) -> dict:
        path = self.note_path(nid)
        if not path.exists():
            raise KeyError(nid)
        return self._parse(nid, path.read_text(encoding="utf-8"))

    def exists(self, nid: str) -> bool:
        try:
            return self.note_path(nid).exists()
        except KeyError:
            return False

    def save(self, note: dict, touch: bool = True) -> dict:
        """Write the note. touch=False keeps its `updated` time (bookkeeping such as a citation key)."""
        with self.lock:
            if note.get("_yaml_error"):
                raise FrontmatterError(f"{self.note_path(note['id']).name} has invalid frontmatter ({note['_yaml_error']}). "
                                       "Fix the file in a text editor; saving now would erase its properties.")
            if touch or not note.get("updated"):
                note["updated"] = now_iso()
            self.note_path(note["id"])  # validates the id
            current = self.layout.current(note["id"])  # no disk walk for a brand-new note
            path = self.layout.target(note, current)  # its type's folder, named after its title
            path.parent.mkdir(parents=True, exist_ok=True)
            tmp = path.with_name(f".{path.name}.tmp")  # hidden, so watchers and Obsidian ignore it
            tmp.write_text(self._dump(note), encoding="utf-8", newline="\n")
            fsio.replace(tmp, path)
            if current is not None and current != path:  # renamed (title changed) or moved to its folder
                try:
                    if not os.path.samefile(current, path):
                        current.unlink()
                    elif current.name != path.name:  # only the case changed: one file on macOS and Windows,
                        mid = path.with_name(f".{path.name}.case")  # so rename it through another name
                        os.replace(current, mid)
                        os.replace(mid, path)
                except OSError:
                    pass
            self.layout.remember(note["id"], path)
            if note.get("type") == "paper" and current is not None and current.stem != path.stem:
                self.layout.move_highlights(current.stem, path.stem)  # its highlights folder follows
            self.rev += 1
            saved = self.get(note["id"])
            if self.meta is not None:
                self.meta.put(saved)
        self._notify("saved", saved["id"])
        return saved

    def listen(self, fn) -> None:
        """fn(kind, note_id) after every save ("saved") or delete ("deleted"), outside the store lock.
        Listeners must be quick (e.g. poke a background worker) and never raise into the caller."""
        self.listeners.append(fn)

    def _notify(self, kind: str, nid: str) -> None:
        for fn in list(getattr(self, "listeners", ())):
            try:
                fn(kind, nid)
            except Exception as e:
                print(f"[research_brain] store listener failed: {e!r}")

    def all(self) -> list[dict]:
        if self._meta_ok():
            return self.meta.all()
        return self.all_files()

    def all_files(self) -> list[dict]:
        """Every note parsed straight from its file (the fallback when the SQLite index is unavailable)."""
        out = []
        for nid in sorted(self.layout.scan()):
            p = self.layout.path(nid)
            try:
                out.append(self._parse(nid, p.read_text(encoding="utf-8")))
            except (KeyError, OSError, UnicodeDecodeError, AttributeError):
                continue
            except Exception as e:  # one unreadable hand-edited file must not take down the whole library
                print(f"[research_brain] skipping {p}: {e!r}")
                continue
        return out

    def new_id(self, title: str, year: int | None = None) -> str:
        base = slugify(title)
        if year:
            base = f"{base}-{year}"
        nid, i = base, 2
        with self.lock:
            self.layout.scan()  # once; then the map answers (no disk walk per candidate)
            while self.layout.known(nid):
                nid = f"{base}-{i}"
                i += 1
            return nid

    def create(self, fields: dict) -> dict:
        with self.lock:
            note = {k: (list(v) if isinstance(v, list) else v) for k, v in FIELDS_DEFAULTS.items()}
            note["body"] = ""
            for k, v in fields.items():
                if k in FIELDS_DEFAULTS or k == "body":
                    note[k] = v
            note["title"] = (note.get("title") or "").strip() or "Untitled"
            if note.get("type") not in TYPES:
                note["type"] = "note"
            if note["type"] == "paper" and "status" not in fields:
                note["status"] = "to-read"  # a newly added paper is waiting to be read
            if note["type"] == "question" and note.get("question_status") not in QUESTION_STATUSES:
                note["question_status"] = "open"
            note["links"] = []
            if fields.get("private") is not None:
                set_private(note, fields["private"])
            if note["type"] == "fragment":
                base = f"{note['parent']}-{slugify(note['title'], 30)}"
                note["id"] = self.new_id(base[:90])
            else:
                note["id"] = self.new_id(note["title"], note.get("year") if note["type"] == "paper" else None)
            note["created"] = now_iso()
            # reserve the file immediately so concurrent creates don't collide
            return self.save(note)

    def update(self, nid: str, fields: dict) -> dict:
        with self.lock:
            note = self.get(nid)
            if "private" in fields:  # app/privacy.py: true/false in the frontmatter, None = the type's default
                set_private(note, fields["private"])
            for k, v in fields.items():
                if k not in EDITABLE:
                    continue
                if k in ("tags", "authors"):
                    if isinstance(v, str):
                        v = [s.strip() for s in v.split(",")]
                    elif not isinstance(v, (list, tuple)):
                        v = []
                    v = [str(s).strip() for s in (v or []) if str(s).strip()]
                elif k not in ("year", "page"):
                    v = "" if v is None else v if isinstance(v, str) else str(v)
                if k in ("year", "page"):
                    try:
                        v = int(v) if v not in (None, "") else None
                    except (TypeError, ValueError):
                        v = None
                if k == "title":
                    v = (v or "").strip() or note["title"]
                if k == "status" and v not in STATUSES and v != "":
                    raise ValueError(f"status must be one of {', '.join(STATUSES)} (or empty)")
                if k == "question_status":
                    if note["type"] != "question":
                        continue
                    if v not in QUESTION_STATUSES:
                        raise ValueError(f"question_status must be one of {', '.join(QUESTION_STATUSES)}")
                if k == "saved_because":
                    v = " ".join(v.split())[:300]
                note[k] = v
            if note.get("csl"):  # the CSL record follows edits of title/authors/year/venue/... (app/metadata.py)
                from .metadata import sync_edit
                sync_edit(note, [k for k in fields if k in EDITABLE])
            return self.save(note)

    def set_fields(self, nid: str, **fields) -> dict:
        """Set non-editable/internal fields (s2_id, doi, critique...)."""
        with self.lock:
            note = self.get(nid)
            note.update(fields)
            return self.save(note)

    def children(self, nid: str) -> list[dict]:
        if self._meta_ok():
            kids = self.meta.children(nid)
        else:
            kids = [n for n in self.all() if n["type"] == "fragment" and n["parent"] == nid]
        kids.sort(key=lambda n: (n["page"] if n["page"] is not None else 10**6, n["created"]))
        return kids

    def delete(self, nid: str) -> None:
        with self.lock:
            for kid in self.children(nid):
                self.delete(kid["id"])
            note = self.get(nid)
            stamp = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
            dest = self.trash_dir / f"{nid}-{stamp}"
            dest.mkdir(parents=True, exist_ok=True)
            src = self.note_path(nid)
            shutil.move(str(src), dest / f"{nid}.md")
            self.layout.forget(nid)
            try:  # a highlights folder left empty goes too
                if note["type"] == "fragment" and src.parent != self.layout.type_dir("fragment") \
                        and not any(src.parent.iterdir()):
                    src.parent.rmdir()
            except OSError:
                pass
            self.rev += 1
            if self.meta is not None:
                self.meta.remove(nid)
            for p in (self.pdf_path(nid), self.text_path(nid)):
                if p.exists():
                    shutil.move(str(p), dest / p.name)
            for other in note["links"]:
                if self.exists(other):
                    o = self.get(other)
                    if o.get("_yaml_error"):
                        continue  # never rewrite a file we couldn't parse
                    o["links"] = [x for x in o["links"] if x != nid]
                    self.save(o)
        self._notify("deleted", nid)

    def link(self, a: str, b: str) -> dict:
        if a == b:
            raise ValueError("Cannot link a note to itself")
        with self.lock:
            na, nb = self.get(a), self.get(b)
            if b not in na["links"]:
                na["links"].append(b)
                self.save(na)
            if a not in nb["links"]:
                nb["links"].append(a)
                self.save(nb)
            return self.get(a)

    def unlink(self, a: str, b: str) -> dict:
        with self.lock:
            na = self.get(a)
            na["links"] = [x for x in na["links"] if x != b]
            self.save(na)
            if self.exists(b):
                nb = self.get(b)
                nb["links"] = [x for x in nb["links"] if x != a]
                self.save(nb)
            return self.get(a)

    def find_duplicate(self, doi: str = "", arxiv: str = "", s2_id: str = "") -> dict | None:
        if self._meta_ok():
            return self.meta.find_duplicate(doi, arxiv, s2_id)
        doi, arxiv = (doi or "").lower(), (arxiv or "").lower()
        for n in self.all():
            if s2_id and n["s2_id"] == s2_id:
                return n
            if doi and n["doi"].lower() == doi:
                return n
            if arxiv and n["arxiv"].lower() == arxiv:
                return n
        return None

    # ---------- small JSON files: derived caches in .index, user state in state/ ----------
    STATE_FILES = ("review.json", "engagement.json")

    def _json_path(self, name: str) -> Path:
        if name not in self.STATE_FILES:
            return self.index_dir / name
        state = self.root / "state"
        state.mkdir(exist_ok=True)
        p = state / name
        old = self.index_dir / name
        if not p.exists() and old.exists():  # migrate from the older layout, once
            try:
                old.replace(p)
            except OSError:
                return old
        return p

    def load_json(self, name: str) -> dict:
        p = self._json_path(name)
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError):
                return {}
        return {}

    def save_json(self, name: str, data: dict) -> None:
        with self.lock:
            p = self._json_path(name)
            tmp = p.with_suffix(".tmp")
            tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8", newline="\n")
            fsio.replace(tmp, p)

"""SQLite metadata index + full-text search over the vault's notes.

The Markdown files in vault/notes/ stay the source of truth. This module keeps a derived copy in
vault/.index/meta.sqlite (safe to delete; it is rebuilt from the files):

    notes      one row per note: the parsed note as JSON, plus the file's mtime_ns and size and a few
               lookup columns (type, url, doi, arxiv, s2_id, parent, updated)
    notes_fts  FTS5 over title, summary, body, quote, abstract, tags, authors (prefix-indexed)

Freshness: `sync()` lists notes/ (one os.scandir, no file reads) and re-parses only files whose
mtime or size changed, so edits made outside the app (Obsidian, git pull, a text editor) show up on
the next request. Store.save()/delete() also update rows directly. A parsed copy of every note is
kept in memory, so `all()` costs a directory listing rather than 2,000 YAML parses.

If the database is missing, from an older schema, or corrupt, it is deleted and rebuilt. If SQLite
can't be used at all, the Store falls back to reading the files.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import time
from pathlib import Path

SCHEMA_VERSION = 1
FTS_COLUMNS = ("title", "summary", "body", "quote", "abstract", "tags", "authors")
# bm25 column weights, in FTS_COLUMNS order: a hit in the title counts most, then tags/authors
BM25_WEIGHTS = (10.0, 2.0, 1.0, 1.5, 1.0, 4.0, 4.0)
SELF_WRITE_TTL = 2.0  # seconds a write made by the app itself is remembered (to ignore its watcher event)

_TOKEN_RE = re.compile(r"[\w][\w\-']*", re.UNICODE)


class MetaIndex:
    def __init__(self, store):
        self.store = store
        self.path = Path(store.index_dir) / "meta.sqlite"
        self.lock = threading.RLock()
        self.conn: sqlite3.Connection | None = None
        self.ok = False                      # False -> callers fall back to reading files
        self.notes: dict[str, dict] = {}     # id -> parsed note (has_pdf/has_text refreshed on sync)
        self.stat: dict[str, tuple[int, int]] = {}  # id -> (mtime_ns, size) the row was built from
        self.gen = 0                         # bumped whenever anything visible changes
        self._assets: tuple[frozenset, frozenset] = (frozenset(), frozenset())
        self._self_writes: dict[str, tuple[float, tuple[int, int] | None]] = {}
        self._external: set[str] = set()  # ids sync() found changed on disk, not yet reported (live.py)
        # many notes missing from one scan: held until a later scan confirms it (iCloud Drive, a folder being
        # moved or backed up can make files vanish for a moment; dropping them emptied the whole graph)
        self._missing: tuple[frozenset, float] | None = None
        self.rebuilds = 0
        self._fields_sig = hashlib.sha1(",".join(sorted(store_fields(store))).encode()).hexdigest()[:12]
        self._open()

    # ------------------------------------------------------------ open / rebuild
    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self.path, check_same_thread=False, isolation_level=None, timeout=10)
        conn.execute("PRAGMA journal_mode=WAL")
        conn.execute("PRAGMA synchronous=NORMAL")
        return conn

    def _create(self, conn: sqlite3.Connection) -> None:
        conn.executescript(f"""
            CREATE TABLE IF NOT EXISTS info (k TEXT PRIMARY KEY, v TEXT);
            CREATE TABLE IF NOT EXISTS notes (
                rid INTEGER PRIMARY KEY, id TEXT UNIQUE NOT NULL, mtime_ns INTEGER, size INTEGER,
                type TEXT, title TEXT, year INTEGER, updated TEXT, parent TEXT, url_norm TEXT,
                doi_l TEXT, arxiv_l TEXT, s2_id TEXT, zotero_key TEXT, data TEXT NOT NULL);
            CREATE INDEX IF NOT EXISTS notes_doi ON notes(doi_l) WHERE doi_l != '';
            CREATE INDEX IF NOT EXISTS notes_arxiv ON notes(arxiv_l) WHERE arxiv_l != '';
            CREATE INDEX IF NOT EXISTS notes_s2 ON notes(s2_id) WHERE s2_id != '';
            CREATE INDEX IF NOT EXISTS notes_url ON notes(url_norm) WHERE url_norm != '';
            CREATE INDEX IF NOT EXISTS notes_parent ON notes(parent) WHERE parent != '';
            CREATE VIRTUAL TABLE IF NOT EXISTS notes_fts USING fts5(
                {", ".join(FTS_COLUMNS)}, tokenize='unicode61 remove_diacritics 2', prefix='2 3 4');
            PRAGMA user_version = {SCHEMA_VERSION};
        """)
        conn.execute("INSERT OR REPLACE INTO info VALUES ('fields', ?)", (self._fields_sig,))

    def _delete_files(self) -> None:
        for suffix in ("", "-wal", "-shm", "-journal"):
            try:
                Path(str(self.path) + suffix).unlink()
            except FileNotFoundError:
                pass
            except OSError:
                pass

    def _reset_in_place(self) -> None:
        try:
            conn = self._connect()
            try:
                names = [r[0] for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type IN ('table','view') AND name NOT LIKE 'sqlite_%' "
                    "AND name NOT LIKE 'notes_fts_%'")]
                for n in names:
                    conn.execute(f'DROP TABLE IF EXISTS "{n}"')
                conn.execute("PRAGMA user_version = 0")
            finally:
                conn.close()
        except sqlite3.Error:
            pass

    def _open(self) -> None:
        """Open (or create) the database and load it into memory; delete-and-rebuild on any problem.
        On Windows a damaged file another connection still holds can be neither deleted nor emptied: then the
        index is rebuilt in a fresh file beside it (meta.alt.sqlite), and the stale one is removed later."""
        with self.lock:
            main = self.path.with_name("meta.sqlite")
            alt = main.with_name("meta.alt.sqlite")
            if self.path == main and alt.exists() and not main.exists():
                self.path = alt  # the main file went away; keep using the copy that has the rows
            for attempt in (0, 1, 2):
                try:
                    self.conn = self._connect()
                    ver = self.conn.execute("PRAGMA user_version").fetchone()[0]
                    tables = {r[0] for r in self.conn.execute("SELECT name FROM sqlite_master")}
                    if ver != SCHEMA_VERSION or "notes" not in tables or "notes_fts" not in tables:
                        if tables - {"sqlite_sequence"}:
                            raise sqlite3.DatabaseError(f"schema {ver} != {SCHEMA_VERSION}")
                        self._create(self.conn)
                    row = self.conn.execute("SELECT v FROM info WHERE k='fields'").fetchone()
                    if not row or row[0] != self._fields_sig:  # note fields changed: rows are stale
                        raise sqlite3.DatabaseError("note fields changed")
                    if self.conn.execute("PRAGMA quick_check").fetchone()[0] != "ok":
                        raise sqlite3.DatabaseError("quick_check failed")
                    self._load()
                    self.ok = True
                    if self.path == main and alt.exists():  # an earlier fallback copy is no longer needed
                        for suffix in ("", "-wal", "-shm", "-journal"):
                            try:
                                Path(str(alt) + suffix).unlink()
                            except OSError:
                                pass
                    break
                except (sqlite3.DatabaseError, json.JSONDecodeError, OSError) as e:
                    self._close()
                    if attempt == 1 and self.path == main and main.exists():
                        print(f"[research_brain] {main.name} is held open elsewhere; rebuilding into {alt.name}")
                        self.path = alt
                        self._delete_files()
                        self.rebuilds += 1
                        continue
                    if attempt >= 1:
                        print(f"[research_brain] metadata index unavailable ({e!r}); reading note files directly")
                        self.ok = False
                        return
                    if self.path.exists():
                        print(f"[research_brain] rebuilding {self.path.name} ({e})")
                    self._delete_files()
                    if self.path.exists():  # Windows: another connection holds it open, so empty it in place
                        self._reset_in_place()
                    self.rebuilds += 1
            self.sync(force=True)
            self._external.clear()  # the startup catch-up isn't news to anyone

    def _close(self) -> None:
        if self.conn is not None:
            try:
                self.conn.close()
            except sqlite3.Error:
                pass
        self.conn = None
        self.notes, self.stat = {}, {}

    def _load(self) -> None:
        notes, stat = {}, {}
        for nid, mt, sz, data in self.conn.execute("SELECT id, mtime_ns, size, data FROM notes"):
            notes[nid] = json.loads(data)
            stat[nid] = (mt, sz)
        self.notes, self.stat = notes, stat

    def _recover(self, err: Exception) -> None:
        """A query failed mid-run (file deleted or corrupted underneath us): rebuild from the files."""
        print(f"[research_brain] metadata index error ({err!r}); rebuilding")
        with self.lock:
            self._close()
            self._delete_files()
            self.rebuilds += 1
            self.gen += 1
            self._open()

    # ------------------------------------------------------------ rows
    def _row(self, n: dict, st: tuple[int, int]) -> tuple:
        return (n["id"], st[0], st[1], n["type"], n["title"], n["year"], n["updated"], n["parent"],
                (n["url"] or "").rstrip("/"), (n["doi"] or "").lower(), (n["arxiv"] or "").lower(),
                n["s2_id"] or "", n.get("zotero_key") or "", json.dumps(n, ensure_ascii=False))

    def _fts(self, n: dict) -> tuple:
        return tuple(" ".join(n[c]) if c in ("tags", "authors") else (n.get(c) or "") for c in FTS_COLUMNS)

    def _put_rows(self, items: list[tuple[dict, tuple[int, int]]]) -> None:
        c = self.conn
        c.execute("BEGIN")
        try:
            for n, st in items:
                old = c.execute("SELECT rid FROM notes WHERE id=?", (n["id"],)).fetchone()
                if old:
                    c.execute("DELETE FROM notes_fts WHERE rowid=?", (old[0],))
                    c.execute("DELETE FROM notes WHERE rid=?", (old[0],))
                cur = c.execute("INSERT INTO notes (id, mtime_ns, size, type, title, year, updated, parent, url_norm, "
                                "doi_l, arxiv_l, s2_id, zotero_key, data) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                                self._row(n, st))
                c.execute(f"INSERT INTO notes_fts (rowid, {', '.join(FTS_COLUMNS)}) VALUES (?{',?' * len(FTS_COLUMNS)})",
                          (cur.lastrowid, *self._fts(n)))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    def _drop_rows(self, ids: list[str]) -> None:
        c = self.conn
        c.execute("BEGIN")
        try:
            for nid in ids:
                old = c.execute("SELECT rid FROM notes WHERE id=?", (nid,)).fetchone()
                if old:
                    c.execute("DELETE FROM notes_fts WHERE rowid=?", (old[0],))
                    c.execute("DELETE FROM notes WHERE rid=?", (old[0],))
            c.execute("COMMIT")
        except BaseException:
            c.execute("ROLLBACK")
            raise

    # ------------------------------------------------------------ sync
    def _scan(self) -> dict[str, tuple[int, int]]:
        """{id: (mtime_ns, size)} of every note file (app/layout.py: readable folders, ids from the files)."""
        return self.store.layout.scan()

    def _asset_names(self) -> tuple[frozenset, frozenset]:
        def names(d: Path, ext: str) -> frozenset:
            try:
                with os.scandir(d) as it:
                    return frozenset(e.name[:-len(ext)] for e in it if e.name.endswith(ext))
            except OSError:
                return frozenset()
        return names(self.store.pdf_dir, ".pdf"), names(self.store.text_dir, ".txt")

    def sync(self, force: bool = False, only: set[str] | None = None) -> list[str]:
        """Bring rows in line with the files. Returns ids that changed (added, edited or removed)."""
        if not self.ok and not force:
            return []
        with self.lock:
            try:
                disk = self._scan()
                if only is not None:  # targeted resync (from the file watcher)
                    disk = {k: v for k, v in disk.items() if k in only}
                    known = {k: v for k, v in self.stat.items() if k in only}
                else:
                    known = self.stat
                changed = [nid for nid, st in disk.items() if known.get(nid) != st]
                removed = [nid for nid in known if nid not in disk]
                removed = self._confirm_missing(removed, len(known))
                puts = []
                for nid in changed:
                    n = self._parse_file(nid)
                    if n is None:
                        removed.append(nid) if nid in self.stat else None
                        continue
                    puts.append((n, disk[nid]))
                if self.conn is not None:
                    if puts:
                        self._put_rows(puts)
                    if removed:
                        self._drop_rows(removed)
                for n, st in puts:
                    self.notes[n["id"]] = n
                    self.stat[n["id"]] = st
                for nid in removed:
                    self.notes.pop(nid, None)
                    self.stat.pop(nid, None)
                assets = self._asset_names()
                assets_changed = assets != self._assets
                if assets_changed or puts:
                    self._assets = assets
                    pdfs, texts = assets
                    for n in self.notes.values():
                        n["has_pdf"] = n["id"] in pdfs
                        n["has_text"] = n["id"] in texts
                        if n["has_pdf"]:
                            n["source"] = "pdf"  # a PDF attached to a linked paper upgrades it
                        elif n.get("source") == "pdf":
                            n["source"] = ""
                ids = [n["id"] for n, _ in puts] + removed
                self._external.update(ids)  # changed on disk by someone other than Store.save/delete
                if ids or assets_changed:
                    self.gen += 1
                return ids
            except sqlite3.DatabaseError as e:
                self._recover(e)
                return list(self.notes)

    MASS_MISSING = 5          # this many notes gone in one scan (and at least a third of them) needs a second look
    MISSING_CONFIRM_S = 4.0

    def _confirm_missing(self, removed: list[str], known: int) -> list[str]:
        """Notes missing from the folder. A few go at once (you deleted them): gone now. Many at once is held
        until a scan at least MISSING_CONFIRM_S later still misses them, so a moment when the files can't be
        seen (iCloud Drive syncing, a folder being renamed or backed up) doesn't empty the library."""
        if len(removed) < self.MASS_MISSING or len(removed) * 3 < known:
            self._missing = None
            return removed
        now = time.monotonic()
        gone = frozenset(removed)
        if self._missing is None or not (gone & self._missing[0]):
            self._missing = (gone, now)
            print(f"[research_brain] {len(removed)} notes are missing from the documents folder; "
                  "checking again before removing them")
            return []
        first, since = self._missing
        if now - since < self.MISSING_CONFIRM_S:
            return []
        self._missing = None
        return [nid for nid in removed if nid in first]

    def _parse_file(self, nid: str) -> dict | None:
        try:
            raw = self.store.note_path(nid).read_text(encoding="utf-8")
            return self.store._parse(nid, raw)
        except (KeyError, OSError, UnicodeDecodeError):
            return None
        except Exception as e:  # one unreadable hand-edited file must not take down the whole library
            print(f"[research_brain] skipping note {nid}: {e!r}")
            return None

    # ------------------------------------------------------------ hooks from Store
    def put(self, note: dict) -> None:
        """Store.save() just wrote this note (and re-parsed it)."""
        if not self.ok:
            return
        with self.lock:
            try:
                s = self.store.note_path(note["id"]).stat()
                st = (s.st_mtime_ns, s.st_size)
            except (OSError, KeyError):
                return
            self._self_writes[note["id"]] = (time.monotonic(), st)
            self._external.discard(note["id"])
            n = dict(note)
            try:
                self._put_rows([(n, st)])
            except sqlite3.DatabaseError as e:
                self._recover(e)
                return
            self.notes[n["id"]] = n
            self.stat[n["id"]] = st
            self.gen += 1

    def remove(self, nid: str) -> None:
        """Store.delete() just moved this note to the trash."""
        if not self.ok:
            return
        with self.lock:
            self._self_writes[nid] = (time.monotonic(), None)
            self._external.discard(nid)
            try:
                self._drop_rows([nid])
            except sqlite3.DatabaseError as e:
                self._recover(e)
                return
            self.notes.pop(nid, None)
            self.stat.pop(nid, None)
            self.gen += 1

    def pop_external(self, ids: set[str] | None = None) -> list[str]:
        """Of `ids` (None: all), those whose files changed outside the app since last asked (whichever
        request noticed it first), minus files that are exactly what the app itself just wrote."""
        with self.lock:
            hit = sorted(i for i in (self._external if ids is None else ids) if i in self._external)
            self._external.difference_update(hit)
        return [i for i in hit if not self.is_self_write(i)]

    def is_self_write(self, nid: str) -> bool:
        """True if the file's current state is exactly what the app itself wrote in the last ~2 s."""
        with self.lock:
            now = time.monotonic()
            for k in [k for k, (t, _) in self._self_writes.items() if now - t > SELF_WRITE_TTL]:
                del self._self_writes[k]
            hit = self._self_writes.get(nid)
            if not hit:
                return False
            try:
                s = self.store.note_path(nid).stat()
                cur = (s.st_mtime_ns, s.st_size)
            except (OSError, KeyError):
                cur = None
            return cur == hit[1]

    # ------------------------------------------------------------ queries
    def all(self) -> list[dict]:
        """Every note, parsed, as fresh copies (callers may mutate them). Sorted by id like Store.all()."""
        self.sync()
        with self.lock:
            notes = [self.notes[k] for k in sorted(self.notes)]
        return [_copy(n) for n in notes]

    def signature(self) -> str:
        self.sync()
        return f"m{self.gen}"

    def find_duplicate(self, doi: str, arxiv: str, s2_id: str) -> dict | None:
        self.sync()
        conds, args = [], []
        for col, v in (("s2_id", s2_id or ""), ("doi_l", (doi or "").lower()), ("arxiv_l", (arxiv or "").lower())):
            if v:
                conds.append(f"{col} = ?")
                args.append(v)
        if not conds:
            return None
        return self._first(f"SELECT id FROM notes WHERE {' OR '.join(conds)} ORDER BY id LIMIT 1", args)

    def match_url(self, url: str, kind: str, value: str) -> dict | None:
        """Same rule as the old linear scan: the first non-fragment (by id) whose url equals the page url
        (ignoring a trailing slash), or whose arXiv id / DOI equals the one parsed from it."""
        self.sync()
        conds, args = ["(url_norm != '' AND url_norm = ?)"], [url.rstrip("/")]
        if kind in ("arxiv", "doi") and value:
            conds.append(f"{'arxiv_l' if kind == 'arxiv' else 'doi_l'} = ?")
            args.append(value.lower())
        return self._first(f"SELECT id FROM notes WHERE type != 'fragment' AND ({' OR '.join(conds)}) "
                           f"ORDER BY id LIMIT 1", args)

    def children(self, nid: str) -> list[dict]:
        self.sync()
        with self.lock:
            kids = [n for n in self.notes.values() if n["type"] == "fragment" and n["parent"] == nid]
        return [_copy(n) for n in kids]

    def _first(self, sql: str, args: list) -> dict | None:
        with self.lock:
            try:
                row = self.conn.execute(sql, args).fetchone()
            except sqlite3.DatabaseError as e:
                self._recover(e)
                return None
            n = self.notes.get(row[0]) if row else None
            return _copy(n) if n else None

    def search(self, q: str, limit: int = 20, fragments: bool = False) -> list[dict] | None:
        """FTS5 search with prefix matching on every word, ranked by bm25 (title boosted).
        Returns None when the query has no searchable words (caller falls back to substring match)."""
        words = _TOKEN_RE.findall(q or "")
        if not words:
            return None
        self.sync()
        match = " ".join('"' + w.replace('"', '""') + '"*' for w in words)
        weights = ", ".join(str(w) for w in BM25_WEIGHTS)
        frag_filter = "" if fragments else "AND n.type != 'fragment'"
        # a title that starts with the query still wins outright (as the old substring search did);
        # everything else is ordered by bm25 with the title column weighted highest
        sql = (f"SELECT n.id, n.title, n.type, n.year FROM notes_fts JOIN notes n ON n.rid = notes_fts.rowid "
               f"WHERE notes_fts MATCH ? {frag_filter} "
               f"ORDER BY (lower(n.title) LIKE ? ESCAPE '\\') DESC, bm25(notes_fts, {weights}), n.title LIMIT ?")
        like = re.sub(r"([%_\\])", r"\\\1", q.strip().lower()) + "%"
        with self.lock:
            try:
                rows = self.conn.execute(sql, (match, like, limit)).fetchall()
            except sqlite3.OperationalError:
                return None  # e.g. an FTS syntax edge case: fall back to substring search
            except sqlite3.DatabaseError as e:
                self._recover(e)
                return None
        return [{"id": r[0], "title": r[1], "type": r[2], "year": r[3]} for r in rows]

    def recent(self, limit: int = 20, fragments: bool = False) -> list[dict]:
        self.sync()
        with self.lock:
            notes = [n for n in self.notes.values() if fragments or n["type"] != "fragment"]
        notes.sort(key=lambda n: n["updated"] or "", reverse=True)
        return [{"id": n["id"], "title": n["title"], "type": n["type"], "year": n["year"]} for n in notes[:limit]]


def store_fields(store) -> list[str]:
    from .store import FIELDS_DEFAULTS
    return list(FIELDS_DEFAULTS)


def _copy(n: dict) -> dict:
    return {k: (list(v) if isinstance(v, list) else dict(v) if isinstance(v, dict) else v) for k, v in n.items()}

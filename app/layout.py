"""Where each note's file lives: readable folders and names you can find on your computer.

The documents folder of a library (app/paths.py documents_dir: ~/Documents/research brain for a normal
install, else the library folder itself) holds what you made, as Markdown files named by their titles:

    Papers/Helium I line intensity ratios in a plasma (1996).md
    Highlights/Helium I line intensity ratios in a plasma (1996)/p.2 CRM was used to compute.md
    Notes/My idea.md
    Questions/What are atomic cross sections.md
    Topics/crm-ml.md            (app/topics.py)
    PDFs/<id>.pdf               (the papers folder, app/papers.py)

A note's id is written in its file (`id:` in the frontmatter), so links, topics, citations and every
other reference keep working when a file is renamed (its title changed) or moved by hand. A file without
an id (made in another app) gets one from its file name, and keeps it from its next save.

Saving a note puts it where it belongs: in its type's folder (a note you moved into a subfolder of that
folder stays in that subfolder), named after its title, which is followed when the title changes. A
highlight lives in a folder named after its paper, and that folder follows the paper's name.

The app's own data (the search index, extracted text, settings, trash) stays in the library folder, which
is not the documents folder for a normal install (app/store.py).
"""
from __future__ import annotations

import os
import re
import threading
import time
from pathlib import Path

TYPE_DIRS = {"paper": "Papers", "note": "Notes", "question": "Questions", "fragment": "Highlights"}
TOPICS_DIR = "Topics"
PDFS_DIR = "PDFs"
DOC_DIRS = (*TYPE_DIRS.values(), TOPICS_DIR, PDFS_DIR)

_ID_RE = re.compile(r"^id:[ \t]*['\"]?([a-z0-9][a-z0-9-]*)['\"]?[ \t]*$", re.M)
# not allowed in file names on Windows or macOS, or awkward in Obsidian links ([ ] # ^ |)
_BAD = re.compile(r"[\x00-\x1f\x7f/\\:*?\"<>|#^\[\]]")
_RESERVED = re.compile(r"^(con|prn|aux|nul|com\d|lpt\d)$", re.I)
NAME_MAX = 90


def safe_name(text: str, max_len: int = NAME_MAX) -> str:
    """A title as a file name on every system: no / \\ : * ? " < > | # ^ [ ], no control characters,
    no leading or trailing dots and spaces, at most max_len characters (cut at a word)."""
    s = _BAD.sub(" ", str(text or ""))
    s = re.sub(r"\s+", " ", s).strip(" .")
    if len(s) > max_len:
        s = s[:max_len].rsplit(" ", 1)[0].rstrip(" .") or s[:max_len].rstrip(" .")
    if not s or _RESERVED.match(s):
        s = (s + " note").strip() if s else "Untitled"
    return s


def note_name(note: dict) -> str:
    """The file name (without .md) a note gets from its title."""
    title = str(note.get("title") or "").strip() or "Untitled"
    if note.get("type") == "paper" and note.get("year") and str(note["year"]) not in title:
        return safe_name(f"{safe_name(title, NAME_MAX - 7)} ({note['year']})")
    if note.get("type") == "fragment":
        quote = " ".join(str(note.get("quote") or "").split()[:8])
        if quote and quote.lower() not in title.lower():
            return safe_name(f"{title} {quote}")
    return safe_name(title)


def read_id(path: Path) -> str | None:
    """The `id:` in a note file's frontmatter, reading only its start."""
    try:
        with open(path, "r", encoding="utf-8", errors="replace") as f:
            head = f.read(65536)
    except OSError:
        return None
    head = head.replace("\r\n", "\n")
    if not head.startswith("---\n"):
        return None
    end = head.find("\n---", 3)
    m = _ID_RE.search(head[4:end] if end != -1 else head[4:])
    return m.group(1) if m else None


def _same(a: Path, b: Path) -> bool:
    try:
        return os.path.samefile(a, b)
    except OSError:
        return False


class Layout:
    """The id <-> file map for one documents folder, and where a note's file should be."""

    def __init__(self, docs: Path):
        self.docs = Path(docs)
        self.lock = threading.RLock()
        self._by_id: dict[str, Path] = {}
        self._info: dict[str, tuple[int, int, str, Path]] = {}  # path -> (mtime_ns, size, id, Path): ids read once per change
        self._scanned = 0.0
        self._dirs: dict[str, int] = {}  # every folder scanned -> its mtime (a file added, removed or renamed changes it)
        self._listing: dict[str, tuple[int, list[str], list[str]]] = {}  # folder -> (mtime, .md files, subfolders)
        self._warned: set[Path] = set()

    def type_dir(self, typ: str) -> Path:
        return self.docs / TYPE_DIRS.get(typ, TYPE_DIRS["note"])

    def ensure(self) -> None:
        for name in DOC_DIRS:
            (self.docs / name).mkdir(parents=True, exist_ok=True)

    # ---------------------------------------------------------------- reading the disk
    def _walk(self, dirs: dict[str, int]):
        """(path, stat) of every note file under the type folders; records each folder's mtime in `dirs`.
        A folder whose mtime hasn't moved isn't listed again: its files (and subfolders) are as last time,
        and only their stat is taken, to see edits."""
        with self.lock:
            listing = self._listing
        new_listing: dict[str, tuple[int, list[str], list[str]]] = {}
        stack = [str(self.docs / name) for name in reversed(TYPE_DIRS.values())]
        while stack:
            d = stack.pop()
            try:
                m = os.stat(d).st_mtime_ns
            except OSError:
                continue
            dirs[d] = m
            cached = listing.get(d)
            if cached is not None and cached[0] == m:
                files, subs = cached[1], cached[2]
            else:
                try:
                    with os.scandir(d) as it:
                        entries = sorted(it, key=lambda e: e.name)
                except OSError:
                    continue
                files, subs = [], []
                for e in entries:
                    if e.name.startswith("."):
                        continue
                    try:
                        if e.is_dir(follow_symlinks=False):
                            subs.append(e.path)
                        elif e.name.endswith(".md"):
                            files.append(e.path)
                    except OSError:
                        continue
            new_listing[d] = (m, files, subs)
            for f in files:
                try:
                    yield f, os.stat(f)
                except OSError:
                    continue
            stack.extend(reversed(subs))
        with self.lock:
            self._listing = new_listing

    def _dirs_changed(self) -> bool:
        """Whether a file was added, removed or renamed since the last scan (any scanned folder's mtime moved)."""
        with self.lock:
            dirs = dict(self._dirs)
        if not dirs:
            return True
        for d, m in dirs.items():
            try:
                if os.stat(d).st_mtime_ns != m:
                    return True
            except OSError:
                return True
        return False

    def scan(self) -> dict[str, tuple[int, int]]:
        """Every note file under the type folders: {id: (mtime_ns, size)}. Refreshes the id map."""
        from .store import slugify
        found: dict[str, tuple[int, int, Path]] = {}
        info: dict[str, tuple[int, int, str, Path]] = {}
        unnamed: list[tuple[Path, int, int]] = []
        with self.lock:
            old = dict(self._info)
        dirs: dict[str, int] = {}
        for ps, s in self._walk(dirs):
            st = (s.st_mtime_ns, s.st_size)
            hit = old.get(ps)
            p = hit[3] if hit else Path(ps)
            nid = hit[2] if hit and hit[:2] == st else read_id(p)
            if not nid and hit:  # changed but unreadable for now (iCloud downloading it, a save mid-way): same note
                nid = hit[2]
            if not nid:
                unnamed.append((p, *st))
                continue
            if nid in found:
                if p not in self._warned:
                    self._warned.add(p)
                    print(f"[research_brain] {p} has the same id as {found[nid][2]} ({nid}); using the first. "
                          "Remove the id: line from a copy to make it a note of its own.")
                continue
            found[nid] = (*st, p)
            info[ps] = (*st, nid, p)
        for p, m, sz in unnamed:  # made outside the app: an id from the file name
            base = slugify(p.stem)
            nid, i = base, 2
            while nid in found:
                nid, i = f"{base}-{i}", i + 1
            found[nid] = (m, sz, p)
            info[str(p)] = (m, sz, nid, p)
        with self.lock:
            self._by_id = {nid: v[2] for nid, v in found.items()}
            self._info = info
            self._dirs = dirs
            self._scanned = time.monotonic()
        return {nid: (v[0], v[1]) for nid, v in found.items()}

    def path(self, nid: str) -> Path | None:
        """The file of note `nid`, or None. An id not seen yet (a file just added or renamed outside the
        app) looks at the disk again."""
        with self.lock:
            p = self._by_id.get(nid)
        if p is not None and p.exists():
            return p
        if p is not None or self._dirs_changed():  # moved, or something was added: look again
            self.scan()
            with self.lock:
                return self._by_id.get(nid)
        return None

    def current(self, nid: str) -> Path | None:
        """The file of `nid` as this app last knew it (checked on disk), without looking for new files: for
        saving, which then knows where the file is or that it's a new note."""
        with self.lock:
            p = self._by_id.get(nid)
        if p is None or p.exists():
            return p
        self.scan()  # moved or renamed outside the app since
        with self.lock:
            return self._by_id.get(nid)

    def known(self, nid: str) -> bool:
        """Whether `nid` is taken, from the map alone (no disk access)."""
        with self.lock:
            return nid in self._by_id

    def names(self) -> dict[str, str]:
        """{id: file name without .md}: so a link written as Obsidian does ([[File name]]) finds its note."""
        with self.lock:
            return {nid: p.stem for nid, p in self._by_id.items()}

    def ids(self) -> list[str]:
        with self.lock:
            return sorted(self._by_id)

    def remember(self, nid: str, path: Path) -> None:
        try:
            s = path.stat()
            st = (s.st_mtime_ns, s.st_size)
        except OSError:
            st = (0, 0)
        with self.lock:
            old = self._by_id.get(nid)
            if old is not None and old != path:
                self._info.pop(str(old), None)
            self._by_id[nid] = path
            self._info[str(path)] = (*st, nid, path)
            # our own write: the folders' new mtimes are known, not a sign of an outside change
            touched = [path.parent, path.parent.parent] + ([old.parent] if old is not None else [])
            for d in touched:
                ds = str(d)
                if ds in self._dirs or d == path.parent:
                    try:
                        self._dirs[ds] = os.stat(ds).st_mtime_ns
                    except OSError:
                        pass

    def forget(self, nid: str) -> None:
        with self.lock:
            p = self._by_id.pop(nid, None)
            if p is not None:
                self._info.pop(str(p), None)
                try:
                    if str(p.parent) in self._dirs:
                        self._dirs[str(p.parent)] = os.stat(p.parent).st_mtime_ns
                except OSError:
                    pass

    # ---------------------------------------------------------------- where a note goes
    def paper_folder_name(self, pid: str) -> str:
        """The highlights folder name for paper `pid`: its file's name."""
        p = self.path(pid) if pid else None
        return p.stem if p is not None else safe_name(pid or "Highlights")

    def target(self, note: dict, current: Path | None = None) -> Path:
        """Where `note` should be written. Keeps the subfolder of its type folder it is in (if any);
        a highlight goes in its paper's folder. Never a file that belongs to another note."""
        typ = note.get("type") or "note"
        top = self.type_dir(typ)
        if typ == "fragment" and note.get("parent"):
            folder = top / self.paper_folder_name(note["parent"])
        elif current is not None and (current.parent == top or top in current.parents):
            folder = current.parent
        else:
            folder = top
        name = note_name(note)
        cand, i = folder / f"{name}.md", 2
        while cand.exists() and not (current is not None and _same(cand, current)):
            cand, i = folder / f"{name} {i}.md", i + 1
        return cand

    def move_highlights(self, old: str, new: str) -> None:
        """A paper's file was renamed from `old` to `new`: its highlights folder follows."""
        if not old or old == new:
            return
        top = self.type_dir("fragment")
        src, dst = top / old, top / new
        if not src.is_dir():
            return
        if not dst.exists() or _same(src, dst):
            src.replace(dst)
        else:  # both exist: move the files across, never overwriting
            for p in sorted(src.glob("*.md")):
                t, i = dst / p.name, 2
                while t.exists():
                    t, i = dst / f"{p.stem} {i}.md", i + 1
                p.replace(t)
            try:
                src.rmdir()
            except OSError:
                pass
        self.scan()

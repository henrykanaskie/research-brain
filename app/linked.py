"""Folders of papers, kept where they are.

You can add folders you already keep PDFs in (a OneDrive folder with subfolders, a folder per project...) in
Library settings › Folders of papers. research brain links to each PDF where it is: it never moves, renames,
changes or deletes a file in them, and stores no second copy. Each folder that holds PDFs becomes a topic,
named after the folder by default (Research/Plasma/Sheaths/x.pdf goes in "Sheaths"); before an import you see
every folder with the name it will get, and can change any name, leave a folder out of topics, or have your AI
suggest names from the file names (nothing is downloaded or read for that).

What is stored, in the library's state/linked.json:
    folders   [{id, path, added, names: {"<folder inside it>": "Topic name" | ""}}]   ("" = no topic)
    files     {paper id: {"f": folder id, "rel": "Plasma/Sheaths/x.pdf"}}
    ignored   {folder id: ["<rel>", ...]}   PDFs whose paper you deleted: not added back

Store.pdf_path() answers a linked PDF's real path, so opening, highlighting, citing and the viewer work as for
any PDF. Every new PDF is read once when it's added, for its text and identifiers, so search, Ask and Research have
the whole paper. An online-only one (OneDrive with its space freed up) is downloaded for that, read, and sent straight
back to online only before the next (Windows: unpinned; macOS: the system's evict call, checked), so only its text
(about 2% of the PDF) stays on the computer. If that doesn't take on a Mac, reading pauses once 500 MB of read PDFs
are held and goes on by itself when you free space on the folder. With no room for even one PDF, the paper is added
from its file name and waits; it's read when there's room (the next check), or as soon as you open it or ask about it.

Keeping up. The folders are checked when the app starts, every few minutes while it runs, and on Check now:
a PDF dropped into one is added (to its folder's topic; a new subfolder gets a topic named after it), and a PDF
moved to another folder keeps its paper and moves to that folder's topic. A PDF deleted from the folder leaves
its paper, notes and highlights in the library, without the PDF. Deleting a paper in research brain leaves its
PDF in your folder and remembers not to add it again. Removing a folder from the list keeps its papers, without
their PDFs.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import sys
import threading
import uuid
from pathlib import Path

CONFIG = "linked.json"
SKIP_DIRS = {"Removed from library", "$RECYCLE.BIN", "node_modules", "__MACOSX"}
MAX_FILES = 20000


def _now() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


class Links:
    """The linked folders and files of one library (state/linked.json), cached until the file changes."""

    def __init__(self, root: Path):
        self.path = Path(root) / "state" / CONFIG
        self.lock = threading.RLock()
        self._data: dict | None = None
        self._stamp = None

    # ------------------------------------------------------------ the file
    def data(self) -> dict:
        with self.lock:
            try:
                st = self.path.stat()
                stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                stamp = None
            if self._data is None or stamp != self._stamp:
                d: dict = {}
                if stamp is not None:
                    try:
                        d = json.loads(self.path.read_text(encoding="utf-8"))
                    except (OSError, ValueError):
                        d = {}
                if not isinstance(d, dict):
                    d = {}
                d.setdefault("folders", [])
                d.setdefault("files", {})
                d.setdefault("ignored", {})
                self._data, self._stamp = d, stamp
            return self._data

    def save(self, d: dict) -> None:
        from .paths import write_json_atomic
        with self.lock:
            write_json_atomic(self.path, d)
            self._data = d
            try:
                st = self.path.stat()
                self._stamp = (st.st_mtime_ns, st.st_size)
            except OSError:
                self._stamp = None

    # ------------------------------------------------------------ folders
    def folders(self) -> list[dict]:
        return list(self.data()["folders"])

    def folder(self, fid: str) -> dict | None:
        return next((f for f in self.data()["folders"] if f["id"] == fid), None)

    def folder_for_path(self, path: Path) -> dict | None:
        p = _norm(path)
        return next((f for f in self.data()["folders"] if _norm(f["path"]) == p), None)

    def add_folder(self, path: Path, names: dict[str, str]) -> dict:
        with self.lock:
            d = self.data()
            have = self.folder_for_path(path)
            if have:
                have["names"] = {**have.get("names", {}), **names}
                self.save(d)
                return have
            for f in d["folders"]:  # one folder inside another would add its PDFs twice
                a, b = _norm(f["path"]), _norm(path)
                if b.startswith(a + os.sep) or a.startswith(b + os.sep):
                    raise ValueError(f"That folder overlaps one you already added ({f['path']}). Add the outer "
                                     "folder only: its subfolders come with it.")
            f = {"id": uuid.uuid4().hex[:10], "path": str(Path(path)), "added": _now(), "names": dict(names)}
            d["folders"].append(f)
            self.save(d)
            return f

    def remove_folder(self, fid: str) -> list[str]:
        """Stop following a folder. Returns the papers whose PDF was in it (they stay, without a PDF)."""
        with self.lock:
            d = self.data()
            d["folders"] = [f for f in d["folders"] if f["id"] != fid]
            gone = [nid for nid, x in d["files"].items() if x.get("f") == fid]
            for nid in gone:
                d["files"].pop(nid, None)
            d["ignored"].pop(fid, None)
            self.save(d)
            return gone

    # ------------------------------------------------------------ files
    def path_of(self, nid: str) -> Path | None:
        x = self.data()["files"].get(nid)
        if not x:
            return None
        f = self.folder(x.get("f", ""))
        return Path(f["path"]) / x["rel"] if f else None

    def linked_ids(self) -> dict[str, Path]:
        """{paper id: its PDF's path} for every linked PDF (whether the file is there right now or not)."""
        d = self.data()
        roots = {f["id"]: Path(f["path"]) for f in d["folders"]}
        return {nid: roots[x["f"]] / x["rel"] for nid, x in d["files"].items() if x.get("f") in roots}

    def by_place(self) -> dict[tuple[str, str], str]:
        return {(x["f"], x["rel"]): nid for nid, x in self.data()["files"].items()}

    def link(self, nid: str, fid: str, rel: str, size: int = 0) -> None:
        with self.lock:
            d = self.data()
            d["files"][nid] = {"f": fid, "rel": rel, "size": int(size)}
            self.save(d)

    def files_in(self, fid: str) -> dict[str, dict]:
        """{paper id: {rel, size}} of a folder's linked PDFs."""
        return {nid: x for nid, x in self.data()["files"].items() if x.get("f") == fid}

    def unlink(self, nid: str, ignore: bool = False) -> None:
        """Forget a paper's linked PDF (the file is left as it is). ignore: don't add that file back."""
        with self.lock:
            d = self.data()
            x = d["files"].pop(nid, None)
            if x and ignore:
                lst = d["ignored"].setdefault(x["f"], [])
                if x["rel"] not in lst:
                    lst.append(x["rel"])
            if x:
                self.save(d)

    def ignored(self, fid: str) -> set[str]:
        return set(self.data()["ignored"].get(fid, []))


def _norm(p) -> str:
    try:
        s = str(Path(p).expanduser().resolve())
    except OSError:
        s = str(Path(p).expanduser())
    return s.casefold() if os.name == "nt" or sys.platform == "darwin" else s


# ---------------------------------------------------------------- reading a folder (names and sizes only)
def scan(root: Path) -> list[dict]:
    """Every PDF under `root` (any depth): [{rel, dir, size, mtime}]. Only lists the folders (no file is opened, so
    nothing online-only is downloaded). Hidden files and folders, and Removed from library, are skipped."""
    root = Path(root)
    out: list[dict] = []
    for d, dirs, files in os.walk(root, followlinks=False):
        dirs[:] = sorted(x for x in dirs if not x.startswith(".") and x not in SKIP_DIRS)
        for name in sorted(files):
            if name.startswith(".") or not name.lower().endswith(".pdf"):
                continue
            p = Path(d) / name
            try:
                st = p.stat()
            except OSError:
                continue
            rel = p.relative_to(root).as_posix()
            from .acquire import cloud_only
            out.append({"rel": rel, "dir": rel.rpartition("/")[0], "size": st.st_size, "mtime": st.st_mtime_ns,
                        "cloud": cloud_only(st)})
            if len(out) >= MAX_FILES:
                return out
    return out


def default_name(root: Path, rel_dir: str) -> str:
    """The topic a folder's PDFs go in unless you choose another: the folder's own name (the deepest one), and
    for PDFs right in the folder you added, that folder's name."""
    return (rel_dir.rpartition("/")[2] if rel_dir else Path(root).name).strip()


def plan(root: Path, files: list[dict], names: dict[str, str], existing_topics: list[str]) -> list[dict]:
    """The folders of a scan, each with its PDF count and the topic name it will get (yours if you set one), and
    whether that topic already exists or is shared with another folder of the same name."""
    count: dict[str, int] = {}
    for f in files:
        count[f["dir"]] = count.get(f["dir"], 0) + 1
    have = {t.casefold(): t for t in existing_topics}
    rows = []
    for rel_dir in sorted(count, key=lambda r: (r.count("/"), r.casefold())):
        name = names.get(rel_dir, default_name(root, rel_dir))
        rows.append({"dir": rel_dir, "label": rel_dir or Path(root).name, "pdfs": count[rel_dir], "topic": name,
                     "default": default_name(root, rel_dir), "exists": bool(name) and name.casefold() in have})
    by_name: dict[str, list[str]] = {}
    for r in rows:
        if r["topic"]:
            by_name.setdefault(r["topic"].casefold(), []).append(r["label"])
    for r in rows:
        others = [x for x in by_name.get(r["topic"].casefold(), []) if x != r["label"]] if r["topic"] else []
        r["shared_with"] = others
    return rows


def topic_for(folder: dict, rel: str) -> str:
    rel_dir = rel.rpartition("/")[0]
    names = folder.get("names") or {}
    return names[rel_dir] if rel_dir in names else default_name(Path(folder["path"]), rel_dir)

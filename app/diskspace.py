"""Disk space, and papers folders kept in the cloud.

Running out of space. Adding a PDF (any way: file, drag and drop, URL, browser extension, Zotero, a free copy
found online) first checks that the disk it goes to keeps MIN_FREE after it; if not, the paper is refused
with a message saying how much is left, instead of filling the disk. A write that still fails because the
disk is full (another program filled it meanwhile) answers 507 with the same kind of message (server.py
middleware), and never leaves a half-written file: notes are written to a temporary file and swapped in, so the
old version stays whole. /api/status reports the free space so the app can warn before it gets that far.

PDFs in the cloud. A papers folder inside OneDrive (or iCloud Drive, Dropbox, Google Drive, Box) can keep its
PDFs online only, downloaded when you open one (README: Short on disk space?). "Keep new PDFs online only"
(Library settings › Papers folder) makes new PDFs follow: on Windows each new PDF is marked unpinned, and
OneDrive frees its space as soon as it has uploaded it (the same as Free up space in File Explorer). macOS has
no command for that: it frees online-only files itself when the disk runs low, and Finder's Free up space
does it at once. Online-only files are never read in the background (app/acquire.py cloud_only).
"""
from __future__ import annotations

import errno
import os
import shutil
import subprocess
import sys
from pathlib import Path

MB = 1 << 20
MIN_FREE = int(os.environ.get("RB_MIN_FREE_MB", "300")) * MB   # what adding a PDF always leaves free
LOW = int(os.environ.get("RB_LOW_SPACE_MB", "1024")) * MB      # below this, the app warns


class DiskFull(OSError):
    """Not enough room for what was asked. str() is a message for the person, not a traceback."""

    def __init__(self, message: str):
        super().__init__(errno.ENOSPC, message)
        self.message = message

    def __str__(self) -> str:
        return self.message


def _existing(path: Path) -> Path:
    p = Path(path)
    while not p.exists() and p != p.parent:
        p = p.parent
    return p


def free_bytes(path: Path) -> int | None:
    """Free space on the disk holding `path` (or its nearest existing parent); None if it can't be told."""
    try:
        return shutil.disk_usage(_existing(Path(path))).free
    except OSError:
        return None


def human(n: int | None) -> str:
    if n is None:
        return "an unknown amount"
    if n >= 1 << 30:
        return f"{n / (1 << 30):.1f} GB"
    return f"{max(0, n) // MB} MB"


def ensure_room(path: Path, need: int, what: str = "this PDF") -> None:
    """Raise DiskFull unless the disk holding `path` keeps MIN_FREE after `need` more bytes."""
    free = free_bytes(path)
    if free is None or free - max(0, need) >= MIN_FREE:
        return
    raise DiskFull(f"Not enough space on this computer for {what}: {human(free)} free, and research brain always "
                   f"leaves {human(MIN_FREE)}. Free up some space (or keep your PDFs in OneDrive: README, "
                   "Short on disk space?), then try again.")


def is_disk_full(exc: BaseException) -> bool:
    """A 'disk is full' failure from the OS or from SQLite (the search index), wherever it was raised."""
    seen = set()
    while exc is not None and id(exc) not in seen:
        seen.add(id(exc))
        if isinstance(exc, OSError) and exc.errno in (errno.ENOSPC, getattr(errno, "EDQUOT", -1)):
            return True
        if getattr(exc, "winerror", None) in (39, 112):  # ERROR_HANDLE_DISK_FULL, ERROR_DISK_FULL
            return True
        if "database or disk is full" in str(exc):
            return True
        exc = exc.__cause__ or exc.__context__
    return False


FULL_MESSAGE = ("This computer's disk is full, so research brain couldn't save that. Nothing was lost: what "
                "was already saved is unchanged. Free up some space (or keep your PDFs in OneDrive: README, "
                "Short on disk space?), then try again.")


def status(*folders: Path) -> dict:
    """{"free": bytes, "low": bool, "min_free": bytes} for the fullest of these folders' disks."""
    frees = [f for f in (free_bytes(p) for p in folders) if f is not None]
    free = min(frees) if frees else None
    return {"free": free, "low": free is not None and free < LOW, "min_free": MIN_FREE,
            "free_text": human(free)}


# ---------------------------------------------------------------- cloud folders
def cloud_provider(path: Path) -> str:
    """The sync service a folder is in ("OneDrive", "iCloud Drive", ...), or "" for an ordinary folder."""
    try:
        p = Path(path).expanduser().resolve()
    except OSError:
        p = Path(path).expanduser()
    s = str(p)
    if os.name == "nt":
        for var in ("OneDriveCommercial", "OneDriveConsumer", "OneDrive"):
            root = os.environ.get(var)
            if root:
                try:
                    if p == Path(root).resolve() or Path(root).resolve() in p.parents:
                        return "OneDrive"
                except OSError:
                    pass
    home = Path.home()
    cs = home / "Library" / "CloudStorage"
    if cs in p.parents:
        name = p.relative_to(cs).parts[0].lower()
        for key, label in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google", "Google Drive"),
                           ("box", "Box"), ("icloud", "iCloud Drive")):
            if name.startswith(key):
                return label
        return "a cloud folder"
    if (home / "Library" / "Mobile Documents") in p.parents:
        return "iCloud Drive"
    low = s.lower()
    for key, label in (("onedrive", "OneDrive"), ("dropbox", "Dropbox"), ("google drive", "Google Drive")):
        if f"{os.sep}{key}" in low:
            return label
    return ""


def give_back(path: Path, confirm: float = 4.0) -> bool:
    """Send a PDF research brain just read back to online only, so it doesn't stay on this computer. Windows:
    unpinned (OneDrive frees it right after). macOS: the system's evict call (the Finder's Remove Download), then
    a check, for up to `confirm` seconds, that it really is online only again. False if it isn't (or can't be)."""
    if os.name == "nt":
        return keep_online_only(path)
    if sys.platform != "darwin":
        return False
    try:
        from Foundation import NSURL, NSFileManager
        ok, _err = NSFileManager.defaultManager().evictUbiquitousItemAtURL_error_(NSURL.fileURLWithPath_(str(path)), None)
    except Exception:
        return False
    if not ok:
        return False
    from .acquire import cloud_only
    import time
    end = time.monotonic() + confirm
    while True:
        try:
            if cloud_only(os.stat(path)):
                return True
        except OSError:
            return False
        if time.monotonic() >= end:
            return False
        time.sleep(0.25)


def can_free_new_pdfs() -> bool:
    """Whether research brain can ask the sync app to free a file's space itself (Windows only)."""
    return os.name == "nt"


def keep_online_only(path: Path, recursive: bool = False) -> bool:
    """Ask the sync app to free `path`'s space once it's uploaded (Windows: unpinned, not pinned; what Free up
    space does). True if asked. Never raises; a no-op where the system has no such command."""
    if os.name != "nt":
        return False
    args = ["attrib", "-P", "+U", str(path)]
    if recursive:
        args += ["/S", "/D"]
    try:
        r = subprocess.run(args, capture_output=True, timeout=60,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        return r.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def put_file(src: Path, dest: Path, *, online_only: bool = False, copy: bool = False) -> None:
    """Move (or copy) a PDF into the papers folder: room checked first, no partial file left if it fails,
    then (online_only) handed to the sync app to free once uploaded."""
    src, dest = Path(src), Path(dest)
    size = src.stat().st_size
    same_disk = False
    try:
        same_disk = os.stat(_existing(dest.parent)).st_dev == src.stat().st_dev
    except OSError:
        pass
    if copy or not same_disk:  # a rename on one disk needs no room; a copy does
        ensure_room(dest.parent, size)
    dest.parent.mkdir(parents=True, exist_ok=True)
    part = dest.with_name(f".{dest.name}.part")
    try:
        if copy:
            shutil.copy2(src, part)
            os.replace(part, dest)
        elif same_disk:
            shutil.move(str(src), dest)
        else:
            shutil.copy2(src, part)
            os.replace(part, dest)
            try:
                src.unlink()
            except OSError:
                pass
    except BaseException:
        try:
            part.unlink()
        except OSError:
            pass
        raise
    if online_only:
        keep_online_only(dest)


if __name__ == "__main__" and len(sys.argv) > 1:  # python -m app.diskspace <folder>: what the app sees
    print(status(Path(sys.argv[1])), cloud_provider(Path(sys.argv[1])) or "not a cloud folder")

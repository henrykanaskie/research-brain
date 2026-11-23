"""File replacement that also works on Windows.

On Windows, replacing a file fails with a sharing violation while another program has it open: an
antivirus scanner, a sync client (OneDrive, Dropbox, iCloud), a search indexer or Obsidian. Those holds
last milliseconds, so a save retries for up to about two seconds before giving up. On macOS and Linux
os.replace never waits.
"""
from __future__ import annotations

import os
import sys
import time

WINDOWS = sys.platform == "win32"


def replace(src, dst, attempts: int = 20, wait: float = 0.1) -> None:
    for i in range(attempts):
        try:
            os.replace(long(src), long(dst))
            return
        except PermissionError:
            if not WINDOWS or i == attempts - 1:
                raise
            time.sleep(wait)


def read_text(path, attempts: int = 20, wait: float = 0.1, encoding: str = "utf-8") -> str:
    """Read a file another thread or program may be replacing this instant. On Windows, opening a file while
    it is swapped in (os.replace) or held by a scanner or sync client fails with a sharing violation for a
    few milliseconds: retry for up to about two seconds, like replace. A missing file raises at once."""
    from pathlib import Path
    for i in range(attempts):
        try:
            return long(path).read_text(encoding=encoding)
        except PermissionError:
            if not WINDOWS or i == attempts - 1:
                raise
            time.sleep(wait)
    raise AssertionError("unreachable")


def long(path, always: bool = False):
    """The path as Windows opens it however long it is. Past about 260 characters a path can be opened only with the
    "\\\\?\\" prefix, unless the computer has long paths switched on (Windows 11 has them off unless someone changed it).
    always: prefix it even when short (a folder to walk, whose files may be deeper). Elsewhere: the path as it is."""
    from pathlib import Path
    s = str(path)
    if not WINDOWS or s.startswith("\\\\?\\") or (len(s) < 240 and not always):
        return Path(path)
    s = os.path.abspath(s)
    return Path("\\\\?\\UNC\\" + s[2:] if s.startswith("\\\\") else "\\\\?\\" + s)


def plain(path) -> str:
    """A path as people read it (without long()'s prefix)."""
    s = str(path)
    if s.startswith("\\\\?\\UNC\\"):
        return "\\\\" + s[8:]
    return s[4:] if s.startswith("\\\\?\\") else s


def remove_tree(path, attempts: int = 30, wait: float = 0.1) -> None:
    """Delete a folder, ignoring errors. On Windows a process that was just killed can keep its working
    folder open for a moment (taskkill returns before the process is gone), so retry for ~3 seconds."""
    import shutil
    from pathlib import Path
    p = Path(path)
    for i in range(attempts if WINDOWS else 1):
        shutil.rmtree(p, ignore_errors=True)
        if not p.exists():
            return
        time.sleep(wait)

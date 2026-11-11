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
            os.replace(src, dst)
            return
        except PermissionError:
            if not WINDOWS or i == attempts - 1:
                raise
            time.sleep(wait)


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

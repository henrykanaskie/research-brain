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

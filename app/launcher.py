"""Open research brain like an app, only when you ask for it.

`research-brain open` (what the dock / Start menu / app-menu entry runs):
- if research brain is already running, it just opens a window on it;
- otherwise it starts the server in the background, waits for it, and opens a window;
- the window is an app window (no tabs or address bar) in Chrome, Edge, Brave or Chromium when one is
  installed, else a tab in your default browser (RB_APP_WINDOW=0 always uses the default browser);
- a server started this way stops by itself once no research brain window has been open for a few
  minutes (RB_IDLE_EXIT seconds, default 180), unless an agent search or another background job is
  still running. Nothing starts when the computer starts.

`research-brain install-app` adds the entry: on macOS an app in ~/Applications ("research brain.app",
made on this computer, so Gatekeeper has nothing to quarantine and no developer account is needed); on
Windows a Start-menu shortcut; on Linux an app-menu entry. `--remove` takes it away again.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import paths

APP_NAME = "research brain"
IDLE_DEFAULT = 180


def _status_ok(url: str, timeout: float = 1.0) -> bool:
    try:
        with urllib.request.urlopen(url + "/api/status", timeout=timeout) as r:
            return r.status == 200
    except OSError:
        return False


# ---------------------------------------------------------------- the window
def _chromium() -> str | None:
    """A Chromium-family browser that can open an app window (--app=URL)."""
    if sys.platform == "darwin":
        for app in ("Google Chrome", "Microsoft Edge", "Brave Browser", "Chromium", "Arc"):
            for root in ("/Applications", str(Path.home() / "Applications")):
                exe = Path(root) / f"{app}.app" / "Contents" / "MacOS" / app
                if exe.exists():
                    return str(exe)
        return None
    if os.name == "nt":
        bases = [os.environ.get(k, "") for k in ("PROGRAMFILES", "PROGRAMFILES(X86)", "LOCALAPPDATA")]
        rels = [r"Google\Chrome\Application\chrome.exe", r"Microsoft\Edge\Application\msedge.exe",
                r"BraveSoftware\Brave-Browser\Application\brave.exe", r"Chromium\Application\chrome.exe"]
        for b in bases:
            for r in rels:
                p = Path(b) / r if b else None
                if p and p.exists():
                    return str(p)
        return None
    for name in ("google-chrome", "google-chrome-stable", "chromium", "chromium-browser", "microsoft-edge",
                 "microsoft-edge-stable", "brave-browser"):
        p = shutil.which(name)
        if p:
            return p
    return None


def open_window(url: str) -> str:
    """Open url as an app window if possible. Returns "app" or "browser"."""
    exe = None if os.environ.get("RB_APP_WINDOW", "1") == "0" else _chromium()
    if exe:
        try:
            subprocess.Popen([exe, f"--app={url}"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                             **_detached())
            return "app"
        except OSError:
            pass
    webbrowser.open(url)
    return "browser"


def _detached() -> dict:
    if os.name == "nt":
        flags = 0x00000008 | 0x00000200 | 0x08000000  # DETACHED_PROCESS | NEW_PROCESS_GROUP | NO_WINDOW
        return {"creationflags": flags, "close_fds": True}
    return {"start_new_session": True}


# ---------------------------------------------------------------- open
def cmd_open(a) -> int:
    port = a.port
    url = f"http://127.0.0.1:{port}"
    if _status_ok(url):
        open_window(url)
        return 0
    log = paths.user_data_dir() / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", f"{__package__ or 'app'}.cli", "serve", "--no-open", "--port", str(port), "--quit-when-closed"]
    if a.profile:
        args += ["--profile", a.profile]
    with open(log, "ab") as out:
        subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         cwd=str(paths.PKG_DIR.parent), **_detached())
    for _ in range(240):  # up to a minute: the first start builds the search index
        if _status_ok(url, 0.5):
            open_window(url)
            return 0
        time.sleep(0.25)
    print(f"research brain didn't start. What it said is in {log}", file=sys.stderr)
    return 1


# ---------------------------------------------------------------- the dock / Start menu / app menu entry
def _command() -> str:
    """The installed research-brain command (absolute), so the entry works without PATH."""
    found = shutil.which("research-brain")
    if found:
        return str(Path(found).resolve())
    here = Path(sys.argv[0]).resolve()
    return str(here)


def _mac_app(remove: bool) -> str:
    app = Path.home() / "Applications" / f"{APP_NAME}.app"
    if remove:
        shutil.rmtree(app, ignore_errors=True)
        return f"Removed {app}"
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    exe = macos / "research-brain"
    exe.write_text(f'#!/bin/sh\nexec "{_command()}" open\n', encoding="utf-8", newline="\n")
    exe.chmod(0o755)
    icon = paths.app_root() / "static" / "icons" / "icon-512.png"
    has_icon = False
    if icon.exists() and shutil.which("sips") and shutil.which("iconutil"):
        iconset = res / "rb.iconset"
        iconset.mkdir(exist_ok=True)
        for s in (16, 32, 128, 256, 512):
            subprocess.run(["sips", "-z", str(s), str(s), str(icon), "--out", str(iconset / f"icon_{s}x{s}.png")], capture_output=True)
        has_icon = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(res / "rb.icns")], capture_output=True).returncode == 0
        shutil.rmtree(iconset, ignore_errors=True)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>local.research-brain.launcher</string>
  <key>CFBundleExecutable</key><string>research-brain</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1</string>
  {'<key>CFBundleIconFile</key><string>rb</string>' if has_icon else ''}
  <key>LSUIElement</key><true/>
</dict></plist>
"""
    (app / "Contents" / "Info.plist").write_text(plist, encoding="utf-8", newline="\n")
    return f"Added {app}: open it from Launchpad, Spotlight or the Dock."


def _windows_shortcut(remove: bool) -> str:
    programs = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    lnk = programs / f"{APP_NAME}.lnk"
    if remove:
        lnk.unlink(missing_ok=True)
        return f"Removed {lnk}"
    programs.mkdir(parents=True, exist_ok=True)
    icon = paths.app_root() / "static" / "icons" / "icon.ico"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut('{lnk}');"
          f"$s.TargetPath='{_command()}';$s.Arguments='open';$s.WindowStyle=7;"
          f"$s.Description='research brain: your papers and notes';"
          + (f"$s.IconLocation='{icon}';" if icon.exists() else "") + "$s.Save()")
    r = subprocess.run(["powershell", "-NoProfile", "-ExecutionPolicy", "Bypass", "-Command", ps], capture_output=True, text=True)
    if r.returncode != 0:
        raise OSError(r.stderr.strip() or "PowerShell couldn't make the shortcut")
    return f"Added “{APP_NAME}” to the Start menu."


def _linux_desktop(remove: bool) -> str:
    apps = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local" / "share"))) / "applications"
    f = apps / "research-brain.desktop"
    if remove:
        f.unlink(missing_ok=True)
        return f"Removed {f}"
    apps.mkdir(parents=True, exist_ok=True)
    icon = paths.app_root() / "static" / "icons" / "icon-512.png"
    f.write_text("[Desktop Entry]\nType=Application\n"
                 f"Name={APP_NAME}\nComment=Your papers and notes\nExec=\"{_command()}\" open\n"
                 + (f"Icon={icon}\n" if icon.exists() else "") + "Terminal=false\nCategories=Education;Science;Office;\n",
                 encoding="utf-8", newline="\n")
    return f"Added “{APP_NAME}” to your app menu."


def cmd_install_app(a) -> int:
    try:
        if sys.platform == "darwin":
            msg = _mac_app(a.remove)
        elif os.name == "nt":
            msg = _windows_shortcut(a.remove)
        else:
            msg = _linux_desktop(a.remove)
    except OSError as e:
        print(f"Couldn't {'remove' if a.remove else 'add'} the app entry: {e}", file=sys.stderr)
        return 1
    print(msg)
    return 0

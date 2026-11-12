"""Open research brain like an app, only when you ask for it.

`research-brain open` (what the dock / Start menu / app-menu entry runs):
- if research brain is already running, it just opens a window on it;
- otherwise it starts the server in the background, waits for it, and opens a window;
- the window is an app window (no tabs or address bar) in Chrome, Edge, Brave or Chromium when one is
  installed, else a tab in your default browser (RB_APP_WINDOW=0 always uses the default browser);
- a server started this way stops by itself once no research brain window has been open for a few
  minutes (RB_IDLE_EXIT seconds, default 180), unless an agent search or another background job is
  still running. Nothing starts when the computer starts.

`research-brain install-app` adds the entry: on macOS an app in /Applications, or ~/Applications when
/Applications isn't writable ("research brain.app",
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
def _port_busy(port: int) -> bool:
    import socket
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.3)
        return sock.connect_ex(("127.0.0.1", port)) == 0


def _start_server(a, url: str) -> bool:
    """Start the server in the background unless it's running; True once it answers (up to two minutes:
    the first start builds the search index)."""
    if _status_ok(url):
        return True
    # one that is stopping (just quit) still holds the port and answers 503: let it go first
    for _ in range(150):
        if not _port_busy(a.port):
            break
        time.sleep(0.1)
    log = paths.user_data_dir() / "app.log"
    log.parent.mkdir(parents=True, exist_ok=True)
    args = [sys.executable, "-m", f"{__package__ or 'app'}.cli", "serve", "--no-open", "--port", str(a.port), "--quit-when-closed"]
    if a.profile:
        args += ["--profile", a.profile]
    with open(log, "ab") as out:
        subprocess.Popen(args, stdout=out, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
                         cwd=str(paths.PKG_DIR.parent), **_detached())
    for _ in range(480):
        if _status_ok(url, 0.5):
            return True
        time.sleep(0.25)
    return False


def cmd_open(a) -> int:
    url = f"http://127.0.0.1:{a.port}"
    from . import macwindow
    if macwindow.available():  # a Mac: research brain's own window, no title bar (app/macwindow.py)
        # start the server now, while the window is made (about half a second each), not after it
        import threading
        box: dict = {}
        th = threading.Thread(target=lambda: box.update(ok=_start_server(a, url)), daemon=True)
        th.start()

        def started() -> bool:
            th.join()
            if box.pop("ok", False):
                return True
            return _start_server(a, url)  # a later restart (the window lost its server): start it again
        return macwindow.run(url, started)
    if _start_server(a, url):
        open_window(url + "/?app=1")  # ?app=1: the window closes itself when research brain stops
        return 0
    log = paths.user_data_dir() / "app.log"
    print(f"research brain didn't start. What it said is in {log}", file=sys.stderr)
    _tell("research brain didn't start", f"What it said is at the end of {log}")
    return 1


def _tell(title: str, msg: str) -> None:
    """Show an error when there's no terminal to print it in: opened from the Start menu or the app menu,
    a failed start would otherwise do nothing at all. The Mac app shows its own alert. RB_NO_ALERT=1 (tests)
    switches it off."""
    if sys.platform == "darwin" or os.environ.get("RB_NO_ALERT") == "1" or (sys.stderr and sys.stderr.isatty()):
        return
    if os.name == "nt":
        try:
            import ctypes
            ctypes.windll.user32.MessageBoxW(None, msg, title, 0x10)  # MB_ICONERROR
        except (AttributeError, OSError):
            pass
        return
    for cmd in (["zenity", "--error", f"--title={title}", f"--text={msg}"], ["kdialog", "--title", title, "--error", msg],
                ["notify-send", "--icon=dialog-error", title, msg]):
        if shutil.which(cmd[0]):
            subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, **_detached())
            return


# ---------------------------------------------------------------- the dock / Start menu / app menu entry
def _command() -> str:
    """The installed research-brain command (absolute), so the entry works without PATH."""
    found = shutil.which("research-brain")
    if found:
        return str(Path(found).resolve())
    here = Path(sys.argv[0]).resolve()
    return str(here)


LSREGISTER = "/System/Library/Frameworks/CoreServices.framework/Frameworks/LaunchServices.framework/Support/lsregister"


def _mac_dirs() -> list[Path]:
    """Where the app can go, best first: /Applications (what Finder's sidebar shows) when you can write
    there, else the Applications folder in your home folder. RB_MAC_APP_DIR picks one (tests use it)."""
    env = os.environ.get("RB_MAC_APP_DIR", "").strip()
    if env:
        return [Path(env).expanduser()]
    system = Path("/Applications")
    user = Path.home() / "Applications"
    return [system, user] if system.is_dir() and os.access(system, os.W_OK) else [user]


def _mac_script(cmd: str) -> str:
    """The app's executable. It runs `research-brain open`, writes what happens to app.log, and shows a
    dialog when something goes wrong, so a click never just does nothing."""
    log = paths.user_data_dir() / "app.log"
    alert = ("/usr/bin/osascript -e 'on run argv' -e 'display alert (item 1 of argv) message (item 2 of argv)' "
             "-e 'end run' \"$1\" \"$2\" >/dev/null 2>&1")
    return f"""#!/bin/sh
RB="{cmd}"
LOG="{log}"
alert() {{ [ -n "$RB_NO_ALERT" ] || {alert}; }}  # RB_NO_ALERT: tests
mkdir -p "$(dirname "$LOG")"
if [ ! -x "$RB" ]; then
  alert "research brain isn't installed where this app expects it" "It looked for $RB. Run ./install.sh again (it remakes this app), or run: research-brain install-app"
  exit 1
fi
echo "--- $(date): opened from the app" >>"$LOG"
# start it in the background and return at once: an app that sits waiting is "not responding" to macOS
/usr/bin/osascript -e 'display notification "Opening… the first start can take a minute." with title "research brain"' >/dev/null 2>&1 &
( "$RB" open >>"$LOG" 2>&1 || alert "research brain didn't start" "What it said is at the end of $LOG" ) </dev/null >/dev/null 2>&1 &
exit 0
"""


def _as_str(v) -> str:
    """An AppleScript string literal."""
    return '"' + str(v).replace("\\", "\\\\").replace('"', '\\"') + '"'


def _mac_applet_source(cmd: str, port: int) -> str:
    """A stay-open AppleScript app: a real Mac app, so it has a Dock icon (with the running dot) and answers
    the Dock, unlike a shell script. It starts `research-brain open` in the background and returns at once;
    clicking the Dock icon again opens another window; it quits by itself once research brain has stopped
    (its windows closed a few minutes ago), and Quit from the Dock stops research brain too."""
    data = paths.user_data_dir()
    return f"""property rbCmd : {_as_str(cmd)}
property logDir : {_as_str(data)}
property logPath : {_as_str(data / "app.log")}
property failFlag : {_as_str(data / "launch-failed")}
property base : {_as_str(f"http://127.0.0.1:{port}")}
property startedAt : missing value
property wasUp : false
property selfQuit : false

on run
	set startedAt to current date
	set wasUp to false
	set selfQuit to false
	try
		do shell script "test -x " & quoted form of rbCmd
	on error
		display alert "research brain isn't installed where this app expects it" message "It looked for " & rbCmd & ". Run ./install.sh again (it remakes this app), or run: research-brain install-app"
		set selfQuit to true
		quit
		return
	end try
	launchRB()
end run

on reopen
	if isUp() then launchRB()
end reopen

on launchRB()
	set L to quoted form of logPath
\tdo shell script "mkdir -p " & quoted form of logDir & "; rm -f " & quoted form of failFlag & "; date '+--- %c: opened from the app' >> " & L & "; (" & quoted form of rbCmd & " open >> " & L & " 2>&1 || touch " & quoted form of failFlag & ") </dev/null >/dev/null 2>&1 &"
end launchRB

on isUp()
	try
		do shell script "curl -sf -m 2 " & base & "/api/status >/dev/null"
		return true
	on error
		return false
	end try
end isUp

on idle
	try
		do shell script "test -e " & quoted form of failFlag
		do shell script "rm -f " & quoted form of failFlag
		display alert "research brain didn't start" message "What it said is at the end of " & logPath
		set selfQuit to true
		quit
		return 5
	end try
	if isUp() then
		set wasUp to true
	else if wasUp or ((current date) - startedAt) > 150 then
		set selfQuit to true
		quit
	end if
	return 5
end idle

on quit
	if not selfQuit then
		try
			do shell script "curl -sf -m 3 -X POST -H 'Content-Type: application/json' -d '{{}}' " & base & "/api/quit >/dev/null"
		end try
	end if
	continue quit
end quit
"""


def _mac_app(remove: bool) -> str:
    if remove:
        gone = []
        # both usual places, so an older copy goes too; RB_MAC_APP_DIR (tests) limits it to that folder
        dirs = _mac_dirs() if os.environ.get("RB_MAC_APP_DIR", "").strip() else \
            [Path("/Applications"), Path.home() / "Applications"]
        for d in dirs:
            app = d / f"{APP_NAME}.app"
            if app.exists():
                shutil.rmtree(app, ignore_errors=True)
                gone.append(str(app))
        return "Removed " + ", ".join(gone) if gone else "There was no research brain app to remove."
    last_err: OSError | None = None
    for d in _mac_dirs():
        try:
            return _mac_bundle(d / f"{APP_NAME}.app")
        except OSError as e:
            last_err = e
    raise last_err or OSError("no Applications folder to write to")


def _mac_icon(res: Path) -> Path | None:
    """rb.icns from static/icons/icon-mac.png: the brain as a Mac icon (a rounded square with Apple's margin
    and shadow, not the full-bleed square the web app uses), every size including Retina (@2x)."""
    icon = paths.app_root() / "static" / "icons" / "icon-mac.png"
    if not (icon.exists() and shutil.which("sips") and shutil.which("iconutil")):
        return None
    iconset = res / "rb.iconset"
    iconset.mkdir(parents=True, exist_ok=True)
    for s in (16, 32, 128, 256, 512):
        for name, px in ((f"icon_{s}x{s}.png", s), (f"icon_{s}x{s}@2x.png", s * 2)):
            subprocess.run(["sips", "-z", str(px), str(px), str(icon), "--out", str(iconset / name)], capture_output=True)
    out = res / "rb.icns"
    ok = subprocess.run(["iconutil", "-c", "icns", str(iconset), "-o", str(out)], capture_output=True).returncode == 0
    shutil.rmtree(iconset, ignore_errors=True)
    return out if ok else None


def _mac_register(app: Path) -> None:
    """Sign it for this computer only (no developer account), and tell Launch Services and Spotlight now."""
    for cmd in (["codesign", "--force", "--deep", "--sign", "-", str(app)], [LSREGISTER, "-f", str(app)], ["mdimport", str(app)]):
        if shutil.which(cmd[0]) or Path(cmd[0]).exists():
            subprocess.run(cmd, capture_output=True)


def _mac_applet(app: Path) -> str:
    """The Dock-icon app, compiled with osacompile (part of macOS)."""
    port = int(os.environ.get("RB_PORT", "8765"))
    src = app.parent / f".{APP_NAME}.applescript"
    src.write_text(_mac_applet_source(_command(), port), encoding="utf-8")
    tmp = app.parent / f".{APP_NAME}.building.app"
    shutil.rmtree(tmp, ignore_errors=True)
    try:
        r = subprocess.run(["osacompile", "-s", "-o", str(tmp), str(src)], capture_output=True, text=True)
        if r.returncode != 0:
            raise OSError(r.stderr.strip() or "osacompile failed")
    finally:
        src.unlink(missing_ok=True)
    res = tmp / "Contents" / "Resources"
    icns = _mac_icon(res)
    plist = tmp / "Contents" / "Info.plist"
    if icns:
        shutil.copyfile(icns, res / "applet.icns")
        icns.unlink(missing_ok=True)
        # osacompile also adds the script icon as an asset catalog (CFBundleIconName), which macOS
        # prefers over applet.icns: without this the Dock and Launchpad showed the script icon
        (res / "Assets.car").unlink(missing_ok=True)
        subprocess.run(["plutil", "-remove", "CFBundleIconName", str(plist)], capture_output=True)
    for key, val in (("CFBundleIdentifier", "local.research-brain.launcher"), ("CFBundleName", APP_NAME),
                     ("CFBundleDisplayName", APP_NAME)):
        subprocess.run(["plutil", "-replace", key, "-string", val, str(plist)], capture_output=True)
    shutil.rmtree(app, ignore_errors=True)
    tmp.rename(app)
    _mac_register(app)
    return f"Added {app}: open it from Applications, Launchpad or Spotlight, and keep it in the Dock (right-click its icon > Options > Keep in Dock)."


def _mac_window_script(cmd: str) -> str:
    """The native-window app's executable: it becomes `research-brain open` (exec), so the app in the Dock is
    the window itself (app/macwindow.py): it answers macOS at once, and Quit closes it."""
    log = paths.user_data_dir() / "app.log"
    return f"""#!/bin/sh
RB="{cmd}"
LOG="{log}"
mkdir -p "$(dirname "$LOG")"
if [ ! -x "$RB" ]; then
  [ -n "$RB_NO_ALERT" ] || /usr/bin/osascript -e 'display alert "research brain is not installed where this app expects it" message "Run ./install.sh again (it remakes this app), or run: research-brain install-app"' >/dev/null 2>&1
  exit 1
fi
echo "--- $(date): opened from the app" >>"$LOG"
exec "$RB" open >>"$LOG" 2>&1 </dev/null
"""


def _mac_window_app(app: Path) -> str:
    """research brain's own window (app/macwindow.py): no Chrome window, no title bar, one Dock icon."""
    tmp = app.parent / f".{APP_NAME}.building.app"
    shutil.rmtree(tmp, ignore_errors=True)
    macos, res = tmp / "Contents" / "MacOS", tmp / "Contents" / "Resources"
    macos.mkdir(parents=True)
    res.mkdir(parents=True)
    exe = macos / "research-brain"
    exe.write_text(_mac_window_script(_command()), encoding="utf-8", newline="\n")
    exe.chmod(0o755)
    icns = _mac_icon(res)
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>local.research-brain.launcher</string>
  <key>CFBundleExecutable</key><string>research-brain</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>2</string>
  <key>CFBundleVersion</key><string>2</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>NSHighResolutionCapable</key><true/>
  <key>LSApplicationCategoryType</key><string>public.app-category.education</string>
  {'<key>CFBundleIconFile</key><string>rb</string>' if icns else ''}
</dict></plist>
"""
    (tmp / "Contents" / "Info.plist").write_text(plist, encoding="utf-8", newline="\n")
    shutil.rmtree(app, ignore_errors=True)
    tmp.rename(app)
    _mac_register(app)
    return f"Added {app}: open it from Applications, Launchpad or Spotlight, and keep it in the Dock (right-click its icon > Options > Keep in Dock)."


def _mac_bundle(app: Path) -> str:
    from . import macwindow
    if macwindow.available() and os.environ.get("RB_MAC_SCRIPT_APP") != "1":
        return _mac_window_app(app)
    if shutil.which("osacompile") and os.environ.get("RB_MAC_SCRIPT_APP") != "1":
        return _mac_applet(app)
    # without osacompile (never on a real Mac): a background-only shell-script app
    macos, res = app / "Contents" / "MacOS", app / "Contents" / "Resources"
    macos.mkdir(parents=True, exist_ok=True)
    res.mkdir(parents=True, exist_ok=True)
    exe = macos / "research-brain"
    exe.write_text(_mac_script(_command()), encoding="utf-8", newline="\n")
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
    # LSUIElement: no Dock icon. The script returns at once (a notification shows it worked), and a script
    # can't answer the Dock's Apple events, so a Dock icon would only ever read "not responding".
    plist = f"""<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>{APP_NAME}</string>
  <key>CFBundleDisplayName</key><string>{APP_NAME}</string>
  <key>CFBundleIdentifier</key><string>local.research-brain.launcher</string>
  <key>CFBundleExecutable</key><string>research-brain</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1</string>
  <key>CFBundleVersion</key><string>1</string>
  <key>LSMinimumSystemVersion</key><string>11.0</string>
  <key>LSUIElement</key><true/>
  {'<key>CFBundleIconFile</key><string>rb</string>' if has_icon else ''}
</dict></plist>
"""
    (app / "Contents" / "Info.plist").write_text(plist, encoding="utf-8", newline="\n")
    _mac_register(app)
    return f"Added {app}: open it from Applications, Launchpad or Spotlight (it can take a minute to show in Spotlight)."


def _windows_shortcut(remove: bool) -> str:
    programs = Path(os.environ.get("APPDATA", str(Path.home()))) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    lnk = programs / f"{APP_NAME}.lnk"
    if remove:
        lnk.unlink(missing_ok=True)
        return f"Removed {lnk}"
    programs.mkdir(parents=True, exist_ok=True)
    icon = paths.app_root() / "static" / "icons" / "icon.ico"
    # pythonw (next to this Python) runs it with no console window at all; the research-brain command
    # is a console program, so it would sit minimized in the taskbar while research brain starts
    pyw = Path(sys.executable).with_name("pythonw.exe")
    target, args = (str(pyw), f"-m {__package__ or 'app'}.cli open") if pyw.exists() else (_command(), "open")
    q = lambda v: "'" + str(v).replace("'", "''") + "'"  # a PowerShell string, whatever the path holds
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(lnk)});"
          f"$s.TargetPath={q(target)};$s.Arguments={q(args)};$s.WindowStyle=7;"
          f"$s.Description='research brain: your papers and notes';"
          + (f"$s.IconLocation={q(icon)};" if icon.exists() else "") + "$s.Save()")
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

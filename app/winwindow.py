"""research brain in its own Windows window: no Chrome or Edge window, no browser tab.

`research-brain open` on Windows (what the Start menu entry runs) shows research brain in a window of its own
(pywebview on WebView2, the Edge engine built into Windows 10 and 11) instead of a Chrome/Edge app window:
- a normal Windows window with its own title bar, taskbar button and the brain icon;
- the window shows "research brain is starting" at once, while the server starts;
- closing it stops research brain (if this window started it); if research brain stops some other way, it
  is started again, and the window closes only if it won't start;
- an update (app/updates.py) closes it so the install can replace the files it runs from, and the update
  script opens it again when the new version is up (`window.json` tells the update which process to wait for);
- links that open a new tab or window open in your default browser, downloads ask where to save, the right
  click menu (copy, paste) works, and voice mode may use the microphone on research brain's own page.

The page talks to it over WebView2's own channel (static/live.js: chrome.webview.postMessage({rb: ...})).
Without pywebview, without WebView2, with RB_APP_WINDOW=browser (or 0), or if the window doesn't come up,
`open` uses the Chrome/Edge window as before (app/launcher.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import threading
import webbrowser

from . import __version__, paths
from .macwindow import APP_NAME, LOADING, _quit_server, _status

WEBVIEW2_KEY = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # the Evergreen WebView2 Runtime's EdgeUpdate client
MUTEX = "Local\\research-brain-window"


def _webview2_installed() -> bool:
    """The WebView2 runtime is on this computer (Windows 11 always; Windows 10 with any recent Edge)."""
    try:
        import winreg
    except ImportError:
        return False
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}")]
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
            if str(pv) and str(pv) != "0.0.0.0":
                return True
        except OSError:
            continue
    return False


def available() -> bool:
    """A window of its own can be used here: Windows, pywebview and WebView2 installed, and not switched off.
    (Without WebView2 pywebview would fall back to Internet Explorer's engine, which can't run research brain.)"""
    if os.name != "nt" or os.environ.get("RB_APP_WINDOW", "1").strip().lower() in ("0", "browser"):
        return False
    try:
        if importlib.util.find_spec("webview") is None or importlib.util.find_spec("clr") is None:
            return False
    except (ImportError, ValueError):
        return False
    return _webview2_installed()


# ---------------------------------------------------------------- one window
def _pid_file():
    return paths.user_data_dir() / "window.json"


def _single_instance() -> object | None:
    """A handle held while this window is open, or None when another research brain window already is (it's
    brought to the front instead)."""
    import ctypes
    k32 = ctypes.windll.kernel32
    k32.CreateMutexW.restype = ctypes.c_void_p
    h = k32.CreateMutexW(None, False, MUTEX)
    if h and k32.GetLastError() == 183:  # ERROR_ALREADY_EXISTS
        k32.CloseHandle(ctypes.c_void_p(h))
        _bring_to_front()
        return None
    return h


def _bring_to_front() -> None:
    import ctypes
    u32 = ctypes.windll.user32
    hwnd = u32.FindWindowW(None, APP_NAME)
    if hwnd:
        if u32.IsIconic(hwnd):
            u32.ShowWindow(hwnd, 9)  # SW_RESTORE
        u32.SetForegroundWindow(hwnd)


def _flash(win) -> None:
    """A Find papers search finished while you were elsewhere: flash the taskbar button until you come back."""
    import ctypes

    class FLASHWINFO(ctypes.Structure):
        _fields_ = [("cbSize", ctypes.c_uint), ("hwnd", ctypes.c_void_p), ("dwFlags", ctypes.c_uint),
                    ("uCount", ctypes.c_uint), ("dwTimeout", ctypes.c_uint)]
    try:
        hwnd = int(win.native.Handle.ToInt64())
        if ctypes.windll.user32.GetForegroundWindow() == hwnd:
            return
        info = FLASHWINFO(ctypes.sizeof(FLASHWINFO), hwnd, 0x2 | 0xC, 0, 0)  # FLASHW_TRAY | FLASHW_TIMERNOFG
        ctypes.windll.user32.FlashWindowEx(ctypes.byref(info))
    except Exception as e:
        print(f"[research_brain] couldn't flash the taskbar button: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------- the page's channel
class _Page:
    def __init__(self):
        self.unsaved = False      # the page has edits not yet on disk (static/app.js tellWindow)
        self.may_close = False    # the page said close (after its own "Save before closing?" dialog)
        self.win = None


def _on_message(page: _Page, msg) -> None:
    """A message from the page (static/live.js and static/app.js, {rb: {...}})."""
    if not isinstance(msg, dict):
        return
    url = str(msg.get("open") or "")
    if url.startswith(("http://", "https://")):  # window.open() and new-tab links: in your default browser
        webbrowser.open(url)
    if "attention" in msg and page.win is not None:
        _flash(page.win)
    if msg.get("log"):
        print(f"[research_brain] page: {str(msg['log'])[:300]}", file=sys.stderr)
    if "unsaved" in msg:
        page.unsaved = bool(msg["unsaved"])
    if msg.get("close") and page.win is not None:  # the page's dialog said close: now it may
        page.may_close = True
        win = page.win
        threading.Thread(target=win.destroy, daemon=True).start()


def _patch(page: _Page) -> None:
    """Hook pywebview's WebView2 browser: our messages first (its own go on to it), and once WebView2 is
    ready, the right-click menu, the browser's keys (Ctrl+F, zoom) and the microphone for our own page."""
    from webview.platforms import edgechromium
    cls = edgechromium.EdgeChrome
    if getattr(cls, "_rb_patched", False):
        cls._rb_page = page
        return
    cls._rb_page = page
    notify, ready = cls.on_script_notify, cls.on_webview_ready

    def on_script_notify(self, sender, args):
        try:
            data = json.loads(args.get_WebMessageAsJson())
        except Exception:
            data = None
        if isinstance(data, dict) and "rb" in data:
            try:
                _on_message(cls._rb_page, data["rb"])
            except Exception as e:  # never lose the window over a message
                print(f"[research_brain] window message: {e!r}", file=sys.stderr)
            return
        return notify(self, sender, args)

    def on_webview_ready(self, sender, args):
        ready(self, sender, args)
        if not args.IsSuccess:
            return
        try:
            _tune(sender.CoreWebView2)
        except Exception as e:  # its looks and the mic are extras; never lose the window over them
            print(f"[research_brain] window setup: {e!r}", file=sys.stderr)

    cls.on_script_notify = on_script_notify
    cls.on_webview_ready = on_webview_ready
    cls._rb_patched = True


def _local(uri: str) -> bool:
    from urllib.parse import urlsplit
    try:
        return urlsplit(str(uri)).hostname in ("127.0.0.1", "localhost")
    except ValueError:
        return False


def _tune(core) -> None:
    from Microsoft.Web.WebView2.Core import CoreWebView2PermissionKind as Kind
    from Microsoft.Web.WebView2.Core import CoreWebView2PermissionState as State
    s = core.Settings
    s.AreDefaultContextMenusEnabled = True     # copy, paste, spelling; pywebview turns it off
    s.AreBrowserAcceleratorKeysEnabled = True  # Ctrl+F, Ctrl+P, zoom, as in the Chrome window
    allowed = {int(Kind.Microphone), int(Kind.ClipboardRead), int(Kind.Notifications)}

    def asked(_sender, a):
        if int(a.PermissionKind) in allowed and _local(a.Uri):  # research brain's own page only
            a.State = State.Allow
    core.PermissionRequested += asked


# ---------------------------------------------------------------- run
def run(url: str, start) -> int | None:
    """Show the window now, start the server with start() (True once it answers), then load the app.
    Returns when the window closes; None if the window couldn't be shown (the caller opens the Chrome window)."""
    lock = _single_instance()
    if lock is None:
        return 0
    try:
        import webview
        page = _Page()
        _patch(page)
    except Exception as e:
        import traceback
        print(f"[research_brain] no window of its own: {e!r}\n{traceback.format_exc()}", file=sys.stderr)
        return None

    from . import updates
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    win = webview.create_window(APP_NAME, html=LOADING.replace("{more}", ""), width=1400, height=900,
                                min_size=(900, 600), background_color="#EEEFEB", text_select=True)
    page.win = win
    state = {"loaded": False, "ours": False, "failed": False, "updating": False}
    closed = threading.Event()
    win.events.closed += closed.set

    def closing():
        """Closing with edits not yet saved: stay open, and let the page ask with its own dialog."""
        if not page.unsaved or page.may_close or not state["loaded"] or state["updating"]:
            return True
        script = "window.__rb && window.__rb.askBeforeClose ? (window.__rb.askBeforeClose(), 1) : 0"
        threading.Thread(target=lambda: win.evaluate_js(script), daemon=True).start()
        return False
    win.events.closing += closing

    app_url = url + f"/?app=win&v={__version__}"

    def started():
        # WebView2 that won't start (pywebview only logs it) leaves an empty window: use the Chrome one instead
        if not win.events.loaded.wait(30):
            state["failed"] = True
            win.destroy()
            return
        if not start():
            log = paths.user_data_dir() / "app.log"
            win.load_html(LOADING.replace("is starting{more}", f"didn't start.<br><br>What it said is at the end of {log}"))
            return
        win.load_url(app_url)
        state["loaded"] = True
        state["ours"] = bool((_status(url, "/api/heartbeat") or {}).get("idle_exit"))
        misses = 0
        while not closed.wait(3):
            if updates.in_progress():  # the update replaces the files this window runs from; it reopens it
                state["updating"] = True
                win.destroy()
                return
            if _status(url, timeout=2) is not None:
                misses = 0
                continue
            misses += 1  # research brain stopped some other way: start it again and reload
            if misses < 2 or closed.is_set():
                continue
            misses = 0
            if not start():
                win.destroy()
                return
            if not closed.is_set():
                win.load_url(app_url)
                state["ours"] = bool((_status(url, "/api/heartbeat") or {}).get("idle_exit"))

    storage = paths.user_data_dir() / "window"
    storage.mkdir(parents=True, exist_ok=True)
    pid_file = _pid_file()
    try:
        paths.write_json_atomic(pid_file, {"pid": os.getpid(), "port": int(url.rsplit(":", 1)[-1])})
    except (OSError, ValueError):
        pass
    icon = paths.app_root() / "static" / "icons" / "icon.ico"
    try:
        webview.start(started, private_mode=False, storage_path=str(storage), gui="edgechromium",
                      icon=str(icon) if icon.exists() else None)
    except Exception as e:
        import traceback
        print(f"[research_brain] the window stopped: {e!r}\n{traceback.format_exc()}", file=sys.stderr)
        if not state["loaded"]:
            state["failed"] = True
    finally:
        try:
            pid_file.unlink(missing_ok=True)
        except OSError:
            pass
    if state["failed"]:
        return None
    if state["ours"] and not state["updating"]:
        _quit_server(url)
    # end now: .NET's and pythonnet's shutdown add seconds to every close
    sys.stdout.flush()
    sys.stderr.flush()
    os._exit(0)

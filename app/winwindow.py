"""research brain in its own Windows window: no Chrome or Edge window, no browser tab, no title bar.

`research-brain open` on Windows (what the Start menu entry runs) shows research brain in a window of its own
(pywebview on WebView2, the Edge engine built into Windows 10 and 11) instead of a Chrome/Edge app window:
- no title bar, like the Mac window: research brain's header is the title bar. Pressing an empty part of it
  drags the window (to the top of the screen maximizes it, to a side snaps it), a double-click maximizes,
  a right-click shows the window menu, and its own minimize, maximize and close buttons sit at its right end
  (static/live.js draws them). The window keeps Windows' own resize borders, shadow and rounded corners;
  a thin strip above the header, painted the header's color, resizes it from the top;
- its own taskbar button and the brain icon;
- the window shows "research brain is starting" at once, while the server starts;
- closing it stops research brain (if this window started it); if research brain stops some other way, it
  is started again, and the window closes only if it won't start;
- an update (app/updates.py) closes it so the install can replace the files it runs from, and the update
  script opens it again when the new version is up (`window.json` tells the update which process to wait for);
- links that open a new tab or window open in your default browser, downloads ask where to save, the right
  click menu (copy, paste) works, and voice mode may use the microphone on research brain's own page.

The page talks to it over WebView2's own channel (static/live.js: chrome.webview.postMessage({rb: ...})), and
hears back the same way ({rb: {window: "maximized" | "normal"}}, {rb: {active: true | false}}).
The header works as the title bar with WebView2 123 or newer (CSS app-region); with an older one, with
RB_WIN_TITLEBAR=native, or if the window procedure can't be hooked, the window keeps Windows' own title bar
and the page loads without `titlebar=custom`.
Without pywebview, without WebView2, with RB_APP_WINDOW=browser (or 0), or if the window doesn't come up,
`open` uses the Chrome/Edge window as before (app/launcher.py).
"""
from __future__ import annotations

import importlib.util
import json
import os
import re
import sys
import threading
import webbrowser

from . import __version__, paths
from .macwindow import APP_NAME, HEADER_H, LOADING, _quit_server, _status

WEBVIEW2_KEY = "{F3017226-FE2A-4295-8BDF-00C3A9A7E4C5}"  # the Evergreen WebView2 Runtime's EdgeUpdate client
MUTEX = "Local\\research-brain-window"
APP_REGION_MIN = 123  # the first WebView2 runtime with CSS app-region (draggable regions)

# window messages and hit-test codes (winuser.h)
WM_SIZE, WM_CLOSE, WM_NCCALCSIZE, WM_NCHITTEST, WM_NCACTIVATE, WM_DPICHANGED = 0x0005, 0x0010, 0x0083, 0x0084, 0x0086, 0x02E0
HTCLIENT, HTCAPTION, HTTOP, HTTOPLEFT, HTTOPRIGHT = 1, 2, 12, 13, 14
STRIP_PX = 4      # the resize strip above the header, in 96-dpi pixels
CORNER_PX = 16    # how far in from each end of the strip it resizes diagonally


# ---------------------------------------------------------------- pure helpers (tested on every OS)
def _version_major(version: str) -> int:
    m = re.match(r"\s*(\d+)", str(version or ""))
    return int(m.group(1)) if m else 0


def _titlebar_mode(version: str, env: str = "") -> str:
    """"custom" (the header is the title bar) or "native" (Windows' own title bar)."""
    if str(env or "").strip().lower() in ("native", "0", "off"):
        return "native"
    return "custom" if _version_major(version) >= APP_REGION_MIN else "native"


def _hit(x: int, y: int, width: int, strip: int, maximized: bool, header: int = 0, corner: int = CORNER_PX) -> int:
    """What a point in the window's client area is (client pixels): the top resize strip (HTTOP, or a top
    corner), the header band (HTCAPTION: only asked there when WebView2 passes a draggable point through), or
    0 for "the page's own"."""
    if maximized:
        strip = 0
    if y < 0 or x < 0 or x >= max(width, 1):
        return 0
    if y < strip:
        if x < corner:
            return HTTOPLEFT
        if x >= width - corner:
            return HTTOPRIGHT
        return HTTOP
    if header and y < strip + header:
        return HTCAPTION
    return 0


def _strip(dpi: int, maximized: bool) -> int:
    return 0 if maximized else max(1, round(STRIP_PX * (dpi or 96) / 96))


# ---------------------------------------------------------------- is it usable here
def _webview2_version() -> str:
    """The installed WebView2 runtime's version ("" if none). Windows 11 always has one; Windows 10 with any
    recent Edge."""
    try:
        import winreg
    except ImportError:
        return ""
    keys = [(winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\WOW6432Node\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}"),
            (winreg.HKEY_LOCAL_MACHINE, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}"),
            (winreg.HKEY_CURRENT_USER, rf"SOFTWARE\Microsoft\EdgeUpdate\Clients\{WEBVIEW2_KEY}")]
    best = ""
    for root, path in keys:
        try:
            with winreg.OpenKey(root, path) as k:
                pv, _ = winreg.QueryValueEx(k, "pv")
        except OSError:
            continue
        pv = str(pv or "")
        if pv and pv != "0.0.0.0" and _version_major(pv) >= _version_major(best):
            best = pv
    return best


def _webview2_installed() -> bool:
    return bool(_webview2_version())


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


# ---------------------------------------------------------------- the title bar
class _Chrome:
    """The window's frame: hooked window procedure, resize strip, state the page is told about."""

    def __init__(self):
        self.custom = False       # the header is the title bar (window procedure hooked, caption removed)
        self.form = None
        self.hwnd = 0
        self.core = None          # CoreWebView2, once ready
        self.proc = None          # our WNDPROC (kept referenced, or Windows calls freed memory)
        self.old = None           # the procedure it replaced
        self.dpi = 96
        self.strip = 0
        self.maximized = None     # last told to the page
        self.active = True
        self.failed_once = False


_u32 = None


def _user32():
    """A private user32 handle with exact 64-bit signatures (ctypes.windll's functions are shared with
    pywebview, so their argtypes aren't changed)."""
    global _u32
    if _u32 is not None:
        return _u32
    import ctypes
    from ctypes import wintypes
    u = ctypes.WinDLL("user32", use_last_error=True)
    lres, wp, lp = ctypes.c_ssize_t, ctypes.c_size_t, ctypes.c_ssize_t
    u.SetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int, ctypes.c_void_p]
    u.SetWindowLongPtrW.restype = ctypes.c_void_p
    u.CallWindowProcW.argtypes = [ctypes.c_void_p, wintypes.HWND, ctypes.c_uint, wp, lp]
    u.CallWindowProcW.restype = lres
    u.SetWindowPos.argtypes = [wintypes.HWND, wintypes.HWND, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_int, ctypes.c_uint]
    u.IsZoomed.argtypes = [wintypes.HWND]
    u.ShowWindow.argtypes = [wintypes.HWND, ctypes.c_int]
    u.PostMessageW.argtypes = [wintypes.HWND, ctypes.c_uint, wp, lp]
    u.ScreenToClient.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.POINT)]
    u.GetClientRect.argtypes = [wintypes.HWND, ctypes.POINTER(wintypes.RECT)]
    try:
        u.GetDpiForWindow.argtypes = [wintypes.HWND]
        u.GetDpiForWindow.restype = ctypes.c_uint
        u.GetSystemMetricsForDpi.argtypes = [ctypes.c_int, ctypes.c_uint]
    except AttributeError:  # before Windows 10 1607
        pass
    _u32 = u
    return u


def _wndproc_type():
    import ctypes
    from ctypes import wintypes
    return ctypes.WINFUNCTYPE(ctypes.c_ssize_t, wintypes.HWND, ctypes.c_uint, ctypes.c_size_t, ctypes.c_ssize_t)


def _dpi(c: _Chrome) -> int:
    try:
        return int(_user32().GetDpiForWindow(c.hwnd)) or 96
    except (AttributeError, OSError):
        return 96


def _frame(c: _Chrome) -> int:
    """How far a maximized window reaches past the screen's top edge (its resize border): added back, so the
    header isn't cut off."""
    u = _user32()
    try:
        return int(u.GetSystemMetricsForDpi(33, c.dpi)) + int(u.GetSystemMetricsForDpi(92, c.dpi))  # SM_CYFRAME, SM_CXPADDEDBORDER
    except (AttributeError, OSError):
        return int(u.GetSystemMetrics(33)) + int(u.GetSystemMetrics(92))


def _form(win):
    try:
        if win.native is not None:
            return win.native
    except Exception:
        pass
    from webview.platforms.winforms import BrowserView
    return BrowserView.instances[win.uid]


def _post(c: _Chrome, msg: dict) -> None:
    """Tell the page something (UI thread only)."""
    if c.core is None:
        return
    try:
        c.core.PostWebMessageAsJson(json.dumps({"rb": msg}))
    except Exception as e:
        print(f"[research_brain] couldn't tell the page {msg}: {e!r}", file=sys.stderr)


def _tell_state(c: _Chrome, force: bool = False) -> None:
    try:
        state = "maximized" if _user32().IsZoomed(c.hwnd) else "normal"
    except OSError:
        return
    if force or state != c.maximized:
        c.maximized = state
        _post(c, {"window": state})
    if force:
        _post(c, {"active": c.active})


def _layout(c: _Chrome) -> None:
    """The resize strip above the header: a few pixels (none when maximized), as the form's top padding, so
    the strip is the form's own and answers WM_NCHITTEST (the page's view covers everything else)."""
    if not c.custom or c.form is None:
        return
    try:
        from System.Windows.Forms import Padding
        zoomed = bool(_user32().IsZoomed(c.hwnd))
        strip = _strip(c.dpi, zoomed)
        if strip != c.strip or c.form.Padding.Top != strip:
            c.strip = strip
            c.form.Padding = Padding(0, strip, 0, 0)
    except Exception as e:
        print(f"[research_brain] title bar strip: {e!r}", file=sys.stderr)


def _later(c: _Chrome, fn) -> None:
    """Run fn on the UI thread after the current message (not inside the window procedure)."""
    try:
        from System import Action
        c.form.BeginInvoke(Action(fn))
    except Exception as e:
        print(f"[research_brain] window: {e!r}", file=sys.stderr)


def _wndproc(c: _Chrome, hwnd, msg, wp, lp):
    import ctypes
    from ctypes import wintypes
    u = _user32()
    try:
        if msg == WM_NCCALCSIZE and wp:
            # the client area starts at the window's top (no caption), the sides and bottom keep their borders
            rect = wintypes.RECT.from_address(lp)
            top = rect.top
            r = u.CallWindowProcW(c.old, hwnd, msg, wp, lp)
            rect.top = top + (_frame(c) if u.IsZoomed(hwnd) else 0)
            return r
        if msg == WM_NCHITTEST:
            x, y = ctypes.c_short(lp & 0xFFFF).value, ctypes.c_short((lp >> 16) & 0xFFFF).value
            pt = wintypes.POINT(x, y)
            u.ScreenToClient(hwnd, ctypes.byref(pt))
            rc = wintypes.RECT()
            u.GetClientRect(hwnd, ctypes.byref(rc))
            if 0 <= pt.x < rc.right and 0 <= pt.y < rc.bottom:
                header = round(HEADER_H * c.dpi / 96)
                return _hit(pt.x, pt.y, rc.right, c.strip, bool(u.IsZoomed(hwnd)), header) or HTCLIENT
            return u.CallWindowProcW(c.old, hwnd, msg, wp, lp)  # the side and bottom borders: Windows' own
        if msg == WM_SIZE:
            r = u.CallWindowProcW(c.old, hwnd, msg, wp, lp)
            if wp in (0, 2):  # SIZE_RESTORED, SIZE_MAXIMIZED
                _tell_state(c)
                _later(c, lambda: _layout(c))
            return r
        if msg == WM_NCACTIVATE:
            active = bool(wp)
            if active != c.active:
                c.active = active
                _post(c, {"active": active})
        if msg == WM_DPICHANGED:
            c.dpi = (wp >> 16) & 0xFFFF or c.dpi
            _later(c, lambda: _layout(c))
    except Exception as e:  # never lose a message over the title bar
        if not c.failed_once:
            c.failed_once = True
            print(f"[research_brain] title bar: {e!r}", file=sys.stderr)
    return u.CallWindowProcW(c.old, hwnd, msg, wp, lp)


def _hook(c: _Chrome, win) -> None:
    """Hook the window procedure and drop the caption (UI thread, before the window is shown). On any
    failure the window keeps Windows' own title bar."""
    import ctypes
    u = _user32()
    try:
        c.form = _form(win)
        c.hwnd = int(c.form.Handle.ToInt64())
        c.dpi = _dpi(c)
        c.proc = _wndproc_type()(lambda h, m, w, l: _wndproc(c, h, m, w, l))
        ctypes.set_last_error(0)
        old = u.SetWindowLongPtrW(c.hwnd, -4, ctypes.cast(c.proc, ctypes.c_void_p))  # GWLP_WNDPROC
        if not old:
            raise OSError(f"SetWindowLongPtrW failed ({ctypes.get_last_error()})")
        c.old = old
        c.custom = True
        _layout(c)
        u.SetWindowPos(c.hwnd, None, 0, 0, 0, 0, 0x0020 | 0x0002 | 0x0001 | 0x0004)  # FRAMECHANGED|NOMOVE|NOSIZE|NOZORDER
    except Exception as e:
        import traceback
        print(f"[research_brain] keeping Windows' title bar: {e!r}\n{traceback.format_exc()}", file=sys.stderr)
        if c.old:
            try:
                u.SetWindowLongPtrW(c.hwnd, -4, c.old)
            except Exception:
                pass
        c.custom, c.old = False, None


def _caption(page, what: str) -> None:
    """The header's own minimize, maximize and close buttons (static/live.js)."""
    c = page.chrome
    try:
        u = _user32()
        hwnd = c.hwnd or int(_form(page.win).Handle.ToInt64())
        if what == "minimize":
            u.ShowWindow(hwnd, 6)  # SW_MINIMIZE
        elif what == "maximize":
            u.ShowWindow(hwnd, 9 if u.IsZoomed(hwnd) else 3)  # SW_RESTORE / SW_MAXIMIZE
        elif what == "close":
            u.PostMessageW(hwnd, WM_CLOSE, 0, 0)  # as the close button does: the unsaved-edits check runs
    except Exception as e:
        print(f"[research_brain] window button {what}: {e!r}", file=sys.stderr)


def _paint(page, color: str) -> None:
    """The header's color, for the strip above it."""
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", color or "") or page.chrome.form is None:
        return
    try:
        from System.Drawing import ColorTranslator
        page.chrome.form.BackColor = ColorTranslator.FromHtml(color)
    except Exception as e:
        print(f"[research_brain] title bar color: {e!r}", file=sys.stderr)


# ---------------------------------------------------------------- the page's channel
class _Page:
    def __init__(self):
        self.unsaved = False      # the page has edits not yet on disk (static/app.js tellWindow)
        self.may_close = False    # the page said close (after its own "Save before closing?" dialog)
        self.win = None
        self.chrome = _Chrome()


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
    if msg.get("caption") in ("minimize", "maximize", "close"):
        _caption(page, msg["caption"])
    if isinstance(msg.get("chrome"), str):
        _paint(page, msg["chrome"])
    if msg.get("dictate") is True:  # the chat's mic button: Windows voice typing (Win+H) types into the question (a toggle: never on a stop)
        try:
            from . import winshell
            winshell.voice_typing()
        except (ImportError, AttributeError) as e:
            print(f"[research_brain] voice typing: {e!r}", file=sys.stderr)
        except Exception as e:
            print(f"[research_brain] voice typing: {e!r}", file=sys.stderr)
    if msg.get("close") and page.win is not None:  # the page's dialog said close: now it may
        page.may_close = True
        win = page.win
        threading.Thread(target=win.destroy, daemon=True).start()


def _patch(page: _Page) -> None:
    """Hook pywebview's WebView2 browser: our messages first (its own go on to it), and once WebView2 is
    ready, the header as title bar, the right-click menu, the browser's keys (Ctrl+F, zoom) and the
    microphone for our own page."""
    from webview.platforms import edgechromium
    cls = edgechromium.EdgeChrome
    cls._rb_page = page
    if getattr(cls, "_rb_patched", False):
        return
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
        if args.IsSuccess:
            try:  # before pywebview's first navigation, so the loading page can be dragged too
                _prepare(sender.CoreWebView2, cls._rb_page)
            except Exception as e:
                print(f"[research_brain] title bar setup: {e!r}", file=sys.stderr)
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


def _prepare(core, page: _Page) -> None:
    c = page.chrome
    c.core = core
    if c.custom:
        core.Settings.IsNonClientRegionSupportEnabled = True  # CSS app-region: drag is the title bar

    def navigated(_sender, _args):
        _tell_state(c, force=True)  # every page load starts knowing the window's state
    core.NavigationCompleted += navigated


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
def _work_area() -> tuple[int, int] | None:
    """The main screen's size without the taskbar, in logical (96 dpi) pixels, as pywebview sizes windows."""
    try:
        import ctypes
        from ctypes import wintypes
        rect = wintypes.RECT()
        if not ctypes.windll.user32.SystemParametersInfoW(0x0030, 0, ctypes.byref(rect), 0):  # SPI_GETWORKAREA
            return None
        try:
            dpi = int(ctypes.windll.user32.GetDpiForSystem()) or 96
        except AttributeError:
            dpi = 96
        return (rect.right - rect.left) * 96 // dpi, (rect.bottom - rect.top) * 96 // dpi
    except Exception:
        return None


def _fit(area: tuple[int, int] | None = None, want=(1400, 900), least=(900, 600)):
    """The window's first size and its smallest: 1400 x 900 where it fits, else the screen less a margin, so
    a small laptop screen shows the whole window, its buttons included."""
    area = area if area is not None else _work_area()
    if not area:
        return want, least
    aw, ah = area
    w, h = min(want[0], max(aw - 40, 480)), min(want[1], max(ah - 40, 360))
    return (w, h), (min(least[0], w), min(least[1], h))


def _identity() -> None:
    """One taskbar button of its own (not grouped with other Python programs as pythonw), which the Start menu
    entry and a pinned icon share (app/winshell.py); older Start menu entries get the ID here."""
    try:
        from . import winshell
        winshell.set_app_id()
        winshell.ensure_shortcut()
    except Exception as e:
        print(f"[research_brain] taskbar identity: {e!r}", file=sys.stderr)


def _relaunch(win) -> None:
    """Pinning the running window pins research brain itself, even without the Start menu entry."""
    try:
        from pathlib import Path
        from . import winshell
        pyw = Path(sys.executable).with_name("pythonw.exe")
        exe = pyw if pyw.exists() else Path(sys.executable)
        icon = paths.app_root() / "static" / "icons" / "icon.ico"
        from System import Action
        form = win.native
        cmd = f'"{exe}" -m {__package__ or "app"}.cli open'
        # on the window's own (UI) thread, where its shell properties live
        form.BeginInvoke(Action(lambda: winshell.set_window_relaunch(int(form.Handle.ToInt64()), cmd, APP_NAME,
                                                                     str(icon) if icon.exists() else None)))
    except Exception as e:
        print(f"[research_brain] taskbar relaunch: {e!r}", file=sys.stderr)


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
    _identity()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    mode = _titlebar_mode(_webview2_version(), os.environ.get("RB_WIN_TITLEBAR", ""))
    loading = LOADING
    if mode == "custom":  # no title bar yet: the loading page drags the window
        loading = LOADING.replace("<style>", "<style>html{app-region:drag;-webkit-app-region:drag}", 1)
    (w, h), least = _fit()
    win = webview.create_window(APP_NAME, html=loading.replace("{more}", ""), width=w, height=h,
                                min_size=least, background_color="#EEEFEB", text_select=True)
    page.win = win
    if mode == "custom":
        # before the window is shown (pywebview runs before_show on the UI thread), so it never has a caption
        win.events.before_show += lambda: _hook(page.chrome, win)
    win.events.shown += lambda: _relaunch(win)
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

    def app_url() -> str:
        bar = "&titlebar=custom" if page.chrome.custom else ""  # only once the caption really is gone
        return url + f"/?app=win{bar}&v={__version__}"

    def started():
        # WebView2 that won't start (pywebview only logs it) leaves an empty window: use the Chrome one instead
        if not win.events.loaded.wait(30):
            state["failed"] = True
            win.destroy()
            return
        if not start():
            log = paths.user_data_dir() / "app.log"
            win.load_html(loading.replace("is starting{more}", f"didn't start.<br><br>What it said is at the end of {log}"))
            return
        win.load_url(app_url())
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
                win.load_url(app_url())
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

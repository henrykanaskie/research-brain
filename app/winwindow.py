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
import subprocess
import sys
import threading
import time
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
        self.controller = None    # CoreWebView2Controller, told when the window moves (_moved)
        self.normal = None        # the window's size when last restored by you (outer, pixels)
        self.from_state = False   # minimized or maximized since: the next restore comes back to `normal`
        self.moved_pending = False


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
            if wp in (1, 2) and not c.from_state:  # SIZE_MINIMIZED, SIZE_MAXIMIZED: remember the size to come back to
                c.from_state = True
                c.normal = _normal_size(hwnd)
            if wp == 0 and c.from_state:  # SIZE_RESTORED, back from minimized or maximized
                c.from_state = False
                _later(c, lambda: _restored(c))
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
        if msg in (WM_MOVE, WM_EXITSIZEMOVE, WM_WINDOWPOSCHANGED) and not c.moved_pending:
            c.moved_pending = True
            _later(c, lambda: _moved(c))
    except Exception as e:  # never lose a message over the title bar
        if not c.failed_once:
            c.failed_once = True
            print(f"[research_brain] title bar: {e!r}", file=sys.stderr)
    return u.CallWindowProcW(c.old, hwnd, msg, wp, lp)


WM_MOVE, WM_EXITSIZEMOVE, WM_WINDOWPOSCHANGED = 0x0003, 0x0232, 0x0047


def _controller(c: _Chrome):
    """The WinForms WebView2 control's CoreWebView2Controller (a private field: the control doesn't expose it)."""
    if c.controller is None and c.form is not None:
        from System.Reflection import BindingFlags
        wv = c.form.browser.webview
        flags = BindingFlags.NonPublic | BindingFlags.Public | BindingFlags.Instance | BindingFlags.DeclaredOnly
        seen = []
        t = wv.GetType()
        while t is not None and c.controller is None:
            for m in list(t.GetFields(flags)) + list(t.GetProperties(flags)):
                kind = m.FieldType if hasattr(m, "FieldType") else m.PropertyType
                seen.append(f"{t.Name}.{m.Name}:{kind.Name}")
                if kind.Name == "CoreWebView2Controller":
                    try:
                        c.controller = m.GetValue(wv) if hasattr(m, "FieldType") else m.GetValue(wv, None)
                    except Exception:
                        c.controller = None
                    if c.controller is not None:
                        break
            t = t.BaseType if t.Name != "Control" else None
        if c.controller is None and os.environ.get("RB_WIN_DEBUG") == "1" and not getattr(c, "_told_members", False):
            c._told_members = True
            print(f"[research_brain] WebView2 members: {[x for x in seen if 'ebView' in x or 'ontroller' in x][:40]}",
                  file=sys.stderr, flush=True)
    return c.controller


def _normal_size(hwnd) -> tuple[int, int] | None:
    """The window's restored (normal) size, as Windows keeps it (GetWindowPlacement), outer pixels."""
    import ctypes
    from ctypes import wintypes

    class WINDOWPLACEMENT(ctypes.Structure):
        _fields_ = [("length", wintypes.UINT), ("flags", wintypes.UINT), ("showCmd", wintypes.UINT),
                    ("ptMinPosition", wintypes.POINT), ("ptMaxPosition", wintypes.POINT),
                    ("rcNormalPosition", wintypes.RECT)]
    try:
        wp = WINDOWPLACEMENT()
        wp.length = ctypes.sizeof(wp)
        if not ctypes.windll.user32.GetWindowPlacement(hwnd, ctypes.byref(wp)):
            return None
        r = wp.rcNormalPosition
        return (r.right - r.left, r.bottom - r.top) if r.right > r.left and r.bottom > r.top else None
    except Exception:
        return None


def _restored(c: _Chrome) -> None:
    """Back from minimized or maximized: the size it had, and the page back over the window (Windows Forms
    lays the page out while the window is parked off screen, minimized, and leaves it there: a blank window
    that doesn't answer clicks). Again a moment later, after Windows Forms' own resizing."""
    _back_to_size(c)
    _place_page(c)
    _show_page(c)

    def again():
        try:
            from System import Action
            c.form.BeginInvoke(Action(lambda: (_back_to_size(c), _place_page(c))))
        except Exception:
            pass
    threading.Timer(0.3, again).start()


def _place_page(c: _Chrome) -> None:
    try:
        import ctypes
        from ctypes import wintypes
        wv = c.form.browser.webview
        cr = wintypes.RECT()
        ctypes.windll.user32.GetClientRect(c.hwnd, ctypes.byref(cr))
        top = 0 if ctypes.windll.user32.IsZoomed(c.hwnd) else c.strip
        want = (0, top, cr.right, max(0, cr.bottom - top))
        if (wv.Left, wv.Top, wv.Width, wv.Height) != want:
            if os.environ.get("RB_WIN_DEBUG") == "1":
                print(f"[research_brain] page was at {wv.Left},{wv.Top} {wv.Width}x{wv.Height}: put back at {want}",
                      file=sys.stderr, flush=True)
            from System.Drawing import Rectangle
            wv.Bounds = Rectangle(*want)
    except Exception as e:
        print(f"[research_brain] placing the page: {e!r}", file=sys.stderr)


def _back_to_size(c: _Chrome) -> None:
    """Back from minimized or maximized at the size it had. Windows Forms works the restored size out from
    a title bar it thinks is there, so without this the window comes back taller each time."""
    import ctypes
    from ctypes import wintypes
    try:
        u = ctypes.windll.user32
        if not c.normal or u.IsZoomed(c.hwnd) or u.IsIconic(c.hwnd):
            return
        wr = wintypes.RECT()
        u.GetWindowRect(c.hwnd, ctypes.byref(wr))
        if (wr.right - wr.left, wr.bottom - wr.top) != c.normal:
            if os.environ.get("RB_WIN_DEBUG") == "1":
                print(f"[research_brain] restored at {wr.right - wr.left}x{wr.bottom - wr.top}, back to "
                      f"{c.normal[0]}x{c.normal[1]}", file=sys.stderr, flush=True)
            u.SetWindowPos(c.hwnd, 0, 0, 0, c.normal[0], c.normal[1], 0x0002 | 0x0004 | 0x0010)  # NOMOVE|NOZORDER|NOACTIVATE
    except Exception as e:
        print(f"[research_brain] restored size: {e!r}", file=sys.stderr)


def _show_page(c: _Chrome) -> None:
    """The page is drawn again after the window comes back from minimized: WebView2 stops drawing a hidden
    window and, with the header as the title bar, isn't always told it's back."""
    try:
        ctl = _controller(c)
        if ctl is not None:
            if not ctl.IsVisible:
                if os.environ.get("RB_WIN_DEBUG") == "1":
                    print("[research_brain] page was hidden after a restore: shown", file=sys.stderr, flush=True)
                ctl.IsVisible = True
            ctl.NotifyParentWindowPositionChanged()
    except Exception as e:
        print(f"[research_brain] showing the page: {e!r}", file=sys.stderr)


def _moved(c: _Chrome) -> None:
    """The window moved (a drag by the header, snap, restore): tell WebView2, as Microsoft asks of a host whose
    window moves, so what it places on the screen (menus, tooltips, the IME) follows the window."""
    c.moved_pending = False
    try:
        ctl = _controller(c)
        if ctl is not None:
            ctl.NotifyParentWindowPositionChanged()
        elif os.environ.get("RB_WIN_DEBUG") == "1":
            print("[research_brain] window moved: no WebView2 controller found", file=sys.stderr, flush=True)
    except Exception as e:
        if not c.failed_once:
            c.failed_once = True
            print(f"[research_brain] window moved: {e!r}", file=sys.stderr)


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


_MEASURE_JS = """JSON.stringify({w: innerWidth, h: innerHeight, dpr: devicePixelRatio, sx: screenX, sy: screenY,
  cap: [...document.querySelectorAll('.win-caption button')].map((b) => { const r = b.getBoundingClientRect();
    return [b.getAttribute('aria-label'), Math.round(r.left), Math.round(r.right)]; })})"""


def _measure(win, page, closed) -> None:
    """RB_WIN_DEBUG=1 (tests/winwindow_drive.py): where the page and the window think things are, every few
    seconds, into app.log, so a click that lands in the wrong place can be explained."""
    import ctypes
    from ctypes import wintypes
    win.evaluate_js("""document.addEventListener('pointerdown', (e) => { try { chrome.webview.postMessage({ rb: {
      log: `press at ${e.clientX},${e.clientY} screen ${e.screenX},${e.screenY} on ${e.target.getAttribute('aria-label') ||
        e.target.id || e.target.className || e.target.tagName}` } }); } catch {} }, true); 1""")
    while not closed.wait(4):
        try:
            hwnd = page.chrome.hwnd or int(_form(win).Handle.ToInt64())
            cr = wintypes.RECT()
            _user32().GetClientRect(hwnd, ctypes.byref(cr))
            form = _form(win)
            wv = getattr(getattr(form, "browser", None), "webview", None)
            kids = []
            cb = ctypes.WINFUNCTYPE(ctypes.c_bool, wintypes.HWND, wintypes.LPARAM)

            def each(h, _l):
                name = ctypes.create_unicode_buffer(64)
                ctypes.windll.user32.GetClassNameW(h, name, 64)
                r = wintypes.RECT()
                ctypes.windll.user32.GetWindowRect(h, ctypes.byref(r))
                if ctypes.windll.user32.IsWindowVisible(h):
                    kids.append(f"{name.value}@{r.left},{r.top},{r.right - r.left}x{r.bottom - r.top}")
                return True
            ctypes.windll.user32.EnumChildWindows(hwnd, cb(each), 0)
            p = wintypes.POINT(0, 0)
            ctypes.windll.user32.ClientToScreen(hwnd, ctypes.byref(p))
            print(f"[research_brain] children: client origin {p.x},{p.y}: {kids[:8]}", file=sys.stderr, flush=True)
            print(f"[research_brain] measure: page={win.evaluate_js(_MEASURE_JS)} client={cr.right}x{cr.bottom} "
                  f"form.client={form.ClientSize.Width}x{form.ClientSize.Height} padding={form.Padding.Top} "
                  f"webview={wv.Bounds if wv is not None else None} zoomed={bool(_user32().IsZoomed(hwnd))}",
                  file=sys.stderr, flush=True)
        except Exception as e:
            print(f"[research_brain] measure: {e!r}", file=sys.stderr, flush=True)


def _caption(page, what: str) -> None:
    """The header's own minimize, maximize and close buttons (static/live.js)."""
    if os.environ.get("RB_WIN_DEBUG") == "1":
        print(f"[research_brain] caption button: {what}", file=sys.stderr, flush=True)
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
        self.broken = threading.Event()  # WebView2 couldn't start (pywebview only logs it)


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
    init = cls.__init__

    def __init__(self, *a, **k):
        init(self, *a, **k)
        # more for WebView2 (pywebview sets its own, so WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS isn't heard): on the window
        # started again, no graphics card (run); in tests, a port to ask the page what it's doing
        more = [x for x in (os.environ.get("RB_WV2_ARGS", ""),
                            f"--remote-debugging-port={int(os.environ['RB_WV2_DEBUG_PORT'])}" if os.environ.get("RB_WV2_DEBUG_PORT", "").isdigit() else "") if x]
        if more:
            try:
                props = self.webview.CreationProperties
                props.AdditionalBrowserArguments = (props.AdditionalBrowserArguments or "") + " " + " ".join(more)
            except Exception as e:
                print(f"[research_brain] WebView2 arguments: {e!r}", file=sys.stderr)

    def on_script_notify(self, sender, args):
        try:
            data = json.loads(args.get_WebMessageAsJson())
        except Exception:
            data = None
        if isinstance(data, dict) and "rb" in data:
            try:
                if isinstance(data["rb"], dict) and data["rb"].get("dropped"):
                    _dropped(sender, args, str(data["rb"]["dropped"])[:64])
                    return
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
        if args.IsSuccess and os.environ.get("RB_WIN_FAIL_ONCE") == "1" and not os.environ.get("RB_WV2_RETRY"):
            cls._rb_page.broken.set()  # tests: as if WebView2 had failed to start (see run's started)
            return
        ready(self, sender, args)
        if not args.IsSuccess:
            try:  # what was going on, for window.log
                print(f"[research_brain] WebView2 processes on this window's folder: {_webview_using(_storage())}, "
                      f"try {int(os.environ.get('RB_WV2_RETRY') or 0) + 1}", file=sys.stderr)
            except Exception:
                pass
            cls._rb_page.broken.set()  # the window gives up at once (run's started), not after half a minute
            return
        try:
            _tune(sender.CoreWebView2)
        except Exception as e:  # its looks and the mic are extras; never lose the window over them
            print(f"[research_brain] window setup: {e!r}", file=sys.stderr)

    cls.__init__ = __init__
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


def _dropped_paths(objects) -> list[dict]:
    """[{name, path}] for the File objects a drop handed over (WebView2's CoreWebView2File): files and folders."""
    out = []
    for o in list(objects or [])[:200]:
        path = str(getattr(o, "Path", "") or "")
        if path and os.path.exists(path):
            out.append({"name": os.path.basename(path.rstrip("\\/")), "path": path})
    return out


def _dropped(sender, args, drop_id: str) -> None:
    """The page dropped files or folders (static/live.js droppedPaths): tell it where they are on disk."""
    try:
        paths = _dropped_paths(args.get_AdditionalObjects())
    except Exception as e:
        print(f"[research_brain] dropped files: {e!r}", file=sys.stderr)
        paths = []
    sender.CoreWebView2.PostWebMessageAsJson(json.dumps({"rb": {"dropped": drop_id, "paths": paths}}))


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
def _work_area() -> tuple[int, int, int, int] | None:
    """The main screen without the taskbar (width, height, left, top), in logical (96 dpi) pixels, as pywebview
    sizes and places windows."""
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
        return ((rect.right - rect.left) * 96 // dpi, (rect.bottom - rect.top) * 96 // dpi,
                rect.left * 96 // dpi, rect.top * 96 // dpi)
    except Exception:
        return None


def _fit(area=None, want=(1400, 900), least=(900, 600)):
    """The window's first size, its smallest and where it goes: 1400 x 900 where it fits, else the screen less a margin, so
    a small laptop screen shows the whole window, its buttons included."""
    area = area if area is not None else _work_area()
    if not area:
        return want, least, None
    aw, ah, ax, ay = (tuple(area) + (0, 0))[:4]
    w, h = min(want[0], max(aw - 40, 480)), min(want[1], max(ah - 40, 360))
    # centred on that screen by us: Windows' own placement can leave its right end, and buttons, off it
    return (w, h), (min(least[0], w), min(least[1], h)), (ax + (aw - w) // 2, ay + (ah - h) // 2)


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


STORAGE_MAX = 80  # characters: WebView2 makes files about 170 deeper than its folder


def _storage():
    """WebView2's folder: window/ in the data folder, or, where that is so deep that WebView2's own files would pass
    Windows' 260 characters (a long user name, a data folder moved somewhere deep), a short one of its own in
    AppData\\Local. There WebView2 stopped with "can't read and write to its data directory" and no window came up."""
    from pathlib import Path
    usual = paths.user_data_dir() / "window"
    if len(str(usual)) <= STORAGE_MAX or not os.environ.get("LOCALAPPDATA"):
        return usual
    import hashlib
    tag = hashlib.sha1(str(usual).lower().encode("utf-8")).hexdigest()[:8]  # one per data folder
    return Path(os.environ["LOCALAPPDATA"]) / "research_brain" / f"window-{tag}"


def _webview_using(storage) -> int:
    """How many WebView2 processes (msedgewebview2.exe) have this window's data folder (their command line names it)."""
    import ctypes
    from ctypes import wintypes
    k, ps, nt = ctypes.windll.kernel32, ctypes.windll.psapi, ctypes.windll.ntdll
    k.OpenProcess.restype = ctypes.c_void_p
    want = os.path.normcase(os.path.join(str(storage), "EBWebView"))
    arr, got = (wintypes.DWORD * 8192)(), wintypes.DWORD()
    if not ps.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(got)):
        return 0
    n = 0
    for pid in arr[: got.value // ctypes.sizeof(wintypes.DWORD)]:
        h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
        if not h:
            continue
        try:
            name, size = ctypes.create_unicode_buffer(1024), wintypes.DWORD(1024)
            if not k.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, name, ctypes.byref(size)) or not name.value.lower().endswith("msedgewebview2.exe"):
                continue
            buf, ret = ctypes.create_string_buffer(65536), wintypes.ULONG()
            if nt.NtQueryInformationProcess(ctypes.c_void_p(h), 60, buf, len(buf), ctypes.byref(ret)) != 0:  # ProcessCommandLineInformation
                continue
            length, ptr = ctypes.c_ushort.from_buffer(buf, 0).value, ctypes.c_void_p.from_buffer(buf, 8).value
            if ptr and want in os.path.normcase(ctypes.wstring_at(ptr, length // 2)):
                n += 1
        finally:
            k.CloseHandle(ctypes.c_void_p(h))
    return n


def _wait_for_webview(storage, limit: float = 10.0) -> bool:
    """Wait (up to `limit` s) while another WebView2 still has this window's data folder: one whose window has just
    gone takes a second or more to end, and a new one started meanwhile fails (E_ABORT). True if it's free."""
    t0 = time.monotonic()
    while time.monotonic() - t0 < limit:
        try:
            if not _webview_using(storage):
                return True
        except Exception as e:  # never keep the window from opening over this
            print(f"[research_brain] couldn't look for WebView2: {e!r}", file=sys.stderr)
            return True
        time.sleep(0.25)
    return False


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
    (w, h), least, at = _fit()
    win = webview.create_window(APP_NAME, html=loading.replace("{more}", ""), width=w, height=h,
                                x=at[0] if at else None, y=at[1] if at else None,
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
        # WebView2 that won't start leaves an empty window. On a busy PC (just installed, Defender looking at new files)
        # it said E_ABORT now and then: as soon as that's known, try again as a new window (twice), then the Chrome one
        for _ in range(300):
            if win.events.loaded.wait(0.1) or page.broken.is_set():
                break
        if not win.events.loaded.is_set():
            if page.broken.is_set() and int(os.environ.get("RB_WV2_RETRY") or 0) < 2:
                state["retry"] = True  # it fails now and then on a busy PC (E_ABORT): once more, as a new window
            else:
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
        if os.environ.get("RB_WIN_DEBUG") == "1":
            threading.Thread(target=_measure, args=(win, page, closed), daemon=True).start()
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

    storage = _storage()
    storage.mkdir(parents=True, exist_ok=True)
    if not _wait_for_webview(storage):
        print("[research_brain] another WebView2 still has the window's folder after 10 s", file=sys.stderr)
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
    if state.get("retry"):
        import ctypes
        ctypes.windll.kernel32.CloseHandle(ctypes.c_void_p(lock))  # the new window takes over as the one window
        time.sleep(1)
        print("[research_brain] WebView2 didn't start: trying once more", file=sys.stderr)
        try:
            tries = str(int(os.environ.get("RB_WV2_RETRY") or 0) + 1)
            # without the graphics card the second time: where WebView2 didn't come up (on a VM, a PC busy with new files)
            # its main process had started and nothing else had
            env = {**os.environ, "RB_WV2_RETRY": tries, "RB_WV2_ARGS": "--disable-gpu"}
            subprocess.Popen([sys.executable, "-m", f"{__package__ or 'app'}.cli", *sys.argv[1:]], env=env,
                             close_fds=True, creationflags=0x00000200 | 0x08000000)
            os._exit(0)
        except OSError:
            return None
    if state["failed"]:
        return None
    if state["ours"] and not state["updating"]:
        _quit_server(url)
    # end now: .NET's and pythonnet's shutdown add seconds to every close
    for s in (sys.stdout, sys.stderr):
        if s is not None:  # pythonw has neither
            s.flush()
    os._exit(0)

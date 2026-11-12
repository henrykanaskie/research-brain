"""research brain in its own Mac window: no Chrome window, no title bar.

`research-brain open` on a Mac (what the app in /Applications runs) shows research brain in a native window
(pywebview, on WebKit, what Safari uses) instead of a Chrome app window:
- no title bar: a normal Mac window (so resizing, the red/yellow/green buttons, Cmd+W and Cmd+Q are all
  native) whose title bar is transparent with no title, the page running under it; the buttons sit at the
  left end of research brain's header, and pressing on an empty part of the header drags the window
  (a double-click zooms it), the way a title bar does;
- the window shows "Starting research brain" at once, while the server starts;
- closing the window, or Quit (Cmd+Q, the Dock icon), stops research brain; if research brain stops some
  other way, the window closes;
- links that open a new tab or window open in your default browser, and downloads ask where to save.

Without pywebview, or with RB_APP_WINDOW=browser (or 0), `open` uses the Chrome/default-browser window.
"""
from __future__ import annotations

import importlib.util
import json
import os
import sys
import time
import urllib.request
import webbrowser

from . import paths

APP_NAME = "research brain"
HEADER_H = 52  # static/style.css --topbar-h: the band that drags the window like a title bar

LOADING = """<!doctype html><meta charset="utf-8"><title>research brain</title>
<style>html,body{margin:0;height:100%;background:#EEEFEB;color:#5A6169;font:14px -apple-system,system-ui,sans-serif}
@media (prefers-color-scheme:dark){html,body{background:#121518;color:#9AA1A9}}
body{display:flex;align-items:center;justify-content:center;-webkit-user-select:none;cursor:default}
p{margin:0}b{font:italic 500 17px Georgia,serif;color:inherit}</style>
<p><b>research brain</b> &nbsp;is starting{more}</p>"""


def available() -> bool:
    """A native window can be used here: a Mac, pywebview installed, and not switched off."""
    if sys.platform != "darwin" or os.environ.get("RB_APP_WINDOW", "1").strip().lower() in ("0", "browser"):
        return False
    try:
        return importlib.util.find_spec("webview") is not None
    except (ImportError, ValueError):
        return False


def _status(url: str, path: str = "/api/status", timeout: float = 1.0) -> dict | None:
    try:
        with urllib.request.urlopen(url + path, timeout=timeout) as r:
            return json.loads(r.read() or b"{}") if r.status == 200 else None
    except (OSError, ValueError):
        return None


def _quit_server(url: str) -> None:
    """Stop the server when this window started it (it runs with an idle exit); leave one you started
    yourself (`research-brain serve`) running."""
    beat = _status(url, "/api/heartbeat")
    if not beat or not beat.get("idle_exit"):
        return
    try:
        req = urllib.request.Request(url + "/api/quit", data=b"{}", method="POST",
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=3).close()
    except OSError:
        pass


def _identity() -> None:
    """Menu bar name and Dock icon: research brain, not Python."""
    import AppKit
    from Foundation import NSBundle
    info = NSBundle.mainBundle().localizedInfoDictionary() or NSBundle.mainBundle().infoDictionary()
    try:
        info["CFBundleName"] = APP_NAME
    except (TypeError, AttributeError):
        pass
    icon = paths.app_root() / "static" / "icons" / "icon-512.png"
    if icon.exists():
        img = AppKit.NSImage.alloc().initWithContentsOfFile_(str(icon))
        if img:
            AppKit.NSApplication.sharedApplication().setApplicationIconImage_(img)


class _Header:
    """Where the header has buttons and fields (sent by static/live.js): a press anywhere else in the header
    band drags the window. Rects are [x, y, w, h] in page pixels, which are window points."""

    def __init__(self):
        self.keep_clear: list = []


def _titlebar(nsw) -> None:
    """A normal titled window, its title bar transparent and untitled, the page underneath it."""
    import AppKit
    nsw.setStyleMask_(nsw.styleMask() | AppKit.NSWindowStyleMaskFullSizeContentView)
    nsw.setTitlebarAppearsTransparent_(True)
    nsw.setTitleVisibility_(AppKit.NSWindowTitleHidden)
    if hasattr(nsw, "setTitlebarSeparatorStyle_"):
        nsw.setTitlebarSeparatorStyle_(0)  # NSTitlebarSeparatorStyleNone (macOS 11+): no line under it
    # pywebview paints the title bar's background (a non-frameless window); make it clear again
    container = nsw.standardWindowButton_(AppKit.NSWindowCloseButton)
    view = container.superview().superview() if container is not None and container.superview() else None
    for v in ([view] if view is not None else []) + list(view.subviews() if view is not None else []):
        if hasattr(v, "setBackgroundColor_"):
            v.setBackgroundColor_(AppKit.NSColor.clearColor())


def _in(rects, x: float, y: float) -> bool:
    return any(r[0] <= x <= r[0] + r[2] and r[1] <= y <= r[1] + r[3] for r in rects)


def _drag(nsw, event) -> None:
    nsw.performWindowDragWithEvent_(event)


def _zoom(nsw) -> None:
    """Double-click: what the user chose for title bars (System Settings > Desktop & Dock)."""
    import AppKit
    action = AppKit.NSUserDefaults.standardUserDefaults().stringForKey_("AppleActionOnDoubleClick") or "Maximize"
    if action == "Minimize":
        nsw.performMiniaturize_(None)
    elif action != "None":
        nsw.performZoom_(None)


def _header_press(nsw, header: _Header, event):
    """A mouse-down in the window: on an empty part of the header, drag (or zoom on a double-click) the
    window and keep the click from the page; anything else goes on as usual. Returns the event or None."""
    if event.window() is not nsw:
        return event
    p = event.locationInWindow()
    y = nsw.frame().size.height - p.y  # from the top, like the page
    if y > HEADER_H or nsw.styleMask() & (1 << 14):  # below the header, or full screen
        return event
    hit = nsw.contentView().superview().hitTest_(p)
    if hit is not None and "WebKit" not in type(hit).__name__ and "WKWebView" not in type(hit).__name__:
        return event  # the red/yellow/green buttons
    if _in(header.keep_clear, p.x, y):
        return event  # a button or field in research brain's header
    if event.clickCount() >= 2:
        _zoom(nsw)
    else:
        _drag(nsw, event)
    return None


def _style(nsw, header: _Header, keep: list) -> None:
    import AppKit
    try:
        _titlebar(nsw)
    except Exception as e:  # never lose the window over its looks; say why in app.log
        import traceback
        print(f"[research_brain] couldn't hide the title bar: {e!r}\n{traceback.format_exc()}", file=sys.stderr)
    keep.append(AppKit.NSEvent.addLocalMonitorForEventsMatchingMask_handler_(
        AppKit.NSEventMaskLeftMouseDown, lambda e: _header_press(nsw, header, e)))


def _on_message(header: _Header, body: str) -> None:
    """A message from the page (static/live.js, window.webkit.messageHandlers.rb): the header's buttons and
    fields, or a link to open in your browser. Its own channel: pywebview's js_api needs eval, which
    research brain's Content-Security-Policy doesn't allow."""
    try:
        msg = json.loads(body)
    except (TypeError, ValueError):
        return
    if not isinstance(msg, dict):
        return
    if "keep_clear" in msg:
        try:
            header.keep_clear = [[float(v) for v in r[:4]] for r in (msg["keep_clear"] or [])][:200]
        except (TypeError, ValueError, IndexError):
            pass
    url = str(msg.get("open") or "")
    if url.startswith(("http://", "https://")):  # window.open() and new-tab links: in your default browser
        webbrowser.open(url)


_handler_class = None


def _listen(win, header: _Header, keep: list) -> None:
    """Register the page's message channel, `rb`, on the window's WebKit view."""
    global _handler_class
    import Foundation
    from webview.platforms.cocoa import BrowserView
    if _handler_class is None:
        class RBMessages(Foundation.NSObject):
            def userContentController_didReceiveScriptMessage_(self, controller, message):
                _on_message(self.header, str(message.body()))
        _handler_class = RBMessages
    view = BrowserView.instances[win.uid].webview
    handler = _handler_class.alloc().init()
    handler.header = header
    view.configuration().userContentController().addScriptMessageHandler_name_(handler, "rb")
    keep.append(handler)


def run(url: str, start) -> int:
    """Show the window now, start the server with start() (True once it answers), then load the app.
    Returns when the window closes or the app quits."""
    import webview
    from PyObjCTools import AppHelper

    _identity()
    webview.settings["ALLOW_DOWNLOADS"] = True
    webview.settings["OPEN_EXTERNAL_LINKS_IN_BROWSER"] = True
    header = _Header()
    win = webview.create_window(APP_NAME, html=LOADING.replace("{more}", ""), width=1400, height=900,
                                min_size=(900, 600), background_color="#EEEFEB")
    keep: list = []  # the event monitor lives as long as the window
    state = {"loaded": False, "stopped": False}

    def stop():
        """Quit (Cmd+Q, the Dock) or the window closed: stop research brain, once."""
        if state["loaded"] and not state["stopped"]:
            state["stopped"] = True
            _quit_server(url)

    import AppKit
    # Quit ends the process before webview.start() returns, so stop research brain as the app terminates
    keep.append(AppKit.NSNotificationCenter.defaultCenter().addObserverForName_object_queue_usingBlock_(
        AppKit.NSApplicationWillTerminateNotification, None, None, lambda _n: stop()))

    def started():
        win.events.shown.wait(20)  # the native window exists from here on
        AppHelper.callAfter(_style, win.native, header, keep)
        AppHelper.callAfter(_listen, win, header, keep)
        if not start():
            log = paths.user_data_dir() / "app.log"
            win.load_html(LOADING.replace("is starting{more}", f"didn't start.<br><br>What it said is at the end of {log}"))
            return
        win.load_url(url + "/?app=mac")
        state["loaded"] = True
        misses = 0
        while True:  # research brain stopped some other way (the idle exit, `research-brain update`): close
            time.sleep(3)
            if _status(url, timeout=2) is not None:
                misses = 0
                continue
            misses += 1
            if misses >= 2:
                win.destroy()
                return

    storage = paths.user_data_dir() / "window"
    storage.mkdir(parents=True, exist_ok=True)
    webview.start(started, private_mode=False, storage_path=str(storage))
    stop()
    return 0

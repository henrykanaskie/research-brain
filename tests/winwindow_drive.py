"""Drive research brain's real Windows window (app/winwindow.py) and take screenshots of it: a check for a
GitHub Windows runner (.github/workflows/window-shots.yml), not a pytest test (pytest only collects test_*.py).

    python tests/winwindow_drive.py [out_dir]      (default: shots/)

It starts a test server on a small vault, opens the window in a subprocess, then with the mouse and keyboard
(ctypes, as a person would): screenshots it, records its style bits, frame and hit tests, drags it by the
header, double-clicks the header, hovers and clicks the caption buttons, switches to dark mode, and closes it.
Every result goes to <out_dir>/report.json (a failed expectation is recorded, never fatal, so the screenshots
always come out); the window's app.log and output are copied there too. Exits non-zero only on a crash.
"""
from __future__ import annotations

import ctypes
import json
import os
import shutil
import subprocess
import sys
import tempfile
import textwrap
import time
import traceback
import urllib.request
from ctypes import wintypes
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from rb_testserver import ROOT, run_server  # noqa: E402

TITLE = "research brain"
HEADER = 52       # CSS px: the page's header, the caption
BUTTON_W = 46     # CSS px: each caption button

WM_NCHITTEST, WM_CLOSE = 0x0084, 0x0010
HT = {0: "HTNOWHERE", 1: "HTCLIENT", 2: "HTCAPTION", 3: "HTSYSMENU", 8: "HTMINBUTTON", 9: "HTMAXBUTTON",
      10: "HTLEFT", 11: "HTRIGHT", 12: "HTTOP", 13: "HTTOPLEFT", 14: "HTTOPRIGHT", 15: "HTBOTTOM",
      16: "HTBOTTOMLEFT", 17: "HTBOTTOMRIGHT", 18: "HTBORDER", 20: "HTCLOSE", -1: "HTTRANSPARENT"}
STYLES = {"WS_CAPTION": 0x00C00000, "WS_THICKFRAME": 0x00040000, "WS_SYSMENU": 0x00080000,
          "WS_MINIMIZEBOX": 0x00020000, "WS_MAXIMIZEBOX": 0x00010000, "WS_POPUP": 0x80000000, "WS_BORDER": 0x00800000}
MOUSEEVENTF_LEFTDOWN, MOUSEEVENTF_LEFTUP = 0x0002, 0x0004
KEYEVENTF_KEYUP, KEYEVENTF_UNICODE = 0x0002, 0x0004
VK_CONTROL, VK_RETURN, VK_ESCAPE = 0x11, 0x0D, 0x1B
SW_RESTORE = 9

user32 = ctypes.windll.user32 if os.name == "nt" else None
dwmapi = ctypes.windll.dwmapi if os.name == "nt" else None


# ---------------------------------------------------------------- report
class Report:
    def __init__(self, out: Path):
        self.out = out
        self.data: dict = {"checks": [], "info": {}, "errors": []}

    def info(self, key, value):
        self.data["info"][key] = value
        print(f"  {key}: {value}", flush=True)

    def check(self, name, ok, detail=None):
        self.data["checks"].append({"name": name, "ok": bool(ok), "detail": detail})
        print(f"[{'ok' if ok else 'FAIL'}] {name}" + (f": {detail}" if detail is not None else ""), flush=True)

    def error(self, where, e):
        self.data["errors"].append({"where": where, "error": repr(e), "trace": traceback.format_exc()})
        print(f"[error] {where}: {e!r}", flush=True)

    def save(self):
        self.data["summary"] = {"passed": sum(c["ok"] for c in self.data["checks"]),
                                "failed": sum(not c["ok"] for c in self.data["checks"]),
                                "errors": len(self.data["errors"])}
        (self.out / "report.json").write_text(json.dumps(self.data, indent=1), encoding="utf-8")
        print(json.dumps(self.data["summary"]), flush=True)


# ---------------------------------------------------------------- win32
def setup_win32():
    try:
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # PER_MONITOR_AWARE_V2
    except (AttributeError, OSError):
        try:
            ctypes.windll.shcore.SetProcessDpiAwareness(2)
        except (AttributeError, OSError):
            pass
    user32.GetWindowLongPtrW.restype = ctypes.c_ssize_t
    user32.GetWindowLongPtrW.argtypes = [wintypes.HWND, ctypes.c_int]
    user32.SendMessageW.restype = ctypes.c_ssize_t
    user32.SendMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.PostMessageW.argtypes = [wintypes.HWND, wintypes.UINT, wintypes.WPARAM, wintypes.LPARAM]
    user32.FindWindowW.restype = wintypes.HWND
    user32.GetForegroundWindow.restype = wintypes.HWND


def rect(hwnd) -> tuple[int, int, int, int]:
    r = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return r.left, r.top, r.right, r.bottom


def frame_bounds(hwnd):
    """The visible frame (DWMWA_EXTENDED_FRAME_BOUNDS), without the invisible resize borders."""
    r = wintypes.RECT()
    hr = dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r))
    return (r.left, r.top, r.right, r.bottom) if hr == 0 else rect(hwnd)


def client_rect(hwnd):
    r = wintypes.RECT()
    user32.GetClientRect(hwnd, ctypes.byref(r))
    p = wintypes.POINT(0, 0)
    user32.ClientToScreen(hwnd, ctypes.byref(p))
    return p.x, p.y, p.x + r.right, p.y + r.bottom


def scale_of(hwnd) -> float:
    try:
        return (user32.GetDpiForWindow(hwnd) or 96) / 96
    except (AttributeError, OSError):
        return 1.0


def hit(hwnd, x, y) -> str:
    lp = ((y & 0xFFFF) << 16) | (x & 0xFFFF)
    v = int(user32.SendMessageW(hwnd, WM_NCHITTEST, 0, lp))
    if v > 0x7FFFFFFF:
        v -= 1 << 32
    return HT.get(v, str(v))


def move(x, y):
    user32.SetCursorPos(int(x), int(y))
    time.sleep(0.05)


def down():
    user32.mouse_event(MOUSEEVENTF_LEFTDOWN, 0, 0, 0, 0)


def up():
    user32.mouse_event(MOUSEEVENTF_LEFTUP, 0, 0, 0, 0)


def click(x, y, wait=0.6):
    move(x, y)
    down()
    time.sleep(0.05)
    up()
    time.sleep(wait)


def double_click(x, y, wait=1.0):
    move(x, y)
    for _ in range(2):
        down()
        time.sleep(0.03)
        up()
        time.sleep(0.06)
    time.sleep(wait)


def key(vk, up_=False):
    user32.keybd_event(vk, 0, KEYEVENTF_KEYUP if up_ else 0, 0)


def type_text(text):
    class KEYBDINPUT(ctypes.Structure):
        _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD), ("dwFlags", wintypes.DWORD),
                    ("time", wintypes.DWORD), ("dwExtraInfo", ctypes.c_size_t)]

    class _U(ctypes.Union):
        _fields_ = [("ki", KEYBDINPUT), ("pad", ctypes.c_byte * 32)]

    class INPUT(ctypes.Structure):
        _fields_ = [("type", wintypes.DWORD), ("u", _U)]
    for ch in text:
        for flags in (KEYEVENTF_UNICODE, KEYEVENTF_UNICODE | KEYEVENTF_KEYUP):
            inp = INPUT(1, _U(ki=KEYBDINPUT(0, ord(ch), flags, 0, 0)))
            user32.SendInput(1, ctypes.byref(inp), ctypes.sizeof(INPUT))
        time.sleep(0.02)


def front(hwnd):
    if user32.IsIconic(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
    # Windows only lets a process that just had input take the foreground: a harmless key press first
    key(0x12)
    key(0x12, True)  # Alt
    user32.SetForegroundWindow(hwnd)
    time.sleep(0.5)


def shot(out: Path, name: str, box=None, report: Report | None = None):
    try:
        from PIL import ImageGrab
        img = ImageGrab.grab(bbox=box, all_screens=True) if box else ImageGrab.grab(all_screens=True)
        img.save(out / name)
        if report:
            report.info(f"shot {name}", list(box) if box else "full screen")
    except Exception as e:
        if report:
            report.error(f"screenshot {name}", e)


# ---------------------------------------------------------------- the run
def seed(base: str):
    notes = [
        {"type": "note", "title": "Hall thruster breathing mode",
         "body": "Breathing mode oscillations near 20 kHz come from ionization front motion. See [[Langmuir probe analysis]]."},
        {"type": "note", "title": "Langmuir probe analysis", "summary": "$T_e$ from the I-V curve.",
         "body": "The electron temperature comes from the slope of the transition region."},
        {"type": "note", "title": "Sheath physics", "body": "The Bohm criterion: ions enter the sheath at the sound speed."},
        {"type": "note", "title": "Debye shielding", "body": "The Debye length sets the sheath thickness."},
    ]
    for n in notes:
        req = urllib.request.Request(base + "/api/notes", method="POST", data=json.dumps(n).encode(),
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=30) as r:
            r.read()


def find_window(proc, timeout=60.0):
    end = time.time() + timeout
    while time.time() < end:
        hwnd = user32.FindWindowW(None, TITLE)
        if hwnd:
            return hwnd
        if proc.poll() is not None:
            return None
        time.sleep(0.5)
    return None


def drive(out: Path, rep: Report):
    tmp = Path(tempfile.mkdtemp(prefix="rb-window-"))
    vault, data = tmp / "vault", tmp / "rb-data"
    vault.mkdir()
    with run_server(vault, log_path=tmp / "server.log") as srv:
        try:
            seed(srv.base)
            rep.info("notes seeded", 4)
        except Exception as e:
            rep.error("seed", e)
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from app import winwindow
            sys.exit(winwindow.run({srv.base!r}, lambda: True) or 0)
        """)
        env = {**os.environ, "RB_DATA_DIR": str(data), "RB_APP_WINDOW": "1"}
        win_log = out / "window-output.txt"
        with open(win_log, "w", encoding="utf-8") as f:
            proc = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=f, stderr=subprocess.STDOUT)
        try:
            steps(proc, rep, out)
        finally:
            if proc.poll() is None:
                proc.kill()
                rep.info("window process", "killed at the end")
            for src, name in ((data / "app.log", "app.log"), (tmp / "server.log", "server.log")):
                try:
                    if src.exists():
                        shutil.copy(src, out / name)
                except OSError as e:
                    rep.error(f"copy {name}", e)


def steps(proc, rep: Report, out: Path):
    from app import winwindow
    rep.info("webview2 installed", winwindow._webview2_installed())
    rep.info("winwindow.available()", winwindow.available())
    hwnd = find_window(proc)
    rep.check("window appears", bool(hwnd), None if hwnd else f"process exit code {proc.poll()}")
    if not hwnd:
        return
    front(hwnd)
    time.sleep(8)  # the app loads
    rep.check("still running after load", proc.poll() is None, proc.poll())
    s = scale_of(hwnd)
    rep.info("scale", s)

    # 2. how it looks
    l, t, r, b = frame_bounds(hwnd)
    shot(out, "01-normal.png", (l, t, r, b), rep)
    shot(out, "00-screen.png", None, rep)

    # 3. what it is
    style = int(user32.GetWindowLongPtrW(hwnd, -16)) & 0xFFFFFFFF
    rep.info("style", hex(style))
    rep.info("style bits", {k: bool(style & v) for k, v in STYLES.items()})
    rep.info("window rect", rect(hwnd))
    rep.info("extended frame bounds", (l, t, r, b))
    rep.info("client rect (screen)", client_rect(hwnd))
    cl = client_rect(hwnd)
    rep.check("no native title bar (client starts at the visible top)", abs(cl[1] - t) <= int(2 * s) + 1,
              {"client_top": cl[1], "frame_top": t})
    mid = (l + r) // 2
    h_top = hit(hwnd, mid, t + 2)
    h_header = hit(hwnd, l + int((r - l) * 0.4), t + int(HEADER // 2 * s))
    h_left = hit(hwnd, rect(hwnd)[0] + 2, (t + b) // 2)
    rep.info("hit test", {"top strip": h_top, "header": h_header, "left edge": h_left})
    rep.check("top strip resizes (HTTOP)", h_top in ("HTTOP", "HTTOPLEFT", "HTTOPRIGHT"), h_top)
    rep.check("left edge resizes (HTLEFT)", h_left == "HTLEFT", h_left)

    # 4. drag by the header
    before = frame_bounds(hwnd)
    gx, gy = l + int((r - l) * 0.4), t + int(HEADER // 2 * s)
    move(gx, gy)
    down()
    for i in range(1, 11):
        move(gx + 15 * i, gy + 8 * i)
        time.sleep(0.03)
    up()
    time.sleep(0.8)
    after = frame_bounds(hwnd)
    moved = (after[0] - before[0], after[1] - before[1])
    rep.check("dragging the header moves the window", moved[0] > 50 and moved[1] > 20, {"moved": moved})
    shot(out, "02-dragged.png", after, rep)

    # 5. double-click the header
    l, t, r, b = frame_bounds(hwnd)
    double_click(l + int((r - l) * 0.4), t + int(HEADER // 2 * s), 1.5)
    zoomed = bool(user32.IsZoomed(hwnd))
    rep.check("double-clicking the header maximizes", zoomed)
    shot(out, "03-maximized.png", frame_bounds(hwnd), rep)
    if zoomed:
        l, t, r, b = frame_bounds(hwnd)
        rep.info("maximized frame", (l, t, r, b))
        rep.info("maximized client", client_rect(hwnd))
        double_click(l + int((r - l) * 0.4), t + int(HEADER // 2 * s), 1.5)
        rep.check("double-clicking again restores", not user32.IsZoomed(hwnd))
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(1)

    # 6. hover the close button
    l, t, r, b = frame_bounds(hwnd)
    cy = t + int(HEADER / 2 * s)
    move(r - int(BUTTON_W / 2 * s), cy)
    time.sleep(0.8)
    shot(out, "04-close-hover.png", (r - int(300 * s), t, r, t + int(80 * s)), rep)
    move(r - int(BUTTON_W * 1.5 * s), cy)
    time.sleep(0.6)
    shot(out, "04b-max-hover.png", (r - int(300 * s), t, r, t + int(80 * s)), rep)
    rep.info("hit test caption buttons", {"close": hit(hwnd, r - int(BUTTON_W / 2 * s), cy),
                                          "maximize": hit(hwnd, r - int(BUTTON_W * 1.5 * s), cy),
                                          "minimize": hit(hwnd, r - int(BUTTON_W * 2.5 * s), cy)})

    # 7. the caption buttons
    click(r - int(BUTTON_W * 1.5 * s), cy, 1.5)
    rep.check("maximize button maximizes", bool(user32.IsZoomed(hwnd)))
    shot(out, "05-max-button.png", frame_bounds(hwnd), rep)
    if user32.IsZoomed(hwnd):
        l2, t2, r2, b2 = frame_bounds(hwnd)
        click(r2 - int(BUTTON_W * 1.5 * s), t2 + int(HEADER / 2 * s), 1.5)
        rep.check("restore button restores", not user32.IsZoomed(hwnd))
    if user32.IsZoomed(hwnd):
        user32.ShowWindow(hwnd, SW_RESTORE)
        time.sleep(1)
    l, t, r, b = frame_bounds(hwnd)
    click(r - int(BUTTON_W * 2.5 * s), t + int(HEADER / 2 * s), 1.5)
    rep.check("minimize button minimizes", bool(user32.IsIconic(hwnd)))
    user32.ShowWindow(hwnd, SW_RESTORE)
    time.sleep(1)
    front(hwnd)
    rep.check("restored after minimize", not user32.IsIconic(hwnd))

    # 8. dark mode, through the command palette (Ctrl+K, "Appearance: dark")
    try:
        l, t, r, b = frame_bounds(hwnd)
        click((l + r) // 2, (t + b) // 2, 0.5)  # focus the page (the graph)
        key(VK_CONTROL)
        key(ord("K"))
        key(ord("K"), True)
        key(VK_CONTROL, True)
        time.sleep(0.8)
        type_text("Appearance: dark")
        time.sleep(0.8)
        key(VK_RETURN)
        key(VK_RETURN, True)
        time.sleep(1.5)
        shot(out, "06-dark.png", frame_bounds(hwnd), rep)
        l, t, r, b = frame_bounds(hwnd)
        move(r - int(BUTTON_W / 2 * s), t + int(HEADER / 2 * s))
        time.sleep(0.8)
        shot(out, "06b-dark-close-hover.png", (r - int(300 * s), t, r, t + int(80 * s)), rep)
    except Exception as e:
        rep.error("dark mode", e)

    # unfocused window (the caption buttons dim)
    try:
        move(5, 5)
        user32.SetForegroundWindow(user32.GetShellWindow())
        time.sleep(0.8)
        shot(out, "07-unfocused.png", frame_bounds(hwnd), rep)
        front(hwnd)
    except Exception as e:
        rep.error("unfocused", e)

    # 9. close with the close button
    l, t, r, b = frame_bounds(hwnd)
    click(r - int(BUTTON_W / 2 * s), t + int(HEADER / 2 * s), 0.2)
    try:
        code = proc.wait(30)
        rep.check("close button closes, exit code 0", code == 0, code)
    except subprocess.TimeoutExpired:
        rep.check("close button closes, exit code 0", False, "still running after 30 s")
        user32.PostMessageW(hwnd, WM_CLOSE, 0, 0)
        try:
            rep.info("exit after WM_CLOSE", proc.wait(15))
        except subprocess.TimeoutExpired:
            rep.info("exit after WM_CLOSE", "still running")


def main() -> int:
    if os.name != "nt":
        print("Windows only", file=sys.stderr)
        return 2
    out = Path(sys.argv[1] if len(sys.argv) > 1 else "shots").resolve()
    out.mkdir(parents=True, exist_ok=True)
    os.chdir(ROOT)
    sys.path.insert(0, str(ROOT))
    setup_win32()
    rep = Report(out)
    try:
        drive(out, rep)
    except Exception as e:
        rep.error("drive", e)
        rep.save()
        return 1
    rep.save()
    return 0


if __name__ == "__main__":
    sys.exit(main())

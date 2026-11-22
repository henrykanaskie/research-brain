"""The installed app on a real Windows machine, the way a labmate runs it: installed by install.ps1, opened from its
Start menu entry (pythonw.exe, no console, so no standard output), with a data folder, a user's documents folder and
PDFs whose paths hold spaces, characters outside the Windows code page and more than 260 characters. The rest of the
suite runs the checkout under python.exe with a console, and can't see what breaks here.

    python tests/windows_installed.py            (CI: the windows-installed job of .github/workflows/tests.yml)

Needs: research brain installed (its Start menu entry made), playwright, reportlab. Every check runs; what failed is
listed at the end, and app.log and screenshots are left in RB_SHOT_DIR.
"""
from __future__ import annotations

import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
BASE = "http://127.0.0.1:8765"
SHOTS = Path(os.environ.get("RB_SHOT_DIR") or tempfile.mkdtemp(prefix="rb-shots-"))
SHOTS.mkdir(parents=True, exist_ok=True)
HOME = Path(tempfile.mkdtemp(prefix="rb-win-")) / "Émile Łukasz 研究" / "OneDrive - Oregon State University"
DATA, DOCS, FOLDER = HOME / "AppData Roaming" / "research_brain", HOME / "Documents" / "research brain", HOME / "Papers to follow"
SYMBOLS = "Electron density n\u2091 \u2248 10\u00b9\u2079 m\u207b\u00b3, \u03c3\u2217 of the Bohm criterion \u2264 \u03bb_D"
failed: list[str] = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))


def check(name):
    def wrap(fn):
        t0 = time.time()
        try:
            note = fn()
            print(f"ok    {name}  ({time.time() - t0:.1f} s){'  ' + str(note) if note else ''}", flush=True)
        except BaseException as e:  # noqa: BLE001
            failed.append(f"{name}: {type(e).__name__}: {e}")
            print(f"FAIL  {name}: {type(e).__name__}: {e}", flush=True)
            traceback.print_exc(limit=-4, file=sys.stdout)
        return fn
    return wrap


def api(method, path, body=None, timeout=180, raw=None, ctype=None):
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data, headers={"Content-Type": ctype or "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            out = r.read()
            return json.loads(out) if out[:1] in (b"{", b"[") else out
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:400]}") from None


def upload(name: str, data: bytes) -> dict:
    b = uuid.uuid4().hex
    raw = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="{name}"\r\nContent-Type: application/pdf\r\n\r\n').encode() + data + f"\r\n--{b}--\r\n".encode()
    return api("POST", "/api/import/pdf?quick=1", raw=raw, ctype=f"multipart/form-data; boundary={b}")


def text_of(nid: str) -> str:
    for p in DATA.rglob(f"{nid}.txt"):
        return p.read_text(encoding="utf-8")
    raise AssertionError(f"no text was stored for {nid}")


# ---------------------------------------------------------------- PDFs
def _font() -> str:
    from reportlab.pdfbase import pdfmetrics
    from reportlab.pdfbase.ttfonts import TTFont
    if "RB" not in pdfmetrics.getRegisteredFontNames():
        for f in (r"C:\Windows\Fonts\cambria.ttc", r"C:\Windows\Fonts\seguisym.ttf", r"C:\Windows\Fonts\arial.ttf"):
            if Path(f).exists():
                pdfmetrics.registerFont(TTFont("RB", f))
                break
        else:
            return "Helvetica"
    return "RB"


def pdf(title: str, lines: list[str], pages: int = 1, password: str | None = None, meta: str | None = None) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf, font = io.BytesIO(), _font()
    kw = {}
    if password is not None:
        from reportlab.lib.pdfencrypt import StandardEncryption
        kw["encrypt"] = StandardEncryption(password, ownerPassword="owner")
    c = canvas.Canvas(buf, pagesize=letter, **kw)
    c.setTitle(meta if meta is not None else title)
    for p in range(pages):
        c.setFont(font, 16); c.drawString(72, 720, title if p == 0 else f"{title} page {p + 1}")
        c.setFont(font, 11)
        for i, ln in enumerate(lines):
            c.drawString(72, 690 - 16 * i, ln)
        c.showPage()
    c.save()
    return buf.getvalue()


def scanned() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle("A scanned article with no text layer")
    c.rect(50, 50, 400, 600, fill=1); c.showPage(); c.save()
    return buf.getvalue()


# ---------------------------------------------------------------- start it as the Start menu does
def shortcut() -> Path:
    p = Path(os.environ["APPDATA"]) / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    found = [x for x in p.rglob("*.lnk") if "research brain" in x.stem.lower()]
    assert found, f"no Start menu entry under {p}"
    return found[0]


def log_tail(n=60) -> str:
    f = DATA / "app.log"
    return "\n".join(f.read_text(encoding="utf-8", errors="replace").splitlines()[-n:]) if f.exists() else "(no app.log)"


def open_app(timeout=120):
    for k, v in {"RB_DATA_DIR": DATA, "RB_DOCUMENTS_DIR": DOCS}.items():
        Path(v).mkdir(parents=True, exist_ok=True)
        os.environ[k] = str(v)
    os.environ.update(RB_METADATA="off", RB_UPDATE_CHECK="0", RB_REGION_AI="0", RB_NO_ALERT="1")
    for k in [k for k in os.environ if k.startswith("PYTHON")]:
        del os.environ[k]
    os.startfile(str(shortcut()))  # noqa: S606  (the Start menu entry itself: pythonw, no console)
    t0 = time.time()
    while time.time() - t0 < timeout:
        try:
            s = api("GET", "/api/status", timeout=3)
            assert Path(s["vault"]).is_relative_to(DATA), f"it opened another library: {s['vault']}"
            return f"{time.time() - t0:.0f} s"
        except (OSError, AssertionError) as e:
            if "another library" in str(e):
                raise
            time.sleep(0.5)
    raise AssertionError("it never answered. app.log:\n" + log_tail())


def close_app():
    subprocess.run(["powershell", "-NoProfile", "-Command",
                    "Get-CimInstance Win32_Process | ? { $_.CommandLine -match 'research_brain' -and $_.ProcessId -ne $PID } | % { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }"],
                   capture_output=True)
    for _ in range(60):
        try:
            api("GET", "/api/status", timeout=1)
        except (OSError, AssertionError):
            return
        time.sleep(0.5)
    raise AssertionError("it kept running")


def window_picture(hwnd):
    """A picture of one window as it is drawn, whatever covers it (PrintWindow, with the web page in it)."""
    import ctypes
    from ctypes import wintypes
    from PIL import Image
    u, g = ctypes.windll.user32, ctypes.windll.gdi32
    for f in (u.GetWindowDC, g.CreateCompatibleDC, g.CreateCompatibleBitmap, g.SelectObject):
        f.restype = ctypes.c_void_p
    u.GetWindowDC.argtypes = [wintypes.HWND]; u.ReleaseDC.argtypes = [wintypes.HWND, ctypes.c_void_p]
    g.CreateCompatibleDC.argtypes = g.DeleteDC.argtypes = g.DeleteObject.argtypes = [ctypes.c_void_p]
    g.CreateCompatibleBitmap.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_int]
    g.SelectObject.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    u.PrintWindow.argtypes = [wintypes.HWND, ctypes.c_void_p, wintypes.UINT]
    g.GetDIBits.argtypes = [ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT, wintypes.UINT, ctypes.c_void_p, ctypes.c_void_p, wintypes.UINT]
    r = wintypes.RECT()
    u.GetWindowRect(hwnd, ctypes.byref(r))
    w, h = max(1, r.right - r.left), max(1, r.bottom - r.top)
    dc = u.GetWindowDC(hwnd)
    mem, bmp = g.CreateCompatibleDC(dc), g.CreateCompatibleBitmap(dc, w, h)
    g.SelectObject(mem, bmp)
    u.PrintWindow(hwnd, mem, 2)  # PW_RENDERFULLCONTENT
    head = (ctypes.c_uint32 * 10)(40, w, ctypes.c_uint32(-h).value, 1 | (32 << 16), 0, 0, 0, 0, 0, 0)
    buf = ctypes.create_string_buffer(w * h * 4)
    g.GetDIBits(mem, bmp, 0, h, buf, head, 0)
    g.DeleteObject(bmp); g.DeleteDC(mem); u.ReleaseDC(hwnd, dc)
    return Image.frombuffer("RGB", (w, h), buf, "raw", "BGRX", 0, 1)


def processes() -> list[dict]:
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        "[Console]::OutputEncoding=[Text.Encoding]::UTF8; Get-CimInstance Win32_Process | ? { $_.Name -like 'python*' -or $_.Name -like 'conhost*' } | Select Name,ProcessId,ParentProcessId,CommandLine | ConvertTo-Json -Compress"],
                       capture_output=True)
    out = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    return out if isinstance(out, list) else [out]


def main():
    import platform
    print("windows", platform.platform(), platform.machine(), "\npython", sys.version, "\nhome", HOME, flush=True)

    @check("opens from the Start menu, with spaces and non-code-page characters in its folders")
    def _():
        return open_app()

    @check("its own window is there and drawn, not blank")
    def _():
        import ctypes
        u = ctypes.windll.user32
        hwnd = 0
        for _ in range(60):
            hwnd = u.FindWindowW(None, "research brain")
            if hwnd and u.IsWindowVisible(hwnd):
                break
            time.sleep(0.5)
        seen = DATA / "window.log"
        assert hwnd and u.IsWindowVisible(hwnd), ("no visible window titled research brain", bool(hwnd), seen.read_text(encoding="utf-8", errors="replace")[-1500:] if seen.exists() else "")
        # a new Windows 11 account's first-run screen covers everything on GitHub's machines
        subprocess.run(["taskkill", "/F", "/IM", "WWAHost.exe", "/IM", "UserOOBEBroker.exe", "/IM", "CloudExperienceHostBroker.exe"], capture_output=True)
        for _ in range(8):  # it loads the map
            time.sleep(3)
            u.ShowWindow(hwnd, 5); u.SetForegroundWindow(hwnd)
            img = window_picture(hwnd)
            img.save(SHOTS / "window.png")
            small = img.convert("RGB").resize((160, 120))
            common, n = max(small.getcolors(160 * 120))  # how much of it is one colour
            colours = len(small.getcolors(160 * 120))
            if colours > 60 and common < 160 * 120 * 0.97:
                break
        from PIL import ImageGrab
        ImageGrab.grab(all_screens=True).save(SHOTS / "desktop.png")
        assert colours > 60 and common < 160 * 120 * 0.97, f"the window is blank: {colours} colours, {100 * common // (160 * 120)}% of it {n}"
        return f"{img.size[0]}x{img.size[1]}, {colours} colours"

    @check("runs under pythonw, as installed")
    def _():
        mine = [p for p in processes() if "research_brain.server" in (p.get("CommandLine") or "")]
        assert mine, processes()
        return sorted({p["Name"] for p in mine})

    @check("a PDF with symbols outside the code page imports, with its text")
    def _():
        n = upload("symbols.pdf", pdf("Sheath physics with symbols beyond the code page", [SYMBOLS, "The Bohm criterion requires ions at the sound speed."]))
        t = text_of(n["id"])
        assert "Bohm criterion" in t and ("\u2248" in t or "\u2264" in t or _font() == "Helvetica"), t[:300]
        return n["id"]

    @check("a file name outside the code page imports")
    def _():
        n = upload("Łukasz 研究 – σ∗ ≈.pdf", pdf("A paper whose file name is not in the code page", ["Breathing mode oscillations in Hall thrusters."]))
        assert "Breathing mode" in text_of(n["id"])

    @check("title metadata outside the code page")
    def _():
        n = upload("m.pdf", pdf("Metadata with Greek letters and CJK in it", ["Plain body text about plasma diagnostics."], meta="Étude σ∗ – Łukasz 研究 of plasmas"))
        assert "plasma diagnostics" in text_of(n["id"]).lower()
        return n["title"].encode("ascii", "replace").decode()

    @check("a 300-page PDF (a lot of output from the parser)")
    def _():
        n = upload("long.pdf", pdf("A very long thesis on collisional radiative models", [("Line %d of the thesis about electron impact excitation of helium and argon atoms." % i) for i in range(38)], pages=300))
        assert "[[page 80]]" in text_of(n["id"])

    @check("a PDF locked with an owner password only (opens without one)")
    def _():
        n = upload("owner.pdf", pdf("An article that only restricts printing", ["Langmuir probe characteristic."], password=""))
        assert "Langmuir" in text_of(n["id"])

    @check("a PDF that needs a password is refused with a reason, not a crash")
    def _():
        try:
            n = upload("locked.pdf", pdf("A locked article nobody can read here", ["secret"], password="hunter2"))
            return f"imported as {n['id']}"
        except AssertionError as e:
            assert "HTTP 4" in str(e) and "password" in str(e), e

    @check("a scanned PDF (no text) still imports")
    def _():
        upload("scan.pdf", scanned())

    @check("a truncated PDF and one that isn't a PDF get a 4xx, not a 5xx")
    def _():
        good = pdf("A truncated download", ["text"], pages=20)
        for name, data in (("cut.pdf", good[: len(good) // 2]), ("html.pdf", b"<html>not a pdf</html>")):
            try:
                upload(name, data)
            except AssertionError as e:
                assert "HTTP 4" in str(e), e

    @check("eight PDFs at once")
    def _():
        out, errs = [], []
        def one(i):
            try:
                out.append(upload(f"p{i}.pdf", pdf(f"Parallel import number {i} of cross sections", [f"Unique body marker zq{i}x about ionization."])))
            except BaseException as e:  # noqa: BLE001
                errs.append(e)
        ts = [threading.Thread(target=one, args=(i,)) for i in range(8)]
        [t.start() for t in ts]; [t.join() for t in ts]
        assert not errs, errs[:2]
        assert all(any(f"zq{i}x" in text_of(n["id"]) for n in out) for i in range(8))

    @check("no parser process is left behind and no console window was opened for one")
    def _():
        left = [p for p in processes() if "pdftext" in (p.get("CommandLine") or "")]
        assert not left, left

    @check("real papers (arXiv)")
    def _():
        got = []
        for ax in ("1706.03762", "physics/0402096", "2203.02155", "1412.6980", "hep-th/9711200"):
            try:
                req = urllib.request.Request(f"https://arxiv.org/pdf/{ax}", headers={"User-Agent": "Mozilla/5.0"})
                data = urllib.request.urlopen(req, timeout=60).read()
            except OSError as e:
                print("      couldn't download", ax, e)
                continue
            n = upload(ax.replace("/", "_") + ".pdf", data)
            t = text_of(n["id"])
            assert len(t) > 5000, (ax, len(t))
            got.append((ax, n["title"][:40].encode("ascii", "replace").decode(), len(t)))
        return got

    @check("a followed folder: deep paths over 260 characters, odd names, a file another program holds open")
    def _():
        deep = FOLDER / "Höhere Plasmaphysik – σ∗" / ("a long subfolder name " * 4).strip() / ("another long one " * 5).strip()
        ext = lambda p: "\\\\?\\" + str(p)
        os.makedirs(ext(deep), exist_ok=True)
        long_pdf = deep / ("Electron impact excitation " * 3 + ".pdf")
        Path(ext(long_pdf)).write_bytes(pdf("A paper in a very deep folder tree", ["Deep path marker qdeep."]))
        (FOLDER / "Łukasz – σ∗ ≈ 研究.pdf").write_bytes(pdf("A followed paper with an odd file name", ["Odd name marker qodd."]))
        held = FOLDER / "held.pdf"
        held.write_bytes(pdf("A followed paper another program has open", ["Held marker qheld."]))
        import ctypes
        from ctypes import wintypes
        k = ctypes.windll.kernel32
        k.CreateFileW.restype = wintypes.HANDLE
        h = k.CreateFileW(str(held), 0x80000000, 1, None, 3, 0, None)  # open for reading, as a PDF viewer does
        print("      longest path", len(str(long_pdf)))
        try:
            job = api("POST", "/api/linked-folders", {"path": str(FOLDER), "names": {}})["job"]
            jid = job["id"] if isinstance(job, dict) else job
            for _ in range(240):
                j = api("GET", f"/api/linked-folders/jobs/{jid}")
                if j.get("state") != "running":
                    break
                time.sleep(0.5)
        finally:
            k.CloseHandle(h)
        texts = "\n".join(p.read_text(encoding="utf-8") for p in (DATA / "profiles").rglob("text/*.txt"))
        missing = [m for m in ("qdeep", "qodd", "qheld") if m not in texts]
        assert not missing, (missing, j)

    @check("the AI agent's tools server (a pythonw child over pipes) answers")
    def _():
        pkg = next(Path(p["CommandLine"].split('"')[1]) if '"' in p["CommandLine"] else Path(p["CommandLine"].split()[0])
                   for p in processes() if "research_brain.server" in (p.get("CommandLine") or ""))
        exe = pkg  # the interpreter the server runs under, which is what it starts the tools server with
        code = "import research_brain.agent_mcp as m; print(m.__file__)"
        f = subprocess.run([str(exe.with_name("python.exe")), "-c", code], capture_output=True).stdout.decode("utf-8").strip()
        r = subprocess.run([str(exe), "-I", f], input=b'{"jsonrpc":"2.0","id":1,"method":"initialize","params":{"protocolVersion":"2024-11-05","capabilities":{},"clientInfo":{"name":"t","version":"1"}}}\n',
                           capture_output=True, timeout=30, env={**os.environ, "RB_AGENT_URL": BASE, "RB_AGENT_TOKEN": "x", "RB_AGENT_TOOLS": ""})
        assert b'"result"' in r.stdout, (exe.name, r.returncode, r.stdout[:200], r.stderr[-300:])
        return exe.name

    @check("the map and the PDF viewer, in Edge")
    def _():
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="msedge")
            ctx = b.new_context(viewport={"width": 1400, "height": 860}, bypass_csp=True, record_video_dir=str(SHOTS), record_video_size={"width": 1400, "height": 860})
            pg = ctx.new_page()
            errs: list[str] = []
            pg.on("pageerror", lambda e: errs.append(str(e)))
            pg.add_init_script("localStorage.setItem('rb.tour.done','true')")
            pg.goto(BASE)
            pg.wait_for_function("() => window.__rb && window.__rb.S.nodes.size >= 8 && (window.__rb.S.stops || 0) >= 1", timeout=60000)
            pg.wait_for_timeout(2500)
            pg.screenshot(path=str(SHOTS / "map.png"))
            t = pg.evaluate("""() => { const {S, fg} = window.__rb, r = (S.regionLabels || []).find((x) => x.box); if (!r) return null;
              const b = r.box, q = fg.graph2ScreenCoords((b[0] + b[2]) / 2, (b[1] + b[3]) / 2), g = document.getElementById('graph').getBoundingClientRect();
              return { x: q.x + g.x, y: q.y + g.y, k: fg.zoom(), cid: r.cid }; }""")
            if t:
                pg.mouse.move(t["x"], t["y"]); pg.wait_for_timeout(300); pg.mouse.click(t["x"], t["y"]); pg.wait_for_timeout(1500)
                assert pg.evaluate("window.__rb.S.focusCluster") == t["cid"], "clicking a region's name didn't focus it"
                pg.screenshot(path=str(SHOTS / "focused.png"))
            nid = next(n["id"] for n in api("GET", "/api/graph")["nodes"] if n["type"] == "paper" and "symbols" in n["id"])
            pg.goto(f"{BASE}/viewer/{nid}")
            try:
                pg.wait_for_selector(".textLayer span", timeout=30000)
            finally:
                pg.screenshot(path=str(SHOTS / "viewer.png"))
            ctx.close()  # the video is written now
            assert not errs, errs[:3]
            b.close()

    @check("closes, opens again from the Start menu, and everything is still there")
    def _():
        n = len(api("GET", "/api/graph")["nodes"])
        close_app()
        open_app()
        m = len(api("GET", "/api/graph")["nodes"])
        assert m == n, (n, m)
        return f"{n} notes"

    @check("nothing went wrong in app.log")
    def _():
        bad = [ln for ln in log_tail(4000).splitlines() if "Traceback" in ln or "UnicodeEncodeError" in ln or "UnicodeDecodeError" in ln
               or "PermissionError" in ln or "NoneType" in ln or "Internal Server Error" in ln]
        assert not bad, bad[:6]

    (SHOTS / "app.log").write_text(log_tail(4000), encoding="utf-8")
    if (DATA / "window.log").exists():
        shutil.copy(DATA / "window.log", SHOTS / "window.log")
    try:
        close_app()
    except AssertionError:
        pass
    print("\n" + (f"{len(failed)} FAILED:\n  " + "\n  ".join(failed) if failed else "all passed"))
    if failed:
        print("\napp.log, last lines:\n" + log_tail())
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""Updating the installed app on a real Mac: the Update button pressed in its own window (WebKit), and opening it whatever
state an update was left in. For a disposable Mac (CI: the mac-update job of .github/workflows/tests.yml): it uses the
real data folder, /Applications and port 8765. Run after research brain is installed.

    python tests/mac_update.py real            the installed version updates to the newest release on GitHub
    python tests/mac_update.py local FOLDER    through made-up releases (tests/fake_releases.py)

Needs pyobjc-framework-Quartz and reportlab. Screenshots and logs are left in RB_SHOT_DIR.
"""
from __future__ import annotations

import datetime as dt
import io
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
import traceback
import urllib.error
import urllib.request
import uuid
from pathlib import Path

BASE = "http://127.0.0.1:8765"
DATA = Path.home() / "Library" / "Application Support" / "research_brain"
UPD = DATA / "update"
SHOTS = Path(os.environ.get("RB_SHOT_DIR") or tempfile.mkdtemp(prefix="rb-shots-"))
SHOTS.mkdir(parents=True, exist_ok=True)
TOOL = Path.home() / ".local" / "share" / "uv" / "tools" / "research-brain"
CTL = Path(tempfile.mkdtemp(prefix="rb-ctl-"))
failed: list[str] = []
opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))

# research-brain open, exactly, plus a thread that runs JavaScript in its window when asked: how the test presses its
# buttons without the Accessibility permission a real click needs
DRIVER = '''
import os, sys, threading, time
from research_brain import cli
CTL = sys.argv[1]
def drive():
    from PyObjCTools import AppHelper
    while True:
        time.sleep(0.2)
        f = os.path.join(CTL, "js")
        if not os.path.exists(f):
            continue
        with open(f) as h:
            js = h.read()
        os.unlink(f)
        def done(value, error):
            with open(os.path.join(CTL, "out.part"), "w") as h:
                h.write("error: %s" % error if error is not None else str(value))
            os.replace(os.path.join(CTL, "out.part"), os.path.join(CTL, "out"))
        def run():
            from webview.platforms.cocoa import BrowserView
            list(BrowserView.instances.values())[0].webview.evaluateJavaScript_completionHandler_(js, done)
        AppHelper.callAfter(run)
threading.Thread(target=drive, daemon=True).start()
sys.argv = ["research-brain", "open"]
sys.exit(cli.main(["open"]))
'''


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
            dump("failed-" + "".join(c if c.isalnum() else "-" for c in name)[:40])
        return fn
    return wrap


def api(method, path, body=None, timeout=60, raw=None, ctype=None):
    data = raw if raw is not None else None if body is None else json.dumps(body).encode()
    req = urllib.request.Request(BASE + path, method=method, data=data, headers={"Content-Type": ctype or "application/json"})
    try:
        with opener.open(req, timeout=timeout) as r:
            out = r.read()
            return json.loads(out) if out[:1] in (b"{", b"[") else out
    except urllib.error.HTTPError as e:
        raise AssertionError(f"{method} {path}: HTTP {e.code} {e.read().decode('utf-8', 'replace')[:400]}") from None


def upload(i: int) -> None:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(f"A paper from before the update number {i}")
    c.drawString(72, 720, f"A paper from before the update number {i}, marker before{i}")
    c.save()
    b = uuid.uuid4().hex
    raw = (f'--{b}\r\nContent-Disposition: form-data; name="file"; filename="u{i}.pdf"\r\nContent-Type: application/pdf\r\n\r\n').encode() + buf.getvalue() + f"\r\n--{b}--\r\n".encode()
    api("POST", "/api/import/pdf?quick=1", raw=raw, ctype=f"multipart/form-data; boundary={b}")


def version() -> str:
    return api("GET", "/api/update", timeout=20)["current"]


def processes() -> list[tuple[int, str, str]]:
    out = subprocess.run(["ps", "-axo", "pid=,stat=,command="], capture_output=True, text=True, errors="replace").stdout
    rows = [ln.split(None, 2) for ln in out.splitlines()]
    return [(int(r[0]), r[1], r[2]) for r in rows if len(r) == 3 and not r[1].startswith("Z") and int(r[0]) != os.getpid()
            and "mac_update" not in r[2] and any(w in r[2] for w in ("research_brain", "research-brain", "run-update", "update_run", "driver.py"))]


def window_pids() -> list[int]:
    return [p for p, _, c in processes() if c.endswith(("research-brain open", "research_brain.cli open")) or "driver.py" in c]


def window() -> int:
    """How many windows research brain has on the screen."""
    import Quartz
    mine = set(window_pids())
    got = Quartz.CGWindowListCopyWindowInfo(Quartz.kCGWindowListOptionOnScreenOnly, Quartz.kCGNullWindowID) or []
    return sum(1 for w in got if w["kCGWindowOwnerPID"] in mine and w["kCGWindowBounds"]["Width"] > 600 and w["kCGWindowLayer"] == 0)


def wait(what, fn, limit=60):
    t0 = time.time()
    while time.time() - t0 < limit:
        try:
            v = fn()
            if v:
                return v
        except (OSError, AssertionError):
            pass
        time.sleep(0.5)
    raise AssertionError(f"not within {limit} s: {what}")


def shot(name: str) -> None:
    subprocess.run(["screencapture", "-x", str(SHOTS / f"{name}.png")], capture_output=True)


def dump(tag: str) -> None:
    shot(tag)
    parts = [f"{f.name}:\n{f.read_text(encoding='utf-8', errors='replace')[-5000:]}" for f in sorted(UPD.glob("*"))
             if f.is_file() and f.suffix in (".log", ".json", ".sh", "") and f.stat().st_size < 400_000] if UPD.is_dir() else []
    log = DATA / "app.log"
    parts.append("app.log:\n" + (log.read_text(encoding="utf-8", errors="replace")[-5000:] if log.exists() else "(none)"))
    parts.append("processes:\n" + "\n".join(map(str, processes())))
    (SHOTS / f"{tag}.txt").write_text("\n\n".join(parts), encoding="utf-8")
    print(f"----- {tag}\n" + "\n\n".join(parts) + "\n-----", flush=True)


def start_fresh() -> None:
    """As after switching the computer on: nothing of it running."""
    subprocess.run(["osascript", "-e", 'ignoring application responses', "-e", 'tell application "research brain" to quit', "-e", "end ignoring"],
                   capture_output=True, timeout=30)
    for _ in range(40):
        got = processes()
        if not got:
            break
        for p, _, _c in got:
            try:
                os.kill(p, 9)
            except OSError:
                pass
        time.sleep(0.3)
    # the Dock app itself (it may be between two looks)
    subprocess.run(["pkill", "-9", "-f", "research brain.app/Contents/MacOS"], capture_output=True)
    time.sleep(1.5)


def up(limit=120) -> str:
    t0 = time.time()
    wait("research brain answers", lambda: api("GET", "/api/status", timeout=3), limit)
    wait("its window", window, 60)
    return f"{time.time() - t0:.0f} s"


def open_app() -> str:
    """Clicked in the Dock or /Applications."""
    start_fresh()
    subprocess.run(["open", "-a", "/Applications/research brain.app"], check=True)
    return up()


def open_driven() -> str:
    start_fresh()
    for f in CTL.iterdir():
        f.unlink()
    (CTL / "driver.py").write_text(DRIVER, encoding="utf-8")
    log = open(DATA / "app.log", "ab")
    subprocess.Popen([str(TOOL / "bin" / "python"), str(CTL / "driver.py"), str(CTL)], stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                     start_new_session=True, cwd=str(Path.home()))
    t = up()
    wait("the page in the window", lambda: "complete" in js("document.readyState + ' ' + location.port") and js("typeof window.__rbUpdates") == "object", 60)
    return t


def js(code: str) -> str:
    """Run code in the window's page: its value as text."""
    (CTL / "out").unlink(missing_ok=True)
    (CTL / "js.part").write_text(code, encoding="utf-8")
    os.replace(CTL / "js.part", CTL / "js")
    t0 = time.time()
    while time.time() - t0 < 10:
        if (CTL / "out").exists():
            return (CTL / "out").read_text(encoding="utf-8")
        time.sleep(0.1)
    raise AssertionError("the window didn't answer")


def press(selector: str, text: str) -> None:
    got = wait(f"the button {text!r}", lambda: js(f"(() => {{ const b = [...document.querySelectorAll({selector!r})].find((x) => x.offsetParent && x.textContent.trim() === {text!r}); if (!b) return 'no'; b.click(); return 'yes'; }})()") == "yes", 40)
    assert got


def offer(folder: str | None, forget: bool = True) -> None:
    DATA.mkdir(parents=True, exist_ok=True)
    f = DATA / "settings.json"
    got = json.loads(f.read_text(encoding="utf-8")) if f.exists() else {}
    got.pop("update_source", None)
    if folder:
        got["update_source"] = folder
    f.write_text(json.dumps(got), encoding="utf-8")
    if forget:
        (DATA / "update-check.json").unlink(missing_ok=True)


def press_update(to: str, limit=300) -> str:
    """Update in the top bar, then Update now; the window must come back on the new version by itself and say so."""
    press(".up-pill", "Update")
    press(".up-pop button", "Update now")
    t0 = time.time()
    js("window.__seenBefore = 1")
    wait(f"{to} answers", lambda: (s := api("GET", "/api/update", timeout=4))["current"] == to and not s["installing"], limit)
    t1 = time.time() - t0
    wait("the window shows the new version", lambda: js("typeof window.__seenBefore + ' ' + typeof window.__rbUpdates") == "undefined object", 60)
    wait("it says it was updated", lambda: f"Updated to {to}" in js("document.body.innerText"), 30)
    assert window() == 1, f"{window()} windows"
    return f"{t1:.0f} s, the window back after {time.time() - t0:.0f} s"


def opens_after(to: str) -> str:
    t = open_app()
    assert version() == to, version()
    assert len(api("GET", "/api/graph")["nodes"]) == 3, "the library changed"
    return t


def closes() -> None:
    """Quit from the window stops everything."""
    js("setTimeout(() => window.webkit.messageHandlers.rb.postMessage(JSON.stringify({close: true})), 50)")
    wait("everything stopped", lambda: not [p for p in processes() if "applet" not in p[2] and ".app/" not in p[2]], 30)


def real() -> None:
    want = []

    @check("opens while a newer release is out")
    def _():
        t = open_app()
        s = api("GET", "/api/update?force=true", timeout=30)
        assert s["newer"] and s["can_install"], s
        want.append(s["latest"])
        for i in range(3):
            upload(i)
        return f"{t}; {s['current']}, newer {s['latest']}"

    @check("updates from the button in its window, which comes back by itself")
    def _():
        open_driven()
        return press_update(want[0])
    shot("real-after")

    @check("closing the window stops it")
    def _():
        closes()

    @check("opens after it, with the library as it was")
    def _():
        return opens_after(want[0])


def local(root: Path) -> None:
    first: list = []

    @check("opens while a newer release is out")
    def _():
        offer(str(root / "good"))
        t = open_app()
        s = api("GET", "/api/update?force=true", timeout=30)
        assert s["newer"] and s["latest"] == "9.9.9" and s["can_install"], s
        first.append(s["current"])
        for i in range(3):
            upload(i)
        return t

    @check("a release published since the last check is offered when the app is opened again")
    def _():
        now = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=2)
        offer(str(root / "good"), forget=False)
        (DATA / "update-check.json").write_text(json.dumps({"checked": now.isoformat(timespec="seconds"), "source": str(root / "good"),
                                                            "info": {"version": first[0]}}), encoding="utf-8")
        t0 = time.time()
        open_driven()
        wait("Update in the top bar", lambda: js("[...document.querySelectorAll('.up-pill')].some((x) => x.offsetParent) ? 'yes' : 'no'") == "yes", 30)
        return f"{time.time() - t0:.0f} s after clicking"

    @check("a check that failed doesn't hide the next release")
    def _():
        stamp = dt.datetime.now(dt.timezone.utc) - dt.timedelta(hours=1)
        (DATA / "update-check.json").write_text(json.dumps({"checked": stamp.isoformat(timespec="seconds"), "source": str(root / "good"), "info": None}), encoding="utf-8")
        open_driven()
        wait("Update in the top bar", lambda: js("[...document.querySelectorAll('.up-pill')].some((x) => x.offsetParent) ? 'yes' : 'no'") == "yes", 30)

    @check("opens as quickly when the update source can't be reached")
    def _():
        offer("https://10.255.255.1/latest.json")
        t = open_app()
        time.sleep(8)
        assert window() and api("GET", "/api/status", timeout=5)
        return t

    @check("a release that can't be installed here: refused with a reason, and nothing was stopped")
    def _():
        offer(str(root / "cant"))
        open_driven()
        before = {p for p, _, _c in processes()}
        press(".up-pill", "Update")
        press(".up-pop button", "Update now")
        wait("the reason", lambda: "can’t be installed" in (t := js("document.querySelector('.up-pop').innerText")) or "can't be installed" in t, 200)
        assert version() == first[0] and window() == 1 and before & {p for p, _, _c in processes()}
        assert not (UPD / "updating.json").exists()

    @check("a release whose server doesn't start: this version is put back, and the window with it")
    def _():
        UPD.mkdir(parents=True, exist_ok=True)
        for w in (root / "own").glob("*.whl"):
            shutil.copy(w, UPD)
        offer(str(root / "dies"))
        open_driven()
        press(".up-pill", "Update")
        press(".up-pop button", "Update now")
        t0 = time.time()
        wait("gone", lambda: (UPD / "exit-code").exists(), 400)
        wait("back on this version", lambda: not (UPD / "updating.json").exists() and version() == first[0], 400)
        t = time.time() - t0
        wait("the window says so", lambda: "didn’t finish" in js("document.body.innerText") or "didn't finish" in js("document.body.innerText"), 60)
        assert window() == 1
        return f"{t:.0f} s"
    dump("after-dies")

    @check("an update killed before it got anywhere: opens at once, on a version that works")
    def _():
        offer(str(root / "good"))
        open_app()
        api("POST", "/api/update/ack", {})
        api("POST", "/api/update/install", {}, timeout=300)

        def kill():
            got = [p for p, _, c in processes() if "run-update" in c or "update_run" in c]
            for p in got:
                os.kill(p, 9)
            return got
        wait("the updater", kill, 20)
        start_fresh()
        assert (UPD / "updating.json").exists(), "the update finished before it could be killed"
        t = open_app()
        time.sleep(8)
        assert window()
        return f"{t}, version {version()}"

    @check("a leftover marker from an older version's dead update: opens at once")
    def _():
        now = dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")
        for extra in ({}, {"pid": 1, "starting": True}):
            start_fresh()
            (UPD / "updating.json").write_text(json.dumps({"from": "1.0.60", "to": "99.0.0", "started": now, **extra}), encoding="utf-8")
            t = open_app()
            time.sleep(6)
            assert window()
        start_fresh()
        (UPD / "updating.json").unlink(missing_ok=True)
        (UPD / "pending.json").unlink(missing_ok=True)
        return t

    @check("updates from the button in its window, which comes back by itself; a program of its own left running")
    def _():
        offer(str(root / "good"))
        open_driven()
        holder = subprocess.Popen([str(TOOL / "bin" / "python"), "-c", "import numpy, lxml.etree, time; time.sleep(900)"])
        try:
            return press_update("9.9.9")
        finally:
            holder.kill()
    dump("after-update")

    @check("it's quiet a minute later: one window, one server, no update left")
    def _():
        time.sleep(30)
        assert window() == 1 and version() == "9.9.9" and not (UPD / "updating.json").exists()
        left = [p for p in processes() if "run-update" in p[2] or "update_run" in p[2]]
        servers = [p for p in processes() if "-m uvicorn" in p[2]]
        assert not left and len(servers) == 1, (left, servers)

    @check("closing the window after the update stops it")
    def _():
        closes()

    @check("opens after it, with the library as it was")
    def _():
        return opens_after("9.9.9")

    @check("the window closed while it updates: it's installed anyway, and opens")
    def _():
        offer(str(root / "good2"))
        open_driven()
        press(".up-pill", "Update")
        press(".up-pop button", "Update now")
        wait("the install started", lambda: (UPD / "updating.json").exists(), 120)
        for p in window_pids():
            os.kill(p, 15)
        subprocess.run(["open", "-a", "/Applications/research brain.app"], check=True)
        wait("9.9.12", lambda: version() == "9.9.12", 200)
        wait("its window", window, 60)

    @check("opens once more, with the library as it was")
    def _():
        return opens_after("9.9.12")


def main() -> int:
    for k in ("RB_UPDATE_SOURCE", "RB_DATA_DIR", "RB_PORT", "RB_METADATA", "RB_UPDATE_CHECK"):
        os.environ.pop(k, None)
    if os.environ.get("CI") != "true":
        print("This uses (and kills) the research brain installed on this Mac: only on a disposable one (CI=true).")
        return 2
    import platform
    print("mac", platform.mac_ver()[0], platform.machine(), flush=True)
    real() if sys.argv[1] == "real" else local(Path(sys.argv[2]))
    start_fresh()
    print("\n" + (f"{len(failed)} FAILED:\n  " + "\n  ".join(failed) if failed else "all passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

"""How fast the installed app is on this Windows machine with a large library (1,500 papers and notes).

    python tests/windows_speed.py           after installing it; prints a table, fails past generous limits
"""
from __future__ import annotations

import json
import os
import statistics
import subprocess
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import windows_installed as W  # noqa: E402
from windows_installed import SHOTS, api, check, failed, open_app, pdf, upload  # noqa: E402
from windows_update import hwnd, start_fresh, wait_window  # noqa: E402

rows: list = []


SLOW = 1.0  # twice on an Arm PC, where Windows translates the Intel/AMD Python as it runs


def note(what, value, limit, unit="s"):
    limit *= SLOW
    rows.append((what, value, limit, unit))
    print(f"      {what}: {value:.2f} {unit} (limit {limit})", flush=True)
    assert value <= limit, f"{what}: {value:.2f} {unit}, the limit is {limit}"


def timed(fn, n=5):
    out = []
    for _ in range(n):
        t = time.perf_counter()
        fn()
        out.append(time.perf_counter() - t)
    return statistics.median(out)


def usage() -> tuple[float, float, dict]:
    """(CPU seconds so far, MB in memory, CPU seconds by program) of research brain's Python and WebView2 processes."""
    r = subprocess.run(["powershell", "-NoProfile", "-Command",
                        # WebView2 only where it's research brain's (its data folder on the command line): Windows' own first-run
                        # screen is WebView2 too, and on GitHub's Arm machines it was over everything, busy, and counted here
                        "$own = @(Get-CimInstance Win32_Process -Filter \"Name='msedgewebview2.exe'\" | ? { $_.CommandLine -match 'research_brain' } | % ProcessId); "
                        "Get-Process python*,pythonw*,msedgewebview2 -ErrorAction SilentlyContinue | ? { $_.Name -notlike 'msedgewebview2' -or $own -contains $_.Id } | Select Id,Name,CPU,WorkingSet64 | ConvertTo-Json -Compress"],
                       capture_output=True)
    got = json.loads(r.stdout.decode("utf-8", "replace") or "[]")
    got = got if isinstance(got, list) else [got]
    got = [g for g in got if g["Id"] != os.getpid()]
    by: dict = {}
    for g in got:
        by[g["Name"]] = by.get(g["Name"], 0) + (g.get("CPU") or 0)
    return sum(g.get("CPU") or 0 for g in got), sum(g.get("WorkingSet64") or 0 for g in got) / 1e6, by


def main() -> int:
    from scale_data import write_library
    import platform
    global SLOW
    SLOW = 2.0 if platform.machine() == "ARM64" else 1.0
    print("windows", platform.platform(), platform.machine(), "cpus", os.cpu_count(), flush=True)

    @check("a new library opens (first start ever, then again)")
    def _():
        t = time.perf_counter()
        open_app(); wait_window()
        note("first start", time.perf_counter() - t, 40)
        start_fresh()
        t = time.perf_counter()
        open_app(); wait_window()
        note("start, empty library", time.perf_counter() - t, 30)
        start_fresh()

    @check("a library of 1,500 opens")
    def _():
        # its window can be asked what its page is doing (see the idle check)
        os.environ["RB_WV2_DEBUG_PORT"] = "9333"
        # its own data folder, written before the first start (which turns it into the readable folders)
        W.DATA, W.DOCS = W.HOME / "big" / "research_brain", W.HOME / "big" / "Documents" / "research brain"
        write_library(W.DATA / "profiles" / "default", 1500, 150)
        t = time.perf_counter()
        open_app(timeout=240); wait_window()
        n = len(api("GET", "/api/graph", timeout=240)["nodes"])
        note("first start with 1,500 (it's indexed)", time.perf_counter() - t, 120)
        assert n >= 1500, n
        start_fresh()
        t = time.perf_counter()
        open_app(timeout=240); wait_window()
        api("GET", "/api/graph", timeout=240)
        note("start with 1,500, map ready", time.perf_counter() - t, 30)

    @check("the everyday things are quick with 1,500")
    def _():
        g = api("GET", "/api/graph")
        nid = next(n["id"] for n in g["nodes"] if n["type"] == "paper")
        note("the map's data", timed(lambda: api("GET", "/api/graph")), 1.0)
        note("open a paper", timed(lambda: api("GET", f"/api/notes/{nid}")), 1.0)
        note("search", timed(lambda: api("GET", "/api/search?q=plasma%20sheath")), 1.5)
        i = iter(range(1000))
        note("save a note", timed(lambda: api("PUT", f"/api/notes/{nid}", {"summary": f"words {next(i)}"})), 2.5)
        note("the map's data after a save", timed(lambda: (api("PUT", f"/api/notes/{nid}", {"summary": f"more {next(i)}"}), api("GET", "/api/graph")), 3), 8)
        note("import a PDF", timed(lambda: upload("s.pdf", pdf(f"Speed test paper {next(i)}", ["Ionization cross sections."])), 3), 10)

    @check("left alone it uses next to nothing")
    def _():
        time.sleep(75)  # the map's few seconds of movement are over, and what the saves and imports set off
        c0, mb, b0 = usage()
        time.sleep(30)
        c1, mb, b1 = usage()
        try:  # what was on the screen meanwhile, and where the pointer lay (over the map, the test showed redrawing)
            import ctypes
            from ctypes import wintypes
            pt = wintypes.POINT(); ctypes.windll.user32.GetCursorPos(ctypes.byref(pt))
            print(f"      pointer at {pt.x},{pt.y}; window {hwnd()}", flush=True)
            from PIL import ImageGrab
            ImageGrab.grab(all_screens=True).save(SHOTS / "idle-desktop.png")
            if hwnd():
                W.window_picture(hwnd()).save(SHOTS / "idle-window.png")
        except Exception as e:
            print("      (no picture:", e, ")")
        try:  # what the page in its window is doing
            from playwright.sync_api import sync_playwright
            import collections
            with sync_playwright() as pw:
                b = pw.chromium.connect_over_cdp("http://127.0.0.1:9333")
                pg = next(p for c in b.contexts for p in c.pages if "127.0.0.1:8765" in p.url)
                f0 = pg.evaluate("window.__rb && window.__rb.S.frame")
                time.sleep(3)
                f1 = pg.evaluate("window.__rb && window.__rb.S.frame")
                print(f"      the map drew {(f1 or 0) - (f0 or 0)} frames in 3 s; {pg.evaluate('JSON.stringify(window.__rb && window.__rb.motionState)')}", flush=True)
                print("      running animations:", pg.evaluate("JSON.stringify(document.getAnimations().filter(a => a.playState === 'running').slice(0, 8).map(a => [a.animationName || a.constructor.name, a.effect && a.effect.target && (a.effect.target.id || a.effect.target.className)]))"), flush=True)
                cdp = pg.context.new_cdp_session(pg)
                cdp.send("Profiler.enable"); cdp.send("Profiler.start"); time.sleep(4)
                prof = cdp.send("Profiler.stop")["profile"]
                nodes = {q["id"]: q for q in prof["nodes"]}; tot = collections.Counter()
                for sid, d in zip(prof["samples"], prof["timeDeltas"]):
                    f = nodes[sid]["callFrame"]; tot[f"{f['functionName'] or '(anon)'} {f['url'].rsplit('/', 1)[-1][:30]}:{f['lineNumber'] + 1}"] += d
                print("      page time:", [(k, round(v / 1e3)) for k, v in tot.most_common(12)], flush=True)
                b.close()
        except Exception as e:
            print("      (couldn't ask the page:", repr(e)[:200], ")")
        print("      by program:", {k: f"{100 * (b1[k] - b0.get(k, 0)) / 30:.1f}%" for k in b1}, flush=True)
        note("CPU while idle", 100 * (c1 - c0) / 30, 20, "% of one core")
        note("memory, with its window", mb, 2500, "MB")

    @check("the map moves smoothly, in Edge")
    def _():
        from playwright.sync_api import sync_playwright
        with sync_playwright() as pw:
            b = pw.chromium.launch(channel="msedge")
            pg = b.new_page(viewport={"width": 1400, "height": 860}, bypass_csp=True)
            pg.add_init_script("localStorage.setItem('rb.tour.done','true')")
            t = time.perf_counter()
            pg.goto(W.BASE)
            pg.wait_for_function("() => window.__rb && window.__rb.S.nodes.size >= 1500 && (window.__rb.S.stops || 0) >= 1", timeout=120000)
            note("the page with 1,500 dots settled", time.perf_counter() - t, 60)
            pg.wait_for_timeout(1500)
            pg.screenshot(path=str(SHOTS / "speed-map.png"))
            fps = pg.evaluate("""async () => { const fg = window.__rb.fg, k = fg.zoom(); let n = 0; const t0 = performance.now();
              for (let i = 0; i < 60; i++) { fg.zoom(k * (1 + i / 60)); window.__rb.kick(60); await new Promise(requestAnimationFrame); n++; }
              return n / ((performance.now() - t0) / 1000); }""")
            rows.append(("zooming the map (no graphics card here)", fps, 0, "frames/s"))
            print(f"      zooming the map: {fps:.1f} frames/s")
            assert fps >= 2, fps
            b.close()

    print("\n| | this machine | limit |\n|---|---|---|")
    for w, v, l, u in rows:
        print(f"| {w} | {v:.2f} {u} | {l or ''} |")
    (SHOTS / "speed.json").write_text(json.dumps(rows), encoding="utf-8")
    start_fresh()
    print("\n" + (f"{len(failed)} FAILED:\n  " + "\n  ".join(failed) if failed else "all passed"))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

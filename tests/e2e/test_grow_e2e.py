"""A dot's lines travel out to its connections when it's pointed at, and back into it when it's let go.

Run in Chromium (Windows' window, browsers) and in WebKit (the Mac window, Safari): it's drawn, frame by frame,
in both. Each frame, the share of each line that's drawn is recorded as the line is drawn (S.probeLink).
"""
from __future__ import annotations

import pytest

from test_e2e import api, clear_vault, open_app

pw_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def browser(request):
    with pw_api.sync_playwright() as pw:
        try:
            b = getattr(pw, request.param).launch(headless=True)
        except pw_api.Error as e:
            pytest.skip(f"no {request.param} for Playwright: {str(e).splitlines()[0]}")
        yield b
        b.close()


RECORD = """(main) => { const rb = window.__rb, S = rb.S; window.__grow = [];
  S.probeLink = (l, g) => { const a = l.source.id ?? l.source, b = l.target.id ?? l.target;
    if (a === main || b === main) window.__grow.push([performance.now(), S.frame, l.__g ?? 1, !!rb.S.hover]); }; }"""


def frames(page):
    """[[time, frame, share drawn], ...] by frame: the smallest share of the dot's lines drawn that frame"""
    rows = page.evaluate("() => window.__grow.splice(0)")
    by = {}
    for t, f, g, _ in rows:
        by[f] = [min(by.get(f, [t, f, 1])[0], t), f, min(by.get(f, [t, f, 1])[2], g)]
    return [by[f] for f in sorted(by)]


def test_lines_travel_out_and_back(ctx, server):
    clear_vault(server)
    main = api(server, "POST", "/api/notes", {"type": "paper", "title": "Hollow cathode plume oscillations"})["id"]
    for t in ("Electron impact ionization of xenon", "Sputter yield of boron nitride", "Hall thruster wall erosion",
              "Magnetic nozzle detachment"):
        api(server, "POST", f"/api/notes/{main}/link", {"target": api(server, "POST", "/api/notes", {"type": "paper", "title": t})["id"]})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 5")
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)
    page.wait_for_timeout(1500)
    box = page.locator("#graph").bounding_box()
    sx, sy = page.evaluate(f"() => {{ const n = window.__rb.S.nodes.get('{main}'), p = window.__rb.fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }}")
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.wait_for_timeout(1500)  # the map is still: the first frame after this comes late
    page.evaluate(RECORD, main)
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    page.wait_for_timeout(800)
    out = frames(page)
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.wait_for_timeout(1000)
    back = frames(page)
    print("out", [round(r[2], 2) for r in out])
    print("back", [round(r[2], 2) for r in back])
    part = [r for r in out if r[2] < 0.95]
    assert len(part) >= 4, f"the lines were there at once: {[round(r[2], 2) for r in out[:12]]}"
    assert out[-1][2] > 0.99
    spread = max(r[0] for r in part) - min(r[0] for r in part)
    assert 120 < spread < 600, spread
    gone = [r for r in back if 0.02 < r[2] < 0.98]
    assert len(gone) >= 4, f"the lines went at once: {[round(r[2], 2) for r in back[:12]]}"
    assert not page.errors

"""A region's name pointed at: the lines it brings out travel out from the region, and back into it when you leave.

Like test_grow_e2e (a dot's lines), in Chromium and WebKit, recorded frame by frame as each line is drawn (S.probeLink).
"""
from __future__ import annotations

import os

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


# lines from region A to region B: not on the resting map, so only the region's name brings them out
RECORD = """(ids) => { const rb = window.__rb, S = rb.S, a = new Set(ids); window.__grow = [];
  S.probeLink = (l, g) => { const s = l.source.id ?? l.source, t = l.target.id ?? l.target;
    if (a.has(s) !== a.has(t)) window.__grow.push([performance.now(), S.frame, l.__g ?? 1, a.has(l.__from)]); }; }"""


def frames(page):
    """[[time, frame, share drawn, all from inside A], ...] by frame: the smallest share of the lines drawn that frame"""
    rows = page.evaluate("() => window.__grow.splice(0)")
    by = {}
    for t, f, g, inside in rows:
        r = by.get(f, [t, f, 1, True])
        by[f] = [min(r[0], t), f, min(r[2], g), r[3] and inside]
    return [by[f] for f in sorted(by)]


def test_region_lines_travel_out_and_back(ctx, server, tmp_path):
    clear_vault(server)
    mk = lambda t: api(server, "POST", "/api/notes", {"type": "paper", "title": t})["id"]
    a = [mk(t) for t in ("Hollow cathode plume oscillations", "Hall thruster wall erosion", "Magnetic nozzle detachment",
                         "Sputter yield of boron nitride")]
    b = [mk(t) for t in ("Cool dwarf atmospheres", "Brown dwarf cloud bands", "Stellar flare rates", "M dwarf habitability")]
    for t in api(server, "GET", "/api/topics"):  # left from a run in the other browser
        if t.get("name") in ("Electric propulsion", "Small stars"):
            api(server, "DELETE", f"/api/topics/{t['slug']}")
    api(server, "POST", "/api/topics", {"name": "Electric propulsion", "members": a})
    api(server, "POST", "/api/topics", {"name": "Small stars", "members": b})
    for x, y in ((0, 0), (1, 1), (2, 2), (0, 3)):
        api(server, "POST", f"/api/notes/{a[x]}/link", {"target": b[y]})
    page = open_app(ctx, server)
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1 && (window.__rb.S.regionLabels || []).filter((r) => r.box).length >= 2", timeout=30000)
    page.wait_for_timeout(1500)
    box = page.locator("#graph").bounding_box()
    tx, ty = page.evaluate("""(id) => { const {S, fg} = window.__rb, cid = S.nodes.get(id).cluster;
      const b = S.regionLabels.find((r) => r.cid === cid).box, p = fg.graph2ScreenCoords((b[0] + b[2]) / 2, (b[1] + b[3]) / 2);
      return [p.x, p.y]; }""", a[0])
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.wait_for_timeout(1500)  # the map is still: the first frame after this comes late
    page.evaluate(RECORD, a)
    page.mouse.move(box["x"] + tx, box["y"] + ty)
    shot = os.environ.get("RB_REGION_SHOT")
    if shot:  # to look at by eye: partway out
        page.wait_for_timeout(110)
        page.screenshot(path=shot)
    page.wait_for_timeout(800)
    assert page.evaluate("() => window.__rb.S.hoverRegion") is not None, "the pointer isn't on the region's name"
    out = frames(page)
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.wait_for_timeout(1000)
    back = frames(page)
    print("out", [round(r[2], 2) for r in out])
    print("back", [round(r[2], 2) for r in back])
    part = [r for r in out if r[2] < 0.95]
    assert len(part) >= 4, f"the lines were there at once: {[round(r[2], 2) for r in out[:12]]}"
    assert out[-1][2] > 0.99
    assert all(r[3] for r in out), "a line grew from the far end, not the region's"
    spread = max(r[0] for r in part) - min(r[0] for r in part)
    assert 120 < spread < 600, spread
    gone = [r for r in back if 0.02 < r[2] < 0.98]
    assert len(gone) >= 4, f"the lines went at once: {[round(r[2], 2) for r in back[:12]]}"
    assert not page.errors

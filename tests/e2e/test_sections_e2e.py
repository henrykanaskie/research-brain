"""Sections on the map: drawn as clouds, dragged as a whole by their name tag (only that section moves, and it
stays where you drop it), and a paper added "into a section" joins it."""
from __future__ import annotations

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
from test_e2e import api, browser, clear_vault, ctx, open_app, server  # noqa: E402,F401  (fixtures are used by name)

PLASMA = "Hall thruster plasma discharge {w} ionization breathing mode oscillations anomalous electron transport xenon"
STARS = "Stellar spectra of cool dwarf stars {w} atmospheres metallicity abundances exoplanet hosts photometry"


def test_sections_drag_and_add_into(ctx, server):
    clear_vault(server)
    for w in ("alpha", "beta", "gamma", "delta"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster oscillations {w}", "abstract": PLASMA.format(w=w)})
    for w in ("one", "two", "three", "four"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Cool dwarf atmospheres {w}", "abstract": STARS.format(w=w)})
    page = open_app(ctx, server)
    page.wait_for_function("(window.__rb.S.stops || 0) >= 1 && (window.__rb.S.regionLabels || []).filter((r) => r.box).length >= 2", timeout=30000)
    page.wait_for_timeout(500)
    tag = page.evaluate("""() => { const r = window.__rb.S.regionLabels.find((r) => r.box), fg = window.__rb.fg, b = r.box,
      p = fg.graph2ScreenCoords((b[0] + b[2]) / 2, (b[1] + b[3]) / 2); return { cid: r.cid, x: p.x, y: p.y }; }""")
    snap = "() => Object.fromEntries([...window.__rb.S.nodes.values()].map((n) => [n.id, [n.x, n.y, n.cluster]]))"
    before = page.evaluate(snap)
    box = page.locator("#graph").bounding_box()
    x, y = box["x"] + tag["x"], box["y"] + tag["y"]
    page.mouse.move(x, y); page.mouse.down()
    for i in range(1, 16):
        page.mouse.move(x + i * 5, y + i * 3)
        page.wait_for_timeout(16)
    page.mouse.up(); page.wait_for_timeout(600)
    after = page.evaluate(snap)
    mine = [k for k, v in before.items() if v[2] == tag["cid"]]
    others = [k for k, v in before.items() if v[2] != tag["cid"]]
    assert mine and all(abs(after[k][0] - before[k][0]) > 5 for k in mine)       # the section came along
    assert all(abs(after[k][0] - before[k][0]) < 0.01 for k in others)           # nothing else moved
    assert page.evaluate("() => Object.keys(JSON.parse(localStorage.getItem('rb.regionPos') || '{}')).length") == 1
    # a new paper, put into that section as it's added
    note = api(server, "POST", "/api/notes", {"type": "paper", "title": "Exoplanet transit timing", "abstract": "Transit timing variations of hot Jupiters"})
    page.evaluate("""async ([nid, cid]) => { await window.__rb.refreshGraph(); const sec = window.__rb.S.clusters.find((c) => c.id === cid);
      await window.__rb.intoSection({ id: nid, title: 'Exoplanet transit timing' }, sec); }""", [note["id"], tag["cid"]])
    page.wait_for_function("""([nid, ids]) => { const S = window.__rb.S, n = S.nodes.get(nid), m = S.nodes.get(ids[0]);
      return n && m && n.cluster === m.cluster; }""", arg=[note["id"], mine], timeout=20000)
    assert not page.errors

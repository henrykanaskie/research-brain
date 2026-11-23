"""A dot's name beside it on the map: a click opens the paper (and a right-click gives the dot's menu), but pointing
at it opens no card and lights nothing (names sit close together: cards flickered open and shut as you passed)."""
from __future__ import annotations

from test_e2e import api, clear_vault, open_app


def test_a_dots_name_is_clicked_like_the_dot(ctx, server):
    clear_vault(server)
    ids = [api(server, "POST", "/api/notes", {"type": "paper", "title": t, "authors": [a], "year": 2020 + i})["id"]
           for i, (t, a) in enumerate([("Hollow cathode plume oscillations", "Ada Kajita"), ("Sputter yield of boron nitride", "Ben Goto"),
                                       ("Hall thruster wall erosion", "Cy Sasaki")])]
    api(server, "POST", f"/api/notes/{ids[0]}/link", {"target": ids[1]})
    page = open_app(ctx, server)
    page.wait_for_function("() => window.__rb.S.nodes.size >= 3 && (window.__rb.S.stops || 0) >= 1", timeout=30000)
    page.wait_for_timeout(1200)
    page.wait_for_function(f"() => (window.__rb.S.labelHits || []).some(h => h[0].id === '{ids[0]}')")
    box = page.locator("#graph").bounding_box()
    x, y = page.evaluate(f"""() => {{ const h = window.__rb.S.labelHits.find(h => h[0].id === '{ids[0]}');
      return [(h[1] + h[3]) / 2 + 6 - (h[3] - h[1]) / 2 + 4, (h[2] + h[4]) / 2]; }}""")  # near the name's start
    # the name isn't the dot itself
    assert page.evaluate(f"() => {{ const n = window.__rb.S.nodes.get('{ids[0]}'), p = window.__rb.fg.graph2ScreenCoords(n.x, n.y); return Math.hypot(p.x - {x}, p.y - {y}); }}") > 8
    page.mouse.move(box["x"] + 5, box["y"] + 5)
    page.mouse.move(box["x"] + x, box["y"] + y, steps=4)
    page.wait_for_timeout(700)
    assert page.evaluate("() => [window.__rb.S.hover, window.__rb.cards.length]") == [None, 0]
    assert page.evaluate("() => document.getElementById('graph').style.cursor") == "pointer"
    page.mouse.click(box["x"] + x, box["y"] + y)
    page.wait_for_function(f"() => window.__rb.S.cur && window.__rb.S.cur.id === '{ids[0]}'", timeout=4000)
    assert not page.errors

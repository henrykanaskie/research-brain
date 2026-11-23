"""A library search left on the map ends with Esc: after Enter opened a match (a first Esc closes the paper, the next
the search), or after a click away from the box. Esc in another field leaves it."""
from __future__ import annotations

from playwright.sync_api import expect

from test_e2e import api, clear_vault, open_app


def test_esc_ends_a_search_left_on_the_map(ctx, server):
    clear_vault(server)
    for t in ("Hall thruster sheath erosion", "Hollow cathode plume", "Magnetic nozzle detachment"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("() => window.__rb.S.nodes.size >= 3 && (window.__rb.S.stops || 0) >= 1", timeout=30000)
    box = page.locator("#search-input")
    count = page.locator("#search-count")
    # typed, then left (a click elsewhere): Esc on the page clears it
    box.fill("hall")
    expect(count).to_have_text("1")
    page.evaluate("() => document.activeElement.blur()")
    page.keyboard.press("Escape")
    expect(count).to_have_text("")
    assert box.input_value() == "" and page.evaluate("() => window.__rb.S.matches.size") == 0
    # Enter opens the first match: the first Esc closes the paper, the second ends the search
    box.fill("hollow")
    box.press("Enter")
    page.wait_for_function("() => window.__rb.S.cur && !document.getElementById('panel').hidden")
    page.evaluate("() => document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Escape")
    page.wait_for_function("() => document.getElementById('panel').hidden")
    expect(count).to_have_text("1")
    page.keyboard.press("Escape")
    expect(count).to_have_text("")
    # Esc in another field leaves the search
    box.fill("nozzle")
    page.evaluate("""() => { const i = document.createElement('input'); i.id = 'other'; document.body.append(i); i.focus(); }""")
    page.keyboard.press("Escape")
    expect(count).to_have_text("1")
    assert not page.errors

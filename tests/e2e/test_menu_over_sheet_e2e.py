"""A menu opened from inside a sheet (Find papers' Add to library asks which region) is on top of it and can be
picked: it opened behind the sheet, out of reach."""
from __future__ import annotations

from playwright.sync_api import expect

from test_e2e import api, clear_vault, open_app


def test_region_menu_from_find_papers_can_be_picked(ctx, server):
    clear_vault(server)
    ids = [api(server, "POST", "/api/notes", {"type": "paper", "title": t})["id"] for t in ("Hall thruster sheath erosion", "Hall thruster breathing mode")]
    api(server, "POST", "/api/topics", {"name": "Thrusters", "members": ids})
    page = open_app(ctx, server)
    page.wait_for_function("() => window.__rb.S.nodes.size >= 2 && (window.__rb.S.clusters || []).length >= 1")
    page.click("#btn-finder")
    sheet = page.locator(".rbk-sheet.wide")
    expect(sheet).to_be_visible()
    # what Add to library does: asks which region, from a button in the sheet
    page.evaluate("""() => { const b = document.querySelector('.rbk-sheet button');
      window.__picked = 'waiting'; window.__rb.chooseSection({ currentTarget: b }).then((c) => { window.__picked = c ? c.label : c; }); }""")
    item = page.locator(".node-menu .menu-item", has_text="Thrusters")
    expect(item).to_be_visible()
    box = item.bounding_box()
    on_top = page.evaluate(f"() => !!document.elementFromPoint({box['x'] + box['width'] / 2}, {box['y'] + box['height'] / 2}).closest('.node-menu')")
    assert on_top, "the menu is behind the sheet"
    item.click()
    page.wait_for_function("() => window.__picked !== 'waiting'")
    assert "Thrusters" in page.evaluate("() => String(window.__picked)")
    expect(sheet).to_be_visible()  # picking it doesn't close Find papers
    assert not page.errors

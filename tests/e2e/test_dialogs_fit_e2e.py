"""Dialogs on a small window (a 1024x640 laptop, or a bigger screen at 125% zoom): each one stays inside the
window, and what doesn't fit scrolls inside the dialog instead of running off the page, with its buttons
reachable. Reorganize with many regions, a bookmarks import, Library settings, Cite, the confirm dialog, and the
Add and Settings menus.

Run:  python -m pytest -q tests/e2e/test_dialogs_fit_e2e.py   (RB_SHOT_DIR=<dir> saves screenshots)
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

W, H = 1024, 640  # a 1280x800 screen at 125%

# how a dialog sits in the window, and whether it scrolls
MEASURE = """(card) => {
  const r = card.getBoundingClientRect(), cs = getComputedStyle(card);
  return { top: r.top, left: r.left, bottom: r.bottom, right: r.right, sh: card.scrollHeight, ch: card.clientHeight,
           oy: cs.overflowY, vw: innerWidth, vh: innerHeight };
}"""


def _api(server, method, path, body=None):
    req = urllib.request.Request(server["base"] + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _shot(page, name):
    d = os.environ.get("RB_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"fit-{name}.png"))


def _fits(page, card, buttons, name):
    """The dialog is inside the window, scrolls where it is taller than it can be, and each button can be scrolled
    to and clicked (a trial click: Playwright's checks that it is visible, stable and not covered)."""
    expect(card).to_be_visible()
    page.wait_for_timeout(250)  # opening animations
    _shot(page, name)
    m = card.evaluate(MEASURE)
    assert m["top"] >= 0 and m["left"] >= 0 and m["bottom"] <= m["vh"] + 0.5 and m["right"] <= m["vw"] + 0.5, f"{name} runs off the window: {m}"
    if m["sh"] > m["ch"] + 1:
        assert m["oy"] in ("auto", "scroll"), f"{name} is taller than it can be and doesn't scroll: {m}"
    for b in buttons:
        b.scroll_into_view_if_needed()
        b.click(trial=True)
        box, cbox = b.bounding_box(), card.bounding_box()
        assert box["y"] >= cbox["y"] - 0.5 and box["y"] + box["height"] <= min(cbox["y"] + cbox["height"], m["vh"]) + 0.5, f"{name}: a button is out of view"


@pytest.fixture(scope="module")
def library(server):
    ids = [_api(server, "POST", "/api/notes", {"type": "paper", "title": f"Paper {i} on sheath physics"})["id"] for i in range(16)]
    for k in range(14):  # more regions than the Reorganize list has room for
        _api(server, "POST", "/api/topics", {"name": f"Region {k}", "members": ids[k:k + 1]})
    return ids


@pytest.fixture()
def page(ctx, server, library):
    p = ctx.new_page()
    p.set_viewport_size({"width": W, "height": H})
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.goto(server["base"] + "/")
    p.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 16")
    yield p
    assert not errors, errors


def test_bulk_dialogs_fit_a_small_window(page):
    page.locator("#btn-reorganize").click()
    card = page.locator(".bk-card[aria-label='Reorganize regions']")
    expect(card.locator(".bk-item")).to_have_count(15)
    _fits(page, card, [card.get_by_role("button", name="Suggest regions"), card.get_by_role("button", name="Cancel")], "reorganize")
    # the long region list scrolls on its own, so the choices under it stay in view
    assert card.locator(".bk-list").first.evaluate("e => e.scrollHeight > e.clientHeight")
    expect(card.get_by_text("Let the AI choose the regions")).to_be_in_viewport()
    card.get_by_role("button", name="Cancel").click()
    expect(card).to_have_count(0)

    bm = "<DL><p>" + "".join(f"<DT><H3>Folder {f}</H3><DL><p>" + "".join(
        f'<DT><A HREF="https://arxiv.org/abs/2101.{f}{i:03d}">Paper {f}-{i} about plasma sheaths</A>' for i in range(12)) + "</DL><p>"
        for f in range(5)) + "</DL>"
    page.evaluate("(bm) => window.__rbBulk.bookmarksDialog(window.__rbBulk.parseBookmarks(bm))", bm)
    card = page.locator(".bk-card[aria-label='Import bookmarks']")
    _fits(page, card, [card.get_by_role("button", name="Import ticked"), card.get_by_role("button", name="Cancel")], "bookmarks")
    card.get_by_role("button", name="Cancel").click()
    expect(card).to_have_count(0)


def test_library_settings_cite_and_confirm_fit_a_small_window(page):
    page.evaluate("window.__rbSettings.open()")
    card = page.locator(".ls-card")
    _fits(page, card, [card.get_by_role("button", name="Done")], "settings")
    card.get_by_role("button", name="Done").click()
    expect(card).to_have_count(0)

    page.evaluate("window.__rbCite.open()")
    card = page.locator(".ct-card")
    _fits(page, card, [card.get_by_role("button", name="Copy bibliography")], "cite")
    page.keyboard.press("Escape")
    expect(card).to_have_count(0)

    # the confirm dialog with a message longer than the window (a long list of what's about to be deleted)
    page.evaluate("""() => { document.getElementById('modal-title').textContent = 'Delete these papers?';
      document.getElementById('modal-text').textContent = Array.from({length: 60}, (_, i) => 'Paper ' + i + ' on sheath physics').join('\\n');
      document.getElementById('modal').hidden = false; }""")
    card = page.locator("#modal .modal-card")
    _fits(page, card, [page.locator("#modal-cancel"), page.locator("#modal-ok")], "confirm")
    expect(page.locator("#modal-ok")).to_be_in_viewport()  # pinned to the bottom, not only reachable by scrolling
    page.evaluate("document.getElementById('modal').hidden = true")


def test_top_bar_menus_fit_a_short_window(page):
    page.set_viewport_size({"width": W, "height": 520})  # the menus fit 640, so shorter still
    page.locator("#btn-view-menu").click()
    menu = page.locator("#view-menu")
    _fits(page, menu, [menu.locator("#btn-export")], "settings-menu")
    page.keyboard.press("Escape")
    page.locator("#btn-add-menu").click()
    menu = page.locator("#add-menu")
    _fits(page, menu, [menu.locator("#btn-zotero")], "add-menu")

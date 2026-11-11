"""Keyboard shortcuts, the command palette, the shortcuts sheet and the first-run tour (Playwright).

Reuses the server/browser fixtures from test_e2e.py. Skipped when Playwright isn't available.
Set RB_SHOT_DIR=/some/dir to save screenshots of the sheet, the palette and a coach mark.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import (  # noqa: E402,F401  (fixtures are used by name)
    api, body_editor, browser, clear_vault, ctx, new_note, open_app, server,
)

MOD = "Meta" if os.uname().sysname == "Darwin" else "Control"


def shot(page, name):
    d = os.environ.get("RB_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"))


def blur(page):
    page.evaluate("document.activeElement && document.activeElement.blur()")


def sheet_rows(page):
    return page.locator(".rbk-shortcuts .rbk-row")


def test_registry_loaded_first_and_no_key_conflicts(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    # every engineer's commands are in (topics.js, review.js keys, views.js focus, research and memory when present)
    ids = page.evaluate("RB.commands.list().map((c) => c.id)")
    for must in ["general.shortcuts", "general.palette", "general.close", "nav.search", "nav.back", "library.newNote",
                 "library.addById", "note.critique", "graph.focus", "review.good", "viewer.addNote", "extension.clip"]:
        assert must in ids, must
    for maybe in ["topics.toggle", "topics.next", "note.park", "research.toggle", "memory.open"]:
        script = Path(__file__).resolve().parents[2] / "static" / (maybe.split(".")[0] if maybe != "note.park" else "topics")
        if script.with_suffix(".js").exists():
            assert maybe in ids, maybe
    conflicts = page.evaluate("RB.commands.conflicts()")
    assert conflicts == [], conflicts
    # each command has a title and a description, and frequent ones have keys
    bad = page.evaluate("RB.commands.list().filter((c) => !c.title || !c.group).map((c) => c.id)")
    assert bad == []
    assert not page.errors


def test_question_mark_opens_sheet_and_search(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Some note"})
    page = open_app(ctx, server)
    blur(page)
    page.keyboard.press("?")
    sheet = page.locator(".rbk-shortcuts")
    expect(sheet).to_be_visible()
    expect(sheet.locator(".rbk-title")).to_have_text("Keyboard shortcuts")
    for g in ["General", "Navigate", "Library", "Note", "Graph", "Review", "Viewer", "Extension"]:
        expect(sheet.locator(f'.rbk-group[data-group="{g}"]')).to_have_count(1)
    shot(page, "shortcuts-sheet")
    search = sheet.locator(".rbk-search input")
    expect(search).to_be_focused()
    search.fill("highlight")
    expect(sheet.locator('.rbk-row[data-cmd="viewer.addNote"]')).to_be_visible()
    assert "Add note from selection" in sheet_rows(page).first.inner_text()
    search.fill("review")
    expect(sheet.locator('.rbk-row[data-cmd="library.review"]')).to_be_visible()
    expect(sheet.locator('.rbk-row[data-cmd="review.good"]')).to_be_visible()
    # a single letter lists the chords that start with it
    search.fill("g")
    first = sheet_rows(page).first
    assert first.get_attribute("data-cmd") in page.evaluate(
        "RB.commands.list().filter((c) => c.seqs.some((s) => s.length > 1 && s[0].key === 'g')).map((c) => c.id)")
    expect(sheet.locator('.rbk-row[data-cmd="library.topics"], .rbk-row[data-cmd="library.review"]').first).to_be_visible()
    search.fill("nothingatallzz")
    expect(sheet.locator(".rbk-empty")).to_be_visible()
    # Esc clears the search, a second Esc closes the sheet
    page.keyboard.press("Escape")
    expect(search).to_have_value("")
    page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)
    # the View menu opens it too
    page.click("#btn-view-menu")
    page.click("#btn-shortcuts")
    expect(page.locator(".rbk-shortcuts")).to_be_visible()
    assert not page.errors


def test_palette_runs_new_note_and_shows_keys(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    page.keyboard.press(f"{MOD}+k")
    pal = page.locator(".rbk-palette")
    expect(pal).to_be_visible()
    shot(page, "command-palette")
    page.keyboard.type("new note")
    on = pal.locator(".rbk-item.on")
    expect(on).to_have_attribute("data-cmd", "library.newNote")
    expect(on.locator(".rbk-cap").first).to_have_text("N")  # the shortcut is shown, so it can be learned
    page.keyboard.press("Enter")
    expect(pal).to_have_count(0)
    expect(page.locator("#panel-body .title-input")).to_be_focused()
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.title === 'Untitled note'")
    # recent actions come first next time
    page.keyboard.press("Escape")  # leave the title
    page.keyboard.press(f"{MOD}+k")
    expect(page.locator(".rbk-palette .rbk-sec").first).to_have_text("Recent")
    expect(page.locator(".rbk-palette .rbk-item").first).to_have_attribute("data-cmd", "library.newNote")
    page.keyboard.press("Escape")
    expect(page.locator(".rbk-palette")).to_have_count(0)
    # Add paper by id: palette command and its own shortcut both open the Add menu's lookup
    blur(page)
    page.keyboard.press(f"{MOD}+Shift+a")
    expect(page.locator("#add-menu")).to_be_visible()
    expect(page.locator("#lookup-input")).to_be_focused()
    page.keyboard.press("Escape")
    expect(page.locator("#add-menu")).to_be_hidden()
    page.click("#btn-add-menu")
    expect(page.locator("#add-menu")).to_be_visible()
    assert not page.errors


def test_back_and_forward(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "note", "title": "First note"})
    b = api(server, "POST", "/api/notes", {"type": "note", "title": "Second note"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 2")
    page.evaluate("(id) => window.__rb.openNote(id)", a["id"])
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=a["id"])
    page.evaluate("(id) => window.__rb.openNote(id)", b["id"])
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=b["id"])
    blur(page)
    page.keyboard.press(f"{MOD}+BracketLeft")
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=a["id"])
    page.keyboard.press(f"{MOD}+BracketRight")
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=b["id"])
    assert not page.errors


def test_no_shortcuts_while_typing_and_latex_tab(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    new_note(page, "Typing note")
    n_before = page.evaluate("window.__rb.S.nodes.size")
    ta = body_editor(page)
    ta.click()
    ta.type("?nasgf/ G then T")  # letters that are shortcuts outside a text field
    expect(page.locator(".rbk-ov:not([hidden])")).to_have_count(0)
    expect(page.locator("#chat")).to_be_hidden()
    assert ta.input_value() == "?nasgf/ G then T"
    assert page.evaluate("window.__rb.S.nodes.size") == n_before
    # the LaTeX autocomplete still owns Tab
    ta.type(" $\\alph")
    expect(page.locator(".le-pop")).to_be_visible()
    page.keyboard.press("Tab")
    expect(page.locator(".le-pop")).to_be_hidden()
    assert ta.input_value().endswith("$\\alpha"), ta.input_value()
    # and Esc closes its popup before anything else
    ta.type(" \\bet")
    expect(page.locator(".le-pop")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator(".le-pop")).to_be_hidden()
    expect(page.locator("#panel")).to_be_visible()
    # Mod+E (allowed while typing) switches to the preview
    page.keyboard.press(f"{MOD}+e")
    expect(page.locator("#panel-body .preview")).to_be_visible()
    assert not page.errors


def test_escape_closes_innermost_first(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Esc order"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 1")  # the graph loads after the app is ready
    nid = page.evaluate("[...window.__rb.S.nodes.keys()][0]")
    page.evaluate("(id) => window.__rb.openNote(id)", nid)
    expect(page.locator("#panel")).to_be_visible()
    page.click("#btn-chat")
    expect(page.locator("#chat")).to_be_visible()
    page.click("#btn-view-menu")
    expect(page.locator("#view-menu")).to_be_visible()
    page.keyboard.press("?")
    expect(page.locator(".rbk-shortcuts")).to_be_visible()
    page.keyboard.press("Escape")       # the sheet
    expect(page.locator(".rbk-shortcuts")).to_have_count(0)
    expect(page.locator("#view-menu")).to_be_visible()
    page.keyboard.press("Escape")       # the menu
    expect(page.locator("#view-menu")).to_be_hidden()
    expect(page.locator("#chat")).to_be_visible()
    blur(page)
    page.keyboard.press("Escape")       # the chat
    expect(page.locator("#chat")).to_be_hidden()
    expect(page.locator("#panel")).to_be_visible()
    page.keyboard.press("Escape")       # the panel
    expect(page.locator("#panel")).to_be_hidden()
    assert not page.errors


def test_chord_and_tooltips_and_empty_hint(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    expect(page.locator("#empty-state .empty-hint")).to_contain_text("Press ? for shortcuts")
    assert "/" in page.locator("#search-input").get_attribute("title")
    assert "A" in page.locator("#btn-chat").get_attribute("title")
    blur(page)
    page.keyboard.press("g")
    expect(page.locator(".rbk-chord")).to_be_visible()
    page.keyboard.press("n")
    expect(page.locator(".rbk-chord")).to_have_count(0)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.title === 'Untitled note'")
    assert not page.errors


def test_tour_and_help(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Tour note"})
    page = open_app(ctx, server)
    # the palette starts the tour
    page.keyboard.press(f"{MOD}+k")
    page.keyboard.type("quick tour")
    expect(page.locator(".rbk-item.on")).to_have_attribute("data-cmd", "help.tour")
    page.keyboard.press("Enter")
    coach = page.locator(".rb-coach")
    expect(coach).to_be_visible()
    expect(coach.locator(".c-step")).to_have_text("1 of 5")
    shot(page, "coach-mark")
    coach.locator("button", has_text="Next").click()
    expect(coach.locator(".c-step")).to_have_text("2 of 5")
    page.keyboard.press("Escape")
    expect(coach).to_have_count(0)
    assert page.evaluate("localStorage.getItem('rb.tour.done')") == "true"
    page.evaluate("RB.commands.run('help.guide')")
    expect(page.locator(".rbk-guide")).to_contain_text("Fragments")
    shot(page, "help-guide")
    page.keyboard.press("Escape")
    expect(page.locator(".rbk-guide")).to_have_count(0)
    assert not page.errors


def test_viewer_uses_the_same_sheet(ctx, server):
    clear_vault(server)
    n = api(server, "POST", "/api/notes", {"type": "paper", "title": "No PDF paper"})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server["base"] + f"/viewer/{n['id']}")
    page.wait_for_function("window.RB && RB.commands && RB.commands.get('viewer.nextPage').passive === false")
    page.keyboard.press("?")
    sheet = page.locator(".rbk-shortcuts")
    expect(sheet).to_be_visible()
    expect(sheet.locator(".rbk-group").first).to_have_attribute("data-group", "Viewer")
    assert page.evaluate("RB.commands.conflicts()") == []
    page.keyboard.press("Escape")
    expect(sheet).to_have_count(0)
    assert not errors, errors


def test_viewer_keys_with_a_pdf(ctx, server, tmp_path):
    from test_e2e import _pdf_bytes
    clear_vault(server)
    pdf = tmp_path / "sheath.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    with page.expect_file_chooser() as fc:
        page.keyboard.press(f"{MOD}+o")  # Import a PDF
    fc.value.set_files(str(pdf))
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.has_pdf", timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")

    v = ctx.new_page()
    errors = []
    v.on("pageerror", lambda e: errors.append(str(e)))
    v.goto(f"{server['base']}/viewer/{nid}")
    span = v.locator(".page[data-page='1'] .textLayer span", has_text="Bohm criterion")
    expect(span).to_be_visible(timeout=30_000)
    v.keyboard.press("n")  # nothing selected: a hint, no popover
    expect(v.locator("#pop")).to_be_hidden()
    box = span.bounding_box()
    v.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    v.mouse.down()
    v.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
    v.mouse.up()
    expect(v.locator("#clipBtn")).to_be_visible()
    v.keyboard.press("n")  # Add note from selection
    expect(v.locator("#pop")).to_be_visible()
    expect(v.locator("#pBody")).to_be_focused()
    v.keyboard.type("jk ns")  # viewer keys don't fire while typing
    assert v.locator("#pBody").input_value() == "jk ns"
    v.keyboard.press(f"{MOD}+Enter")  # Save
    expect(v.locator("#fragItems .frag")).to_have_count(1)
    expect(v.locator("#pop")).to_be_hidden()
    v.keyboard.press("j")
    v.wait_for_function("document.querySelector('#pageIn').value === '2'")
    v.keyboard.press("]")  # next fragment: back to page 1
    v.wait_for_function("document.querySelector('#pageIn').value === '1'")
    expect(v.locator("#fragItems .frag.active")).to_have_count(1)
    assert not errors, errors


def test_region_tags_focus_and_escape(ctx, server):
    """A region's name tag can be clicked to focus that region; the focus bar names it; Esc shows all."""
    clear_vault(server)
    for i in range(6):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Langmuir probe sheath electron temperature {i}",
                                           "abstract": "Langmuir probe sheath electron temperature plasma density measurement."})
    for i in range(6):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Transformer decoder pretraining {i}",
                                           "abstract": "Transformer decoder language model pretraining tokens attention."})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 12 && (window.__rb.S.regionLabels || []).some(r => r.box)", timeout=20000)
    page.wait_for_timeout(3000)  # let the first zoom-to-fit finish so the tag doesn't move under the click
    pt = page.evaluate("""() => { const {S, fg} = window.__rb; const r = S.regionLabels.find(r => r.box);
        const c = fg.graph2ScreenCoords((r.box[0] + r.box[2]) / 2, (r.box[1] + r.box[3]) / 2);
        const g = document.querySelector('#graph').getBoundingClientRect(); return {x: c.x + g.left, y: c.y + g.top, cid: r.cid}; }""")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_function(f"window.__rb.S.focusCluster === {pt['cid']}")
    expect(page.locator("#region-focus")).to_be_visible()
    blur(page)
    page.keyboard.press("Escape")
    page.wait_for_function("window.__rb.S.focusCluster == null")
    expect(page.locator("#region-focus")).to_be_hidden()
    page.click("#btn-regions")
    expect(page.locator(".rf-chooser .rf-item").first).to_be_visible()
    page.locator(".rf-chooser .rf-item").first.click()
    page.wait_for_function("window.__rb.S.focusCluster != null")
    assert page.evaluate("window.RB.commands.conflicts()") == []
    assert not page.errors

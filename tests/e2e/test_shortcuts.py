"""Keyboard shortcuts, the command palette, the shortcuts sheet and the first-run tour, plus the PDF viewer's
keys and side panel, map regions, bulk import and the chat (Playwright).

Uses the server/browser fixtures from conftest.py and the helpers from test_e2e.py. Skipped when Playwright
isn't available. Set RB_SHOT_DIR=/some/dir to save screenshots of the sheet, the palette and a coach mark.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import (  # noqa: E402
    MOD, _pdf_bytes, api, blur, clear_vault, open_app, shot,
)


def import_pdf_with_shortcut(page, pdf):
    """Mod+O opens the PDF picker. Headless Chromium only sometimes shows a native file chooser for a
    keyboard-triggered input.click() (it depends on user activation across pages), so check that the shortcut
    opened the picker, then hand the file to the same <input> the picker fills."""
    page.evaluate("""() => { const i = document.querySelector('#pdf-input'); window.__pickerOpened = 0;
        i.addEventListener('click', (e) => { window.__pickerOpened++; e.preventDefault(); }, { capture: true }); }""")
    page.keyboard.press(f"{MOD}+o")  # Import a PDF
    page.wait_for_function("window.__pickerOpened === 1")
    page.locator("#pdf-input").set_input_files(str(pdf))


def sheet_rows(page):
    return page.locator(".rbk-shortcuts .rbk-row")


def test_fresh_library_empty_state_registry_chord_and_palette(ctx, server):
    """An empty library: the empty state (with self-hosted fonts and a hint about ?), tooltips naming keys,
    every command registered once with no key conflicts, every math delimiter typeset (and a half-written
    equation held back while streaming), the G N chord and the palette's New note, recent actions first in
    the palette, and Add paper by id opening the Add menu's lookup."""
    clear_vault(server)
    page = open_app(ctx, server)
    expect(page.locator("#empty-state")).to_be_visible()
    expect(page.locator("#empty-state h1")).to_have_text("Start your library")
    expect(page.locator("#empty-state")).to_contain_text("Drop a PDF here, or add a paper by DOI, arXiv id or title.")
    expect(page.locator("#status .status-text")).to_contain_text("Claude")
    # real, self-hosted fonts are in use
    assert page.evaluate("document.fonts.check('500 13px \"Archivo\"')")
    assert page.evaluate("document.fonts.check('italic 500 20px \"Newsreader\"')")
    expect(page.locator("#empty-state .empty-hint")).to_contain_text("Press ? for shortcuts")
    assert "/" in page.locator("#search-input").get_attribute("title")
    assert "A" in page.locator("#btn-chat").get_attribute("title")

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
    # each command has a title and a group
    bad = page.evaluate("RB.commands.list().filter((c) => !c.title || !c.group).map((c) => c.id)")
    assert bad == []

    # $…$, $$…$$, \(…\) and \[…\] all become KaTeX; while an answer streams, a half-written equation in any of
    # them is held back instead of flashing raw TeX
    out = page.evaluate(r"""() => {
      const rb = window.__rb, n = (h) => (h.match(/class="katex"/g) || []).length;
      const full = 'Inline $a^2$, \\(b_1\\), and display $$\\frac{1}{2}$$ then \\[\\nabla\\cdot E = \\rho\\]. `$code$`';
      return {
        full: n(rb.renderMD(full)), code: rb.renderMD(full).includes('<code>$code$</code>'),
        openParen: rb.partialMD('So \\(\\frac{a}{'), openBracket: rb.partialMD('Then \\[x = \\sum_'),
        dollarInParen: rb.partialMD('So \\(a$b\\) done'), openDollar: rb.partialMD('Price $x + \\alpha'),
      };
    }""")
    assert out["full"] == 4 and out["code"]
    for k in ("openParen", "openBracket", "openDollar"):
        assert "stream-tail" in out[k] and "katex" not in out[k], k
    assert "katex" in out["dollarInParen"] and "stream-tail" not in out["dollarInParen"]

    # the G N chord: a new note
    blur(page)
    page.keyboard.press("g")
    expect(page.locator(".rbk-chord")).to_be_visible()
    page.keyboard.press("n")
    expect(page.locator(".rbk-chord")).to_have_count(0)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.title === 'Untitled note'")
    first_note = page.evaluate("window.__rb.S.cur.id")
    page.keyboard.press("Escape")  # leave the title

    # the palette runs New note and shows its key
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
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.title === 'Untitled note' && window.__rb.S.cur.id !== id",
                           arg=first_note)
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


def test_escape_order_back_forward_shortcuts_sheet_tour_and_the_viewers_sheet(ctx, server):
    """Esc closes the innermost thing first (sheet, menu, chat, panel); Mod+[ and Mod+] go back and forward
    between notes; ? opens the shortcuts sheet, whose search finds commands and chords and whose Esc clears,
    then closes; the View menu opens it too; the palette starts the tour and Help opens the guide; the PDF
    viewer uses the same sheet, with its own keys first."""
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "note", "title": "First note"})
    b = api(server, "POST", "/api/notes", {"type": "note", "title": "Second note"})
    no_pdf = api(server, "POST", "/api/notes", {"type": "paper", "title": "No PDF paper"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")  # the graph loads after the app is ready

    # Esc closes the innermost first
    page.evaluate("(id) => window.__rb.openNote(id)", a["id"])
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

    # back and forward
    page.evaluate("(id) => window.__rb.openNote(id)", a["id"])
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=a["id"])
    page.evaluate("(id) => window.__rb.openNote(id)", b["id"])
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=b["id"])
    blur(page)
    page.keyboard.press(f"{MOD}+BracketLeft")
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=a["id"])
    page.keyboard.press(f"{MOD}+BracketRight")
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=b["id"])
    page.evaluate("window.__rb.closePanel()")

    # ? opens the sheet; its search
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
    page.keyboard.press("Escape")
    expect(page.locator(".rbk-shortcuts")).to_have_count(0)

    # the palette starts the tour
    page.keyboard.press(f"{MOD}+k")
    page.keyboard.type("quick tour")
    expect(page.locator(".rbk-item.on")).to_have_attribute("data-cmd", "help.tour")
    page.keyboard.press("Enter")
    coach = page.locator(".rb-coach")
    expect(coach).to_be_visible()
    expect(coach.locator(".c-step")).to_have_text("1 of 6")
    shot(page, "coach-mark")
    coach.locator("button", has_text="Next").click()
    expect(coach.locator(".c-step")).to_have_text("2 of 6")
    page.keyboard.press("Escape")
    expect(coach).to_have_count(0)
    assert page.evaluate("localStorage.getItem('rb.tour.done')") == "true"
    page.evaluate("RB.commands.run('help.guide')")
    expect(page.locator(".rbk-guide")).to_contain_text("Fragments")
    shot(page, "help-guide")
    page.keyboard.press("Escape")
    expect(page.locator(".rbk-guide")).to_have_count(0)
    assert not page.errors

    # the viewer uses the same sheet
    v = ctx.new_page()
    errors = []
    v.on("pageerror", lambda e: errors.append(str(e)))
    v.goto(server["base"] + f"/viewer/{no_pdf['id']}")
    v.wait_for_function("window.RB && RB.commands && RB.commands.get('viewer.nextPage').passive === false")
    v.keyboard.press("?")
    vsheet = v.locator(".rbk-shortcuts")
    expect(vsheet).to_be_visible()
    expect(vsheet.locator(".rbk-group").first).to_have_attribute("data-group", "Viewer")
    assert v.evaluate("RB.commands.conflicts()") == []
    v.keyboard.press("Escape")
    expect(vsheet).to_have_count(0)
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


def test_bulk_import_a_folder_and_bookmarks(ctx, server, tmp_path):
    """A folder of PDFs (subfolders become topics) and a bookmarks export (folders become topics)."""
    clear_vault(server)
    root = tmp_path / "My papers"
    (root / "Sheaths").mkdir(parents=True)
    (root / "Thrusters").mkdir()
    one = _pdf_bytes()
    (root / "Sheaths" / "a.pdf").write_bytes(one)
    (root / "Thrusters" / "b.pdf").write_bytes(_pdf_bytes().replace(b"Sheath physics", b"Thruster physics"))
    (root / "Thrusters" / "same.pdf").write_bytes(one)  # the same file twice: stored once
    (root / "readme.txt").write_text("not a pdf")
    page = open_app(ctx, server)
    page.set_input_files("#bulk-folder-input", str(root))
    dlg = page.locator(".bk-card")
    expect(dlg).to_contain_text("3 PDFs in 2 subfolders")
    dlg.locator("button.btn-primary").click()
    expect(dlg.locator(".bk-status")).to_contain_text("added", timeout=60_000)
    assert "1 already in your library" in dlg.locator(".bk-status").inner_text()
    topics = page.evaluate("fetch('/api/topics').then(r => r.json())")
    names = sorted(t["name"] for t in (topics if isinstance(topics, list) else topics.get("topics", [])))
    assert names == ["Sheaths", "Thrusters"]
    dlg.locator("button", has_text="Done").click()
    # bookmarks: parsed in the page; paper-looking links are ticked, others not
    bm = tmp_path / "bookmarks.html"
    bm.write_text("""<!DOCTYPE NETSCAPE-Bookmark-file-1><DL><p>
      <DT><H3>Bookmarks bar</H3><DL><p>
        <DT><H3>Plasma reading</H3><DL><p>
          <DT><A HREF="https://arxiv.org/abs/2101.00001">An arXiv paper</A>
          <DT><A HREF="https://news.example.com/story">A news story</A>
        </DL><p>
      </DL><p></DL>""", encoding="utf-8")
    page.set_input_files("#bulk-bookmarks-input", str(bm))
    expect(dlg).to_contain_text("2 bookmarks in 1 folder. 1 look like papers")
    expect(dlg.locator(".bk-folder")).to_contain_text("Plasma reading")
    boxes = dlg.locator(".bk-item input")
    assert boxes.nth(0).is_checked() and not boxes.nth(1).is_checked()


def test_viewer_side_panel_keys_ask_tab_focus_directions_and_writing_help(ctx, server, tmp_path):
    """One PDF imported with Mod+O, then in the viewer:
    - the side panel's edge resizes Notes / Ask, the width is kept, arrows and double-click work;
    - keys: N with nothing selected is only a hint, N on a selection opens the note popover, viewer keys don't
      fire while typing, Mod+Enter saves, J is the next page and ] the next fragment;
    - the Ask tab: select a passage, press A, ask; the request carries the paper, page and passage; a page
      citation in the answer jumps to that page; the graph's "Ask about this paper" continues the same thread;
    - Focus (local) marks what matters in the PDF, and the paper panel offers the annotated copy;
    - topic directions and writing help preview what would be sent: nothing goes until you press Send, and the
      private draft can't be ticked while the linked paper can."""
    clear_vault(server)
    pdf = tmp_path / "sheath.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    import_pdf_with_shortcut(page, pdf)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.has_pdf", timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")

    v = ctx.new_page()
    errors, sent = [], []
    v.on("pageerror", lambda e: errors.append(str(e)))

    def fake_stream(route):
        sent.append(json.loads(route.request.post_data))
        answer = f"It means ions must reach the sound speed [[{nid}]] p. 2."
        body = (f"event: delta\ndata: {json.dumps({'text': answer})}\n\n"
                f"event: done\ndata: {json.dumps({'answer': answer, 'sources': [{'id': nid, 'title': 'Sheath', 'score': 1}]})}\n\n")
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)
    v.route("**/api/chat/stream", fake_stream)
    v.goto(f"{server['base']}/viewer/{nid}")

    # the side panel resizes and remembers
    expect(v.locator(".page[data-page='1']")).to_be_visible(timeout=30_000)
    side = v.locator("#side")
    w0 = side.bounding_box()["width"]
    g = v.locator("#sideGrip").bounding_box()
    x, y = g["x"] + g["width"] / 2, g["y"] + 300
    v.mouse.move(x, y)
    v.mouse.down()
    v.mouse.move(x - 220, y, steps=10)
    v.mouse.up()
    w1 = side.bounding_box()["width"]
    assert abs(w1 - (w0 + 220)) < 4
    v.reload()
    expect(v.locator(".page[data-page='1']")).to_be_visible(timeout=30_000)
    assert abs(side.bounding_box()["width"] - w1) < 2  # remembered
    v.locator("#sideGrip").focus()
    v.keyboard.press("ArrowRight")  # narrower
    assert side.bounding_box()["width"] < w1 - 10
    v.locator("#sideGrip").dblclick()
    assert abs(side.bounding_box()["width"] - w0) < 2  # back to the default

    # the Ask tab about a selection (on a fresh load: resizing the side panel lays the pages out again)
    v.goto(f"{server['base']}/viewer/{nid}")
    span = v.locator(".page[data-page='1'] .textLayer span", has_text="Bohm criterion")
    expect(span).to_be_visible(timeout=30_000)

    def select_span():
        box = span.bounding_box()
        v.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
        v.mouse.down()
        v.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
        v.mouse.up()
    select_span()
    expect(v.locator("#askSelBtn")).to_be_visible()
    v.keyboard.press("a")  # Ask about the selection
    expect(v.locator("#askPane")).to_be_visible()
    expect(v.locator("#askCtx .sel")).to_contain_text("Bohm criterion")
    expect(v.locator("#askInput")).to_be_focused()
    v.keyboard.type("What does this mean?")
    v.keyboard.press("Enter")
    expect(v.locator("#askLog .am.ai .txt")).to_contain_text("sound speed")
    assert sent and sent[0]["paper"] == nid and sent[0]["page"] == 1 and "Bohm criterion" in sent[0]["selection"]
    expect(v.locator("#askCtx")).to_contain_text("Reading page 1")  # the selection was used once, then cleared
    v.locator("#askLog .cite[data-page='2']").click()
    v.wait_for_function("document.querySelector('#pageIn').value === '2'")
    page.locator('[data-act="ask-paper"]').click()
    page.wait_for_function("window.__rb.S.chatPaper && window.__rb.S.chat.length === 2")
    expect(page.locator("#chat-log .msg-sel")).to_contain_text("Bohm criterion")
    v.evaluate("localStorage.removeItem('rb.viewer.tab')")  # the viewer remembers its tab: back to Notes
    v.goto(f"{server['base']}/viewer/{nid}")  # afresh: page 1, nothing selected
    # viewer keys
    expect(span).to_be_visible(timeout=30_000)
    v.wait_for_function("document.querySelector('#pageIn').value === '1'")
    blur(v)
    v.keyboard.press("n")  # nothing selected: a hint, no popover
    expect(v.locator("#pop")).to_be_hidden()
    select_span()
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
    v.close()

    # the paper panel offers the annotated copy once there's a highlight
    page.evaluate("""(nid) => fetch('/api/notes', {method: 'POST', headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({type: 'fragment', parent: nid, page: 1, quote: 'Bohm criterion'})})""", nid)
    page.evaluate("(nid) => window.__rb.openNote(nid)", nid)
    expect(page.locator("[data-act=pdf-annotated]")).to_be_visible()
    # Focus in the viewer (opened on the Notes tab, as at first)
    page.evaluate("localStorage.removeItem('rb.viewer.tab')")
    v = ctx.new_page()
    v.goto(f"{server['base']}/viewer/{nid}")
    expect(v.locator(".page[data-page='1'] .textLayer span").first).to_be_visible(timeout=30_000)
    v.keyboard.press("f")
    expect(v.locator("#focusPane")).to_be_visible()
    v.locator("#focusText").fill("Bohm criterion sheath edge")
    v.locator("#focusGo").click()
    expect(v.locator(".fo-item").first).to_be_visible(timeout=20_000)
    expect(v.locator(".textLayer span.rb-focus").first).to_be_attached(timeout=20_000)
    v.locator("#focusClear").click()
    assert v.locator(".textLayer span.rb-focus").count() == 0
    v.close()
    # topic directions: the preview lists what goes, nothing is sent until you press Send
    api(server, "PUT", f"/api/notes/{nid}", {"summary": "Sheath physics and the Bohm criterion."})
    t = api(server, "POST", "/api/topics", {"name": "Sheaths for directions"})
    api(server, "POST", f"/api/topics/{t['slug']}/notes", {"ids": [nid]})
    page.evaluate("(slug) => window.__rbDirections.open('topic', slug, 'Sheaths')", t["slug"])
    dlg = page.locator(".dr-card")
    expect(dlg).to_contain_text("Goes to the AI (1)")
    expect(dlg).to_contain_text("The region’s name and description")
    expect(dlg.locator(".dr-size")).to_contain_text("tokens")
    page.keyboard.press("Escape")
    expect(dlg).to_be_hidden()
    # writing help on a note: the private draft can't be ticked; the linked paper can
    n = api(server, "POST", "/api/notes", {"type": "note", "title": "Draft", "body": f"See [[{nid}]]."})
    page.evaluate(f"window.__rb.openNote({json.dumps(n['id'])})")
    sec = page.locator("#wr-sec")
    expect(sec.locator(".wr-src label").first).to_contain_text("Sheath")
    expect(sec).to_contain_text("Your draft is private")


def test_bulk_import_a_flat_folder_lets_the_ai_sort_it(ctx, server, tmp_path):
    """A folder without subfolders: the AI option is the default, and the new papers end up in topics."""
    clear_vault(server)
    root = tmp_path / "Downloads pile"
    root.mkdir()
    (root / "a.pdf").write_bytes(_pdf_bytes())
    (root / "b.pdf").write_bytes(_pdf_bytes().replace(b"Sheath physics", b"Plasma physics"))  # same length: still a valid PDF
    page = open_app(ctx, server)
    page.set_input_files("#bulk-folder-input", str(root))
    dlg = page.locator(".bk-card")
    expect(dlg).to_contain_text("2 PDFs")
    expect(dlg.locator("input[value=ai]")).to_be_checked()
    dlg.locator("button.btn-primary").click()
    expect(dlg.locator(".bk-status")).to_contain_text("added", timeout=60_000)
    expect(dlg.locator(".bk-status")).to_contain_text("Sorted into", timeout=60_000)  # fake AI: by similarity
    # every paper is now in a topic: nothing is left to sort
    left = page.evaluate("fetch('/api/topics/auto-sort', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({unsorted: true})}).then(r => r.json())")
    assert left["sorted"] == 0, left
    assert not page.errors


def test_click_off_the_chat_closes_it(ctx, server):
    """Clicks inside the chat and on the top bar keep it open, and so does dragging the map; a click on the
    map closes it."""
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Click off"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 1")
    page.click("#btn-chat")
    expect(page.locator("#chat")).to_be_visible()
    page.click("#chat-input")                      # inside: stays
    expect(page.locator("#chat")).to_be_visible()
    page.click("#btn-view-menu")                   # the top bar works alongside it
    expect(page.locator("#chat")).to_be_visible()
    page.keyboard.press("Escape")
    box = page.locator("#graph").bounding_box()
    x, y = box["x"] + 40, box["y"] + box["height"] - 120
    page.mouse.move(x, y); page.mouse.down(); page.mouse.move(x + 80, y - 40, steps=5); page.mouse.up()
    expect(page.locator("#chat")).to_be_visible()  # a drag (panning the map) isn't a click off
    page.mouse.click(x, y)                         # a click on the map closes it
    expect(page.locator("#chat")).to_be_hidden()
    assert not page.errors


def test_tutorials_start_with_your_own_papers_and_follow_what_you_do(ctx, server):
    """The welcome card offers the basics or the full tour. Both start with the user's own papers: the first step
    offers ways to bring them in and waits (Next reads Skip) until a paper arrives. The full tour then follows what
    the user does on that library: opening a paper, focusing a region, opening its subsections. Esc in the panel
    or a focused region closes that, not the tour. A step that can't be done yet says why. Every card fits on
    screen. The Settings menu, Library settings › Tutorials and the palette start the tours again."""
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "A note"})
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.tour.done'); localStorage.removeItem('rb.tour.deepDone')")

    # first run: the chooser (started by hand here, since it's skipped under automation)
    page.evaluate("window.__rb.tour.choose({ first: true })")
    card = page.locator(".rb-choose")
    expect(card).to_contain_text("Welcome to research brain")
    expect(card).to_contain_text("your own papers")
    expect(card.locator(".c-opt")).to_have_count(2)
    shot(page, "tour-chooser")
    card.locator("button", has_text="Not now").click()
    expect(card).to_have_count(0)
    assert page.evaluate("localStorage.getItem('rb.tour.done')") == "true"

    # Settings menu › Tutorials › Get me up to speed
    page.click("#btn-view-menu")
    page.click("#btn-tutorials")
    expect(page.locator("#view-menu")).to_be_hidden()
    page.locator(".rb-choose .c-opt[data-tour='deep']").click()
    coach = page.locator(".rb-coach")
    expect(coach).to_have_count(1)
    total = int(coach.locator(".c-step").inner_text().split(" of ")[1])
    assert total >= 15

    def nxt(label="Next"):
        coach.locator(".c-act button", has_text=label).click()

    def go_to(title):
        for _ in range(total):
            if coach.locator(".c-title").inner_text() == title:
                return
            nxt("Skip" if coach.get_attribute("data-task") in ("waiting", "blocked") else "Next")
        raise AssertionError(f"no step {title!r}")

    def fits():
        box, vp = coach.bounding_box(), page.viewport_size
        assert box["x"] >= 0 and box["y"] >= 0 and box["x"] + box["width"] <= vp["width"] and box["y"] + box["height"] <= vp["height"]

    # 1. bring in your papers: waits, with ways to do it, until a paper arrives
    expect(coach.locator(".c-title")).to_have_text("Bring in your papers")
    expect(coach).to_have_attribute("data-task", "waiting")
    expect(coach.locator(".c-act button", has_text="Skip")).to_be_visible()
    for label in ("Import PDFs", "Add folders of papers", "Find papers online"):
        expect(coach.locator(".c-acts button", has_text=label)).to_be_visible()
    shot(page, "tour-deep-add-papers")
    ps = [api(server, "POST", "/api/notes", {"type": "paper", "title": t, "abstract": a})["id"] for t, a in (
        ("Attention is all you need", "Transformers for translation."), ("Deep residual learning", "Residual networks for images."))]
    api(server, "POST", "/api/topics", {"name": "Deep learning", "members": ps})  # as a linked folder would
    page.evaluate("window.__rb.refreshGraph()")
    expect(coach).to_have_attribute("data-task", "done")
    expect(coach.locator(".c-status")).to_contain_text("You have 2 papers")
    expect(coach.locator(".c-acts")).to_have_count(0)
    fits()
    nxt()

    # open a paper: "Open one for me", then Esc closes the panel and leaves the tour
    go_to("Open a paper")
    expect(coach).to_have_attribute("data-task", "waiting")
    coach.locator(".c-acts button", has_text="Open one for me").click()
    expect(coach).to_have_attribute("data-task", "done")
    expect(page.locator("#panel")).to_be_visible()
    fits()
    page.locator("#panel .ptitle, #panel h1, #panel").first.click(position={"x": 5, "y": 5})
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Escape")
    expect(page.locator("#panel")).to_be_hidden()
    expect(coach).to_have_count(1)
    expect(coach).to_have_attribute("data-task", "waiting")  # it follows what's on screen

    # focus a region, then its subsections
    go_to("Focus a region")
    coach.locator(".c-acts button", has_text="Focus the biggest for me").click()
    expect(coach).to_have_attribute("data-task", "done")
    assert page.evaluate("window.__rb.S.focusCluster") is not None
    fits()
    nxt()
    expect(coach.locator(".c-title")).to_have_text("Subsections")
    expect(coach).to_have_attribute("data-task", "waiting")
    coach.locator(".c-acts button", has_text="Open Subsections").click()
    expect(page.locator(".rf-chooser .rf-head")).to_contain_text("Subsections of")
    expect(coach).to_have_attribute("data-task", "done")
    shot(page, "tour-deep-subsections")
    page.keyboard.press("Escape")  # the sheet
    expect(page.locator(".rf-chooser")).to_have_count(0)
    page.keyboard.press("Escape")  # the focused region
    expect(page.locator("#region-focus")).to_be_hidden()
    expect(coach).to_have_count(1)

    # Ask without an AI agent says why it can't be done yet
    go_to("Ask your library")
    if not page.evaluate("window.__rb.S.claudeOK"):
        expect(coach).to_have_attribute("data-task", "blocked")
        expect(coach.locator(".c-status")).to_contain_text("AI agent")

    # to the end; Back works; the chapters are all there
    chapters = set()
    for i in range(int(coach.locator(".c-step").inner_text().split(" of ")[0]), total + 1):
        chapters.add(coach.locator(".c-ch").inner_text())
        fits()
        if i == total:
            nxt("Done")
        else:
            nxt("Skip" if coach.get_attribute("data-task") in ("waiting", "blocked") else "Next")
    expect(coach).to_have_count(0)
    assert {"Asking", "Writing", "Getting around"} <= chapters
    assert page.evaluate("localStorage.getItem('rb.tour.deepDone')") == "true"

    # Library settings › Tutorials starts the basics, which start with the papers too; Esc leaves it
    page.evaluate("window.__rbSettings.open('tutorials')")
    sec = page.locator("#ls-tutorials")
    expect(sec).to_be_visible()
    sec.locator("button", has_text="Show me the basics").click()
    expect(coach.locator(".c-step")).to_have_text("1 of 6")
    expect(coach.locator(".c-title")).to_have_text("Bring in your papers")
    expect(coach).to_have_attribute("data-task", "done")
    expect(coach.locator(".c-ch")).to_have_count(0)
    page.keyboard.press("Escape")
    expect(coach).to_have_count(0)

    # the palette
    page.keyboard.press(f"{MOD}+k")
    page.keyboard.type("up to speed")
    expect(page.locator(".rbk-item.on")).to_have_attribute("data-cmd", "help.deepTour")
    page.keyboard.press("Enter")
    expect(coach.locator(".c-ch")).to_have_text("Your papers")
    page.keyboard.press("Escape")
    expect(coach).to_have_count(0)
    assert page.errors == []

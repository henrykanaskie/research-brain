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
    import_pdf_with_shortcut(page, pdf)
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


def test_viewer_ask_tab_about_a_selection(ctx, server, tmp_path):
    """Beside the PDF: select a passage, press A, ask; the request carries the paper, page and passage; a page
    citation in the answer jumps to that page; the graph's "Ask about this paper" continues the same thread."""
    import json as _json
    from test_e2e import _pdf_bytes
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
        sent.append(_json.loads(route.request.post_data))
        answer = f"It means ions must reach the sound speed [[{nid}]] p. 2."
        body = (f"event: delta\ndata: {_json.dumps({'text': answer})}\n\n"
                f"event: done\ndata: {_json.dumps({'answer': answer, 'sources': [{'id': nid, 'title': 'Sheath', 'score': 1}]})}\n\n")
        route.fulfill(status=200, headers={"Content-Type": "text/event-stream"}, body=body)
    v.route("**/api/chat/stream", fake_stream)
    v.goto(f"{server['base']}/viewer/{nid}")
    span = v.locator(".page[data-page='1'] .textLayer span", has_text="Bohm criterion")
    expect(span).to_be_visible(timeout=30_000)
    box = span.bounding_box()
    v.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    v.mouse.down()
    v.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
    v.mouse.up()
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
    assert not errors, errors


def test_viewer_side_panel_resizes_and_remembers(ctx, server, tmp_path):
    """Drag the side panel's edge to resize Notes / Ask; the width is kept; arrows and double-click work."""
    from test_e2e import _pdf_bytes
    clear_vault(server)
    pdf = tmp_path / "sheath.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    import_pdf_with_shortcut(page, pdf)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.has_pdf", timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")
    v = ctx.new_page()
    errors = []
    v.on("pageerror", lambda e: errors.append(str(e)))
    v.goto(f"{server['base']}/viewer/{nid}")
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
    assert not errors, errors


def test_every_math_delimiter_is_typeset_and_streaming_holds_back_open_math(ctx, server):
    """$…$, $$…$$, \\(…\\) and \\[…\\] all become KaTeX; while an answer streams, a half-written equation
    in any of them is held back instead of flashing raw TeX."""
    page = open_app(ctx, server)
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


def test_citation_opens_the_pdf_with_its_sentence_highlighted(ctx, server, tmp_path):
    """A "[[paper]] p. 1" citation in an answer is pointed at the sentence that backs the claim; the viewer
    highlights it for a moment. "Find related papers" results are scrolled to."""
    from test_e2e import _pdf_bytes
    clear_vault(server)
    pdf = tmp_path / "sheath.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    import_pdf_with_shortcut(page, pdf)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.has_pdf", timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")
    page.evaluate("""(nid) => { const d = document.createElement('div'); d.className = 'md'; d.id = 'cite-test';
      d.innerHTML = window.__rb.renderMD(`Ions must reach the sound speed at the sheath edge [[${nid}]] p. 1.`);
      document.body.appendChild(d); }""", nid)
    a = page.locator("#cite-test a.cite")
    expect(a).to_have_attribute("data-pinned", "1", timeout=10_000)
    href = a.get_attribute("href")
    assert "&hl=" in href and "Bohm" in __import__("urllib.parse").parse.unquote(href)
    v = ctx.new_page()
    v.goto(server["base"] + href)
    expect(v.locator(".textLayer span.rb-cite").first).to_be_visible(timeout=30_000)
    assert "Bohm criterion" in " ".join(v.locator(".textLayer span.rb-cite").all_inner_texts())
    assert "hl=" not in v.evaluate("location.hash")  # the highlight isn't kept in the address


def test_rough_citation_marks_a_stretch_with_a_margin_bar(ctx, server, tmp_path):
    from test_e2e import _pdf_bytes
    clear_vault(server)
    pdf = tmp_path / "sheath2.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    import_pdf_with_shortcut(page, pdf)
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.has_pdf", timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")
    quote = page.evaluate("(nid) => fetch('/api/locate', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                          "body: JSON.stringify({items: [{id: nid, page: 1, claim: 'sheath'}]})}).then(r => r.json())", nid)
    q = quote["items"][0]["quote"] or "Bohm criterion"
    v = ctx.new_page()
    v.goto(f"{server['base']}/viewer/{nid}#page=1&hl={__import__('urllib.parse').parse.quote(q)}&near=1")
    expect(v.locator(".rb-cite-bar.near")).to_be_visible(timeout=30_000)
    assert v.locator(".textLayer span.rb-cite-near").count() > 0 and v.locator(".textLayer span.rb-cite").count() == 0


def test_bulk_import_a_folder_and_bookmarks(ctx, server, tmp_path):
    """A folder of PDFs (subfolders become topics) and a bookmarks export (folders become topics)."""
    from test_e2e import _pdf_bytes
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

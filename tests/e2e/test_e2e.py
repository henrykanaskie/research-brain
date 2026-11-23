"""End-to-end browser tests: a real server (fake Claude, TF-IDF embeddings, temp vault) driven by
Playwright + Chromium. The server, browser and per-test context fixtures are in conftest.py; the helpers
here (api, clear_vault, open_app, ...) are shared with test_shortcuts.py. All tests in this file share one
server: a test that needs an empty library calls clear_vault first. Set RB_SHOT_DIR=/some/dir to save
screenshots.

Run:   python -m pytest -q tests/e2e
Needs: pip install playwright reportlab && python -m playwright install chromium
       (or a system Chrome at /usr/bin/google-chrome, or RB_E2E_CHROME=/path/to/chrome)
Skipped, with the reason, when Playwright or a browser isn't available. Set RB_E2E_HEADED=1 to watch.

The app is fully vendored (static/vendor/), so every request that isn't to the test server is
blocked: a test that needs the internet fails instead of silently passing. Semantic Scholar lookups
made by the server are sent to a dead proxy, so they fail fast and the import falls back offline.
"""
from __future__ import annotations

import io
import json
import os
import re
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import note_file

ROOT = Path(__file__).resolve().parents[2]

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed (pip install playwright && python -m playwright install chromium)",
                allow_module_level=True)

try:
    import reportlab  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("reportlab is not installed (needed to build a test PDF)", allow_module_level=True)

TIMEOUT = 15_000  # ms, as in conftest.py
MOD = "Meta" if sys.platform == "darwin" else "Control"


def _pdf_bytes() -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle("Sheath physics zqx test document")
    c.setFont("Helvetica", 16)
    c.drawString(72, 700, "Sheath physics zqx test document")
    c.setFont("Helvetica", 13)
    c.drawString(72, 660, "The Bohm criterion requires ions to enter the sheath at the sound speed.")
    c.drawString(72, 640, "Debye length sets the sheath thickness in a collisionless plasma.")
    c.showPage()
    c.drawString(72, 700, "Second page.")
    c.showPage()
    c.save()
    return buf.getvalue()


def shot(page, name, **kw):
    d = os.environ.get("RB_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), **kw)


def blur(page):
    page.evaluate("document.activeElement && document.activeElement.blur()")


def api(server, method, path, body=None):
    req = urllib.request.Request(server["base"] + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def clear_vault(server):
    for n in api(server, "GET", "/api/search?q=&limit=100"):
        api(server, "DELETE", f"/api/notes/{n['id']}")


def open_app(ctx, server):
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.fg !== undefined")
    expect(page.locator("#status .status-text")).not_to_have_text("Checking")
    return page


def wait_saved(page, server, nid, field, expected):
    deadline = time.time() + TIMEOUT / 1000
    while time.time() < deadline:
        if expected in (api(server, "GET", f"/api/notes/{nid}").get(field) or ""):
            return
        page.wait_for_timeout(150)
    raise AssertionError(f"{field} of {nid} never saved {expected!r}")


def new_note(page, title):
    """Add > Write a note, then type a title. Returns the new note's id."""
    page.click("#btn-add-menu")
    page.click("#btn-new-note")
    t = page.locator("#panel-body .title-input")
    expect(t).to_be_focused()
    t.fill(title)
    page.wait_for_function("(t) => window.__rb.S.cur && window.__rb.S.cur.title === t", arg=title)
    return page.evaluate("window.__rb.S.cur.id")


def body_editor(page):
    ta = page.locator("#panel-body textarea.body-field")
    if not ta.is_visible():
        page.locator("#panel-body .seg button", has_text="Edit").click()
    return ta


# ------------------------------------------------------------------------------------------- tests
def test_writing_a_note_autosaves_ignores_shortcuts_and_autocompletes_latex(ctx, server):
    """A new note's body is saved as you type (to the API and the vault file, once) and the note joins the
    graph; letters that are shortcuts elsewhere just type; the LaTeX autocomplete owns Tab, the live preview
    renders KaTeX, Esc closes the popup before the panel, and Mod+E (allowed while typing) shows the preview."""
    clear_vault(server)
    page = open_app(ctx, server)
    nid = new_note(page, "Sheath notes")
    ta = body_editor(page)
    ta.click()
    ta.type("Ions reach the Bohm speed.")
    expect(page.locator("#save-indicator")).to_have_text("Saved")
    wait_saved(page, server, nid, "body", "Ions reach the Bohm speed.")
    assert api(server, "GET", f"/api/notes/{nid}")["title"] == "Sheath notes"
    assert note_file(server["vault"], nid).read_text(encoding="utf-8").count("Bohm speed") == 1
    # the empty state is gone and the node is on the graph
    expect(page.locator("#empty-state")).to_be_hidden()
    page.wait_for_function("(id) => window.__rb.S.nodes.has(id)", arg=nid)
    # no shortcuts while typing
    n_before = page.evaluate("window.__rb.S.nodes.size")
    ta.type("?nasgf/ G then T")  # letters that are shortcuts outside a text field
    expect(page.locator(".rbk-ov:not([hidden])")).to_have_count(0)
    expect(page.locator("#chat")).to_be_hidden()
    assert ta.input_value() == "Ions reach the Bohm speed.?nasgf/ G then T"
    assert page.evaluate("window.__rb.S.nodes.size") == n_before
    # the LaTeX autocomplete still owns Tab
    ta.type(" Angle $\\alph")
    pop = page.locator(".le-pop")
    expect(pop).to_be_visible()
    expect(pop.locator("li.on")).to_contain_text("alpha")
    page.keyboard.press("Tab")
    expect(pop).to_be_hidden()
    value = ta.input_value()
    assert value.endswith("$\\alpha"), value
    page.keyboard.type("$")
    # the live preview renders KaTeX
    expect(page.locator("#panel-body .md.live .katex").first).to_be_visible()
    wait_saved(page, server, nid, "body", "\\alpha")
    # and Esc closes its popup before anything else
    ta.type(" $\\bet")
    expect(pop).to_be_visible()
    page.keyboard.press("Escape")
    expect(pop).to_be_hidden()
    expect(page.locator("#panel")).to_be_visible()
    # Mod+E (allowed while typing) switches to the preview
    page.keyboard.press(f"{MOD}+e")
    expect(page.locator("#panel-body .preview")).to_be_visible()
    assert not page.errors


def test_wikilink_autocomplete_and_backlink(ctx, server):
    clear_vault(server)
    target = api(server, "POST", "/api/notes", {"type": "note", "title": "Debye shielding"})
    page = open_app(ctx, server)
    page.wait_for_function("(id) => window.__rb.S.nodes.has(id)", arg=target["id"])
    src = new_note(page, "Plasma basics")
    ta = body_editor(page)
    ta.click()
    ta.type("See [[Deb")
    pop = page.locator(".wl-pop")
    expect(pop).to_be_visible()
    expect(pop.locator("li.on")).to_contain_text("Debye shielding")
    page.keyboard.press("Tab")
    assert "[[Debye shielding]]" in ta.input_value()
    wait_saved(page, server, src, "body", "[[Debye shielding]]")
    # the target now lists the source under "Mentioned in"
    page.evaluate("(id) => window.__rb.openNote(id)", target["id"])
    bl = page.locator("#panel-body .backlinks")
    expect(bl).to_contain_text("Plasma basics")
    expect(bl.locator(".r-title")).to_have_text(["Plasma basics"])
    assert api(server, "GET", f"/api/notes/{target['id']}")["backlinks"][0]["id"] == src


def test_pdf_import_viewer_passage_note_and_citations(ctx, server, tmp_path):
    """A PDF added from the Add menu is read and shown in the viewer, where a selected passage is saved as a
    note and highlighted. A "[[paper]] p. 1" citation in an answer is pointed at the sentence that backs the
    claim and the viewer highlights it for a moment; a rough citation marks its stretch with a margin bar."""
    clear_vault(server)
    pdf = tmp_path / "sheath.pdf"
    pdf.write_bytes(_pdf_bytes())
    page = open_app(ctx, server)
    page.click("#btn-add-menu")
    with page.expect_file_chooser() as fc:
        page.click("#btn-add-pdf")
    fc.value.set_files(str(pdf))
    title = page.locator("#panel-body .title-input")
    expect(title).to_have_value(re.compile("Sheath physics", re.I), timeout=30_000)
    nid = page.evaluate("window.__rb.S.cur.id")
    assert api(server, "GET", f"/api/notes/{nid}")["has_pdf"] is True

    viewer = ctx.new_page()
    viewer.goto(f"{server['base']}/viewer/{nid}")
    span = viewer.locator(".page[data-page='1'] .textLayer span", has_text="Bohm criterion")
    expect(span).to_be_visible(timeout=30_000)
    assert viewer.evaluate("[...document.querySelectorAll('.page canvas')].some(c => c.width > 100)")
    expect(viewer.locator("#pageTot")).to_have_text("of 2")

    # select a passage in the text layer, then "Add note" -> popover -> Save
    box = span.bounding_box()
    viewer.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    viewer.mouse.down()
    viewer.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
    viewer.mouse.up()
    expect(viewer.locator("#clipBtn")).to_be_visible()
    viewer.click("#clipBtn")
    expect(viewer.locator("#pop")).to_be_visible()
    assert "Bohm criterion" in viewer.locator("#pQuote").input_value()
    viewer.locator("#pBody").fill("Ions must be supersonic at the sheath edge.")
    viewer.click("#pSave")
    expect(viewer.locator("#fragItems .frag")).to_have_count(1)
    frags = api(server, "GET", f"/api/notes/{nid}")["fragments"]
    assert len(frags) == 1 and frags[0]["page"] == 1 and "Bohm criterion" in frags[0]["quote"]
    assert frags[0]["body"] == "Ions must be supersonic at the sheath edge."
    # the saved passage is highlighted on the page
    expect(viewer.locator(".textLayer [data-frag]").first).to_be_attached()

    # a citation in an answer opens the PDF with its sentence highlighted
    page.evaluate("""(nid) => { const d = document.createElement('div'); d.className = 'md'; d.id = 'cite-test';
      d.innerHTML = window.__rb.renderMD(`Ions must reach the sound speed at the sheath edge [[${nid}]] p. 1.`);
      document.body.appendChild(d); }""", nid)
    a = page.locator("#cite-test a.cite")
    expect(a).to_have_attribute("data-pinned", "1", timeout=10_000)
    href = a.get_attribute("href")
    assert "&hl=" in href and "Bohm" in urllib.parse.unquote(href)
    v = ctx.new_page()
    v.goto(server["base"] + href)
    expect(v.locator(".textLayer span.rb-cite").first).to_be_visible(timeout=30_000)
    assert "Bohm criterion" in " ".join(v.locator(".textLayer span.rb-cite").all_inner_texts())
    assert "hl=" not in v.evaluate("location.hash")  # the highlight isn't kept in the address

    # a rough citation marks a stretch with a margin bar
    quote = page.evaluate("(nid) => fetch('/api/locate', {method: 'POST', headers: {'Content-Type': 'application/json'}, "
                          "body: JSON.stringify({items: [{id: nid, page: 1, claim: 'sheath'}]})}).then(r => r.json())", nid)
    q = quote["items"][0]["quote"] or "Bohm criterion"
    v2 = ctx.new_page()
    v2.goto(f"{server['base']}/viewer/{nid}#page=1&hl={urllib.parse.quote(q)}&near=1")
    expect(v2.locator(".rb-cite-bar.near")).to_be_visible(timeout=30_000)
    assert v2.locator(".textLayer span.rb-cite-near").count() > 0 and v2.locator(".textLayer span.rb-cite").count() == 0


def test_critique_fake(ctx, server):
    clear_vault(server)
    p = api(server, "POST", "/api/notes", {"type": "paper", "title": "A paper on sheaths",
                                           "summary": "Sheaths form because electrons are faster than ions."})
    page = open_app(ctx, server)
    page.evaluate("(id) => window.__rb.openNote(id)", p["id"])
    btn = page.locator("#summary-sec .btn-primary", has_text="Critique my summary")
    expect(btn).to_be_visible()
    btn.click()
    crit = page.locator("#summary-sec .machine")
    expect(crit).to_contain_text("fake Claude")
    expect(crit).to_contain_text("Claude's critique")
    expect(crit.locator(".katex").first).to_be_visible()
    # the page streams the critique before the server finishes saving it, so poll for the saved copy
    import time
    deadline = time.time() + 10
    while not (api(server, "GET", f"/api/notes/{p['id']}").get("critique") or {}).get("text"):
        assert time.time() < deadline, "critique was shown but never saved"
        time.sleep(0.1)


def test_library_chat_quick_answer_phone_handoff_research_mode_and_memory(ctx, server):
    """Ask your library (Quick, the default): the answer streams in with math typeset; the phone button writes
    the conversation out as one prompt to copy into another AI; Research mode shows its steps, then folds
    them; the Memory view shows the library map and findings; Esc closes the chat."""
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Hall thrusters", "body": "Breathing mode oscillations near 20 kHz."})
    api(server, "POST", "/api/notes", {"type": "paper", "title": "Breathing mode in Hall thrusters", "abstract": "Ionization oscillations."})
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.chatMode')")
    page.click("#btn-chat")
    expect(page.locator("#chat")).to_be_visible()
    expect(page.locator(".rs-seg button", has_text="Quick")).to_have_attribute("aria-pressed", "true")  # Quick is the default
    expect(page.locator(".rs-notice")).to_be_hidden()
    page.locator("#chat-input").fill("What causes the breathing mode?")
    page.keyboard.press("Enter")
    log = page.locator("#chat-log")
    expect(log.locator(".msg.user")).to_contain_text("breathing mode")
    expect(log).to_contain_text("fake Claude", timeout=15000)
    expect(log.locator(".katex").first).to_be_visible()
    page.wait_for_function("() => !window.__rb.S.chatBusy", timeout=15000)  # the answer has finished, not just started
    # continue on your phone
    page.click("#btn-chat-handoff")
    sheet = page.locator(".ho-sheet")
    expect(sheet).to_be_visible()
    text = sheet.locator(".ho-text").input_value()
    assert "What causes the breathing mode?" in text and "pick up where we left off" in text
    shot(page, "handoff")
    sheet.get_by_role("button", name="Copy prompt").click()
    expect(page.locator(".ho-sheet")).to_be_hidden()
    # Research mode
    page.locator(".rs-seg button", has_text="Research").click()
    expect(page.locator(".rs-notice")).to_contain_text("Claude can only read your library here")
    page.locator("#chat-input").fill("What frequency is the breathing mode?")
    page.keyboard.press("Enter")
    expect(log.locator(".rs-step").first).to_contain_text("Searching your library")
    expect(log).to_contain_text("fake research")
    trail = log.locator(".rs-trail").last
    expect(trail.locator("summary")).to_contain_text("Researched in")
    assert trail.evaluate("e => e.open") is False  # folded once done
    trail.locator("summary").click()
    expect(trail.locator(".rs-step", has_text="Searching:")).to_be_visible()
    # the Memory view: map + findings
    page.click("#btn-memory")
    expect(page.locator(".mm")).to_contain_text("Library map")
    expect(page.locator(".mm")).to_contain_text("Findings")
    page.keyboard.press("Escape")
    expect(page.locator(".mm")).to_have_count(0)
    # registered with the command registry when it exists
    ids = page.evaluate("window.RB && RB.commands ? RB.commands.list().map(c => c.id) : null")
    if ids is not None:
        assert {"research.toggle", "research.ask", "memory.open"} <= set(ids)
    page.locator(".rs-seg button", has_text="Quick").click()
    # Esc from the chat box closes the chat
    page.locator("#chat-input").focus()
    page.keyboard.press("Escape")
    expect(page.locator("#chat")).to_be_hidden()
    assert not page.errors


def test_search_with_slash_then_export_what_matches_from_the_view_menu(ctx, server):
    """"/" focuses the search, which counts matches and opens the first with Enter; Export in the View menu
    exports only the notes matching the search (a real index.html), or all of them with no search."""
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "note", "private": False, "title": "Langmuir probe analysis", "summary": "$T_e$ from the I-V curve."})
    api(server, "POST", "/api/notes", {"type": "note", "private": False, "title": "Transformer attention"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 2")
    page.locator("#graph").click(position={"x": 5, "y": 5})
    page.keyboard.press("/")
    expect(page.locator("#search-input")).to_be_focused()
    page.keyboard.type("langmuir")
    expect(page.locator("#search-count")).to_have_text("1")
    page.keyboard.press("Enter")
    expect(page.locator("#panel-body .title-input")).to_have_value("Langmuir probe analysis")
    assert page.evaluate("window.__rb.S.cur.id") == a["id"]
    # export what the search matches
    expect(page.locator("#search-input")).to_have_value("langmuir")
    page.click("#btn-view-menu")
    page.click("#btn-export")
    modal = page.locator("#export-modal")
    expect(modal).to_contain_text("Export 1 note matching “langmuir”?")
    modal.locator("button", has_text="Export").click()
    expect(modal).to_contain_text("Export ready")
    path = Path(modal.locator(".ex-path").inner_text())
    assert path.is_file() and path.name == "index.html"
    html = path.read_text(encoding="utf-8")
    assert "Langmuir probe analysis" in html and "Transformer attention" not in html
    modal.locator("button", has_text="Done").click()
    expect(modal).to_have_count(0)
    # no search -> everything
    page.locator("#search-input").fill("")
    page.click("#btn-view-menu")
    page.click("#btn-export")
    expect(page.locator("#export-modal")).to_contain_text("Export all 2 notes?")
    page.keyboard.press("Escape")
    expect(page.locator("#export-modal")).to_have_count(0)


def test_delete(ctx, server):
    clear_vault(server)
    n = api(server, "POST", "/api/notes", {"type": "note", "title": "Temporary idea"})
    page = open_app(ctx, server)
    page.evaluate("(id) => window.__rb.openNote(id)", n["id"])
    page.locator("#panel-body .danger-zone .btn-danger").click()
    expect(page.locator("#modal")).to_be_visible()
    expect(page.locator("#modal-title")).to_contain_text("Temporary idea")
    page.click("#modal-ok")
    expect(page.locator("#panel")).to_be_hidden()
    expect(page.locator("#empty-state")).to_be_visible()
    assert note_file(server["vault"], n["id"]) is None
    assert (server["vault"] / ".trash" / f"{n['id']}.md").exists() or list((server["vault"] / ".trash").rglob(f"{n['id']}*"))


def test_library_settings_tabs_search_folders_and_no_update_ui(ctx, server, tmp_path):
    """Library settings: tabs keep kinds of settings apart and the search box finds a setting in any tab.
    Folders are picked with the computer's own chooser; the app has no update section or update chip."""
    folder = tmp_path / "My Papers"
    folder.mkdir()
    api(server, "POST", "/api/notes", {"type": "note", "title": "Counted in Where research brain saves"})
    page = open_app(ctx, server)
    page.route("**/api/pick-folder", lambda r: r.fulfill(json={"path": str(folder)}))  # the Finder dialog, answered
    page.click("#btn-view-menu")
    expect(page.locator("#btn-view-menu")).to_have_attribute("aria-label", "Settings")
    expect(page.locator("#view-menu .menu-item").first).to_contain_text("Library settings")
    page.locator("#btn-library-settings").click()
    dlg = page.locator("#library-settings")
    page.locator("#library-settings .ls-tab", has_text="Library").first.click()
    expect(dlg.locator("#ls-documents")).to_be_visible()
    expect(dlg.locator("#ls-lookup")).to_be_hidden()
    dlg.locator(".ls-tab", has_text="Adding papers").click()
    expect(dlg.locator("#ls-lookup")).to_be_visible()
    expect(dlg.locator("#ls-documents")).to_be_hidden()
    # search across tabs
    dlg.locator(".ls-search").fill("semantic scholar")
    expect(dlg.locator("#ls-lookup")).to_be_visible()
    expect(dlg.locator("#ls-documents")).to_be_hidden()
    dlg.locator(".ls-search").fill("zzzz-nothing")
    expect(dlg.locator(".ls-empty")).to_be_visible()
    dlg.locator(".ls-search").fill("")
    expect(dlg.locator("#ls-lookup")).to_be_visible()  # back on the tab you were on
    # opened from code too; step 1 is where your papers are, step 2 where research brain saves; the rest is
    # under Advanced
    page.evaluate("window.__rbSettings.open()")
    page.locator("#library-settings .ls-tab", has_text="Library").first.click()
    expect(page.locator("#ls-papers .ls-h")).to_have_text("1. Where your papers are")
    expect(page.locator("#ls-documents .ls-h")).to_have_text("2. Where research brain saves")
    expect(page.locator("#ls-advanced details")).not_to_have_attribute("open", "")
    page.locator("#ls-papers-change").click()  # where PDFs are saved: the computer's chooser, then a move if any
    if page.locator("#modal").is_visible():
        page.locator("#modal-ok").click()
    expect(page.locator("#ls-papers-path")).to_have_text(str(folder))
    page.locator("#ls-advanced summary").click()
    page.locator("#ls-advanced").get_by_role("button", name="Choose folder…").click()  # check a folder for duplicates
    expect(page.locator("#ls-scan-folder")).to_have_value(str(folder))
    expect(page.locator("#library-settings .ls-tab", has_text="Devices")).to_have_text("Devices")
    assert page.locator("#ls-updates").count() == 0
    page.wait_for_timeout(4500)  # the old update check ran after 4 s
    assert page.locator("#rb-update-chip").count() == 0
    assert not page.errors


def test_question_evidence_links_dont_blank_the_graph(ctx, server):
    """An open question's kept evidence is a link with no similarity score; it once made every position NaN."""
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Evidence paper"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Another paper"})["id"]
    q = api(server, "POST", "/api/notes", {"type": "question", "title": "Why does it happen?"})["id"]
    api(server, "PUT", f"/api/notes/{a}", {"links": [b]})
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))

    def add_question_link(route):
        resp = route.fetch()
        g = resp.json()
        g["question_links"] = [{"q": q, "paper": a, "verdict": "supports"}]
        route.fulfill(response=resp, json=g)
    page.route("**/api/graph", add_question_link)
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.fg && window.__rb.S.nodes.size >= 3")
    page.wait_for_timeout(1500)
    assert page.evaluate("window.__rb.fg.graphData().links.some(l => l.kind === 'question')")
    assert page.evaluate("window.__rb.fg.graphData().nodes.every(n => Number.isFinite(n.x) && Number.isFinite(n.y))")
    assert page.evaluate("Number.isFinite(window.__rb.fg.zoom())")
    assert not page.errors


def test_a_dragged_dot_stays_where_you_drop_it(ctx, server):
    clear_vault(server)
    for t in ("Dot one", "Dot two", "Dot three"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    page.wait_for_timeout(2500)
    nid, sx, sy = page.evaluate("""() => { const fg = window.__rb.fg, n = fg.graphData().nodes[0];
      const p = fg.graph2ScreenCoords(n.x, n.y); return [n.id, p.x, p.y]; }""")
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    page.mouse.down()
    for i in range(1, 11):
        page.mouse.move(box["x"] + sx + 12 * i, box["y"] + sy + 8 * i)
    page.mouse.up()
    page.wait_for_timeout(1500)
    # saved as where it sits within its region (regions move as the library grows; the dot moves with its region)
    saved = page.evaluate("JSON.parse(localStorage.getItem('rb.pinsRel'))")
    assert nid in saved
    home = page.evaluate("(id) => window.__rb.S.nodes.get(id).__home", nid)
    # not frozen (no fixed position), but it settles where you dropped it
    page.evaluate("() => { window.__stops0 = window.__rb.S.stops || 0; window.__rb.relayout(); }")
    page.wait_for_function("() => (window.__rb.S.stops || 0) > window.__stops0", timeout=30000)
    n = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y, n.fx === undefined, !!n.__userPin]; }", nid)
    assert n[2] and n[3]
    assert abs(n[0] - home[0]) < 15 and abs(n[1] - home[1]) < 15, (n, home)
    page.reload()
    page.wait_for_function("(id) => window.__rb && window.__rb.S.nodes.has(id)", arg=nid)
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)
    after = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y, !!n.__userPin]; }", nid)
    assert after[2] and abs(after[0] - home[0]) < 15 and abs(after[1] - home[1]) < 15, (after, home)
    page.evaluate("RB.commands.run('graph.unpinAll')")
    assert page.evaluate("(id) => !window.__rb.S.nodes.get(id).__home", nid)
    assert not page.errors


def test_a_new_paper_joins_the_dots_you_placed_and_old_pins_dont_bend_the_map(ctx, server):
    """Dots you placed are kept relative to their region, so they can't pin the map into a chain: a paper of that
    region you never moved (added later) settles among them, and positions saved the old way (map coordinates
    under 'rb.pins', which froze regions into lines) are dropped."""
    clear_vault(server)
    group = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Cross section paper {i}"})["id"]
             for i in range(4)]
    late = api(server, "POST", "/api/notes", {"type": "paper", "title": "Stopping power paper added later"})["id"]
    other = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Line ratio paper {i}"})["id"]
             for i in range(4)]
    region = {**{i: 0 for i in group + [late]}, **{i: 1 for i in other}}

    def two_regions(route):
        resp = route.fetch()
        g = resp.json()
        for n in g["nodes"]:
            n["cluster"], n["sub"] = region.get(n["id"], -1), -1
        g["clusters"] = [{"id": 0, "label": "Cross sections", "size": 5, "topic": None, "near": []},
                         {"id": 1, "label": "Line ratios", "size": 4, "topic": None, "near": []}]
        route.fulfill(response=resp, json=g)
    rel = {nid: [-9 + 6 * i, 6 - 4 * i] for i, nid in enumerate(group)}  # around the middle of their region
    legacy = {other[0]: [-400, 900]}  # an old map-coordinate pin, far away
    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.add_init_script(f"localStorage.setItem('rb.pinsRel', {json.dumps(json.dumps(rel))});"
                         f"if (!sessionStorage.getItem('seeded')) {{ localStorage.setItem('rb.pins', {json.dumps(json.dumps(legacy))}); sessionStorage.setItem('seeded', '1'); }}")
    page.route("**/api/graph", two_regions)
    page.goto(server["base"] + "/")
    page.wait_for_function("(n) => window.__rb && window.__rb.fg && window.__rb.S.nodes.size >= n", arg=9)
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)
    pos = lambda i: page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y]; }", i)
    placed = [pos(i) for i in group]
    cx, cy = sum(p[0] for p in placed) / 4, sum(p[1] for p in placed) / 4
    x, y = pos(late)
    assert ((x - cx) ** 2 + (y - cy) ** 2) ** 0.5 < 30, ((x, y), (cx, cy))
    # the old-style pin is gone and didn't drag its dot (or its region) across the map
    assert page.evaluate("localStorage.getItem('rb.pins')") is None
    ox, oy = pos(other[0])
    assert abs(ox) < 150 and abs(oy) < 150, (ox, oy)
    assert not page.evaluate("(id) => !!window.__rb.S.nodes.get(id).__userPin", other[0])
    page.evaluate("RB.commands.run('graph.unpinAll')")
    assert not page.errors


def test_dragging_a_dot_moves_only_that_dot(ctx, server):
    """Dragging one paper never carries others along, not even the ones linked to it: its lines stretch."""
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Main dot"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Linked dot"})["id"]
    c = api(server, "POST", "/api/notes", {"type": "paper", "title": "Unrelated dot about something else"})["id"]
    api(server, "POST", f"/api/notes/{a}/link", {"target": b})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    page.wait_for_timeout(2500)
    pos = lambda i: page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y]; }", i)
    (ax0, ay0), (bx0, by0), (cx0, cy0) = pos(a), pos(b), pos(c)
    sx, sy = page.evaluate("(id) => { const fg = window.__rb.fg, n = window.__rb.S.nodes.get(id); const p = fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }", a)
    k = page.evaluate("window.__rb.fg.zoom()")
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    page.mouse.down()
    for i in range(1, 21):
        page.mouse.move(box["x"] + sx + 10 * i, box["y"] + sy + 6 * i)
    page.mouse.up()
    moved = (200 / k, 120 / k)  # how far the main dot went, in graph units
    page.wait_for_timeout(2500)
    (ax1, ay1), (bx1, by1), (cx1, cy1) = pos(a), pos(b), pos(c)
    assert (ax1 - ax0) > 0.6 * moved[0] and (ay1 - ay0) > 0.6 * moved[1], ((ax0, ay0), (ax1, ay1), moved)
    # the linked dot and the unrelated one stayed where they were
    assert abs(bx1 - bx0) < 0.05 * moved[0] and abs(by1 - by0) < 0.05 * moved[1], ((bx0, by0), (bx1, by1), moved)
    assert abs(cx1 - cx0) < 0.05 * moved[0] and abs(cy1 - cy0) < 0.05 * moved[1], ((cx0, cy0), (cx1, cy1))
    assert not page.errors


def test_resizing_the_window_and_changing_screens_keep_the_map_whole(ctx, server):
    """Window resizes and screen changes, on one settled map:
    - shrinking the window and growing it back leaves the map as it was (step-by-step zoom factors used to
      ratchet it smaller with every resize until it almost disappeared);
    - after a resize the map is refitted, so every dot is on screen;
    - each step of a resize used to wipe the map until the next frame (a flicker): right after the app
      handles a resize, the canvas must still show the map;
    - Fit used to do a third of a zoom-in from far out in one frame; it should feel the same both ways;
    - dragging the window to a Retina or a regular monitor changes the pixel density, not the size: the map's
      canvas must follow, or it draws at the wrong scale and the pointer finds the wrong dots."""
    import math
    clear_vault(server)
    for i in range(10):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Resize paper {i} on topic {i % 3}"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 10")
    page.wait_for_timeout(2500)
    inside = """() => { const fg = window.__rb.fg, el = document.getElementById('graph');
      return [...window.__rb.S.nodes.values()].every(n => { const p = fg.graph2ScreenCoords(n.x, n.y);
        return p.x >= 0 && p.y >= 0 && p.x <= el.clientWidth && p.y <= el.clientHeight; }); }"""

    # back and forth doesn't shrink it
    k0 = page.evaluate("window.__rb.fg.zoom()")
    W, H = 1400, 900
    for _ in range(4):  # drag the right edge in and out, then the bottom edge, then a corner
        for w in list(range(W, 900, -50)) + list(range(900, W + 1, 50)):
            page.set_viewport_size({"width": w, "height": H}); page.wait_for_timeout(10)
        for h in list(range(H, 560, -40)) + list(range(560, H + 1, 40)):
            page.set_viewport_size({"width": W, "height": h}); page.wait_for_timeout(10)
    for step in list(range(0, 10)) + list(range(10, -1, -1)):
        page.set_viewport_size({"width": W - step * 40, "height": H - step * 25}); page.wait_for_timeout(10)
    page.set_viewport_size({"width": W, "height": H})
    page.wait_for_timeout(1500)
    k1 = page.evaluate("window.__rb.fg.zoom()")
    assert abs(k1 - k0) / k0 < 0.05, (k0, k1)
    assert page.evaluate(inside)

    # refitted after a resize
    for size in ({"width": 700, "height": 500}, {"width": 1600, "height": 1000}, {"width": 900, "height": 700}):
        page.set_viewport_size(size)
        page.wait_for_timeout(1200)  # the refit runs once resizing stops
        assert page.evaluate(inside), size

    # never a blank frame while resizing
    page.evaluate("""() => { window.__fl = {blank: 0, steps: 0};
      new ResizeObserver(() => { const c = document.querySelector('#graph canvas'); if (!c) return;
        const d = c.getContext('2d').getImageData(0, 0, c.width, c.height).data; let ink = 0;
        for (let i = 3; i < d.length; i += 4 * 97) if (d[i] > 0) { ink++; if (ink > 3) break; }
        window.__fl.steps++; if (ink <= 3) window.__fl.blank++; }).observe(document.getElementById('graph')); }""")
    for i in range(25):
        page.set_viewport_size({"width": 1100 + i * 12, "height": 760 + i * 6})
        page.wait_for_timeout(16)
    page.wait_for_timeout(800)
    fl = page.evaluate("window.__fl")
    assert fl["steps"] >= 10 and fl["blank"] == 0, fl
    page.set_viewport_size({"width": W, "height": H})
    page.wait_for_timeout(1500)

    # Fit is as smooth from far out as from close in
    shares = {}
    for label, start in (("far", 0.13), ("close", 60)):
        page.evaluate(f"() => {{ window.__rb.fg.zoom({start}); }}")
        page.wait_for_timeout(400)
        page.evaluate("""() => { window.__ks = []; const rec = () => { window.__ks.push(window.__rb.fg.zoom()); if (window.__ks.length < 70) requestAnimationFrame(rec); }; requestAnimationFrame(rec); }""")
        page.click("#btn-zoom-fit")
        page.wait_for_timeout(1600)
        ls = [math.log(k) for k in page.evaluate("window.__ks") if k > 0]
        steps = [abs(b - a) for a, b in zip(ls, ls[1:])]
        shares[label] = max(steps) / (abs(ls[-1] - ls[0]) or 1)
    assert shares["far"] < 0.15 and shares["close"] < 0.15, shares

    # another pixel density
    canvas = "() => { const c = document.querySelector('#graph canvas'); return [c.width, document.getElementById('graph').clientWidth, window.devicePixelRatio]; }"
    w, css, dpr = page.evaluate(canvas)
    assert abs(w - css * dpr) <= 2
    cdp = page.context.new_cdp_session(page)
    vp = page.viewport_size
    cdp.send("Emulation.setDeviceMetricsOverride", {"width": vp["width"], "height": vp["height"], "deviceScaleFactor": 2 if dpr < 2 else 1, "mobile": False})
    page.wait_for_function("window.devicePixelRatio !== %s" % dpr)
    page.wait_for_function("() => { const c = document.querySelector('#graph canvas'); return Math.abs(c.width - document.getElementById('graph').clientWidth * window.devicePixelRatio) <= 2; }", timeout=4000)
    page.wait_for_timeout(1200)  # the refit after a screen change animates the view
    w2, css2, dpr2 = page.evaluate(canvas)
    assert dpr2 != dpr and abs(w2 - css2 * dpr2) <= 2, (w2, css2, dpr2)
    # and hovering a dot still finds it
    sx, sy = page.evaluate("() => { const fg = window.__rb.fg, n = [...window.__rb.S.nodes.values()][0]; const p = fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }")
    box = page.locator("#graph").bounding_box()
    page.mouse.move(5, box["y"] + 300)
    page.wait_for_timeout(300)
    page.mouse.move(box["x"] + sx, box["y"] + sy, steps=6)
    page.wait_for_function("!!window.__rb.S.hover", timeout=4000)
    cdp.send("Emulation.clearDeviceMetricsOverride")
    assert not page.errors


def test_ask_about_topics_and_the_conversation_is_remembered(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Sheath paper", "abstract": "sheath ions wall"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Wall paper", "abstract": "wall emission sheath"})["id"]
    t = api(server, "POST", "/api/topics", {"name": "Sheath physics", "members": [a, b]})["slug"]
    api(server, "PUT", "/api/chats/library", {"messages": []})
    page = open_app(ctx, server)
    page.evaluate("RB.commands.run('chat.aboutTopics')")
    page.locator(".tp-sheet .tp-row", has_text="Sheath physics").click()
    page.locator(".tp-sheet .tp-acts .btn-primary").click()
    expect(page.locator("#chat .chat-title")).to_have_text("This region")
    expect(page.locator("#chat-scope .cs-title")).to_have_text("Sheath physics")
    page.fill("#chat-input", "What do they say about the sheath?")
    page.click("#btn-chat-send")
    expect(page.locator("#chat .msg.assistant").last).not_to_contain_text("reading", timeout=20_000)
    for _ in range(50):  # saved in the background once the answer is in
        saved = api(server, "GET", f"/api/chats/topics-{t}")
        if len(saved["messages"]) == 2:
            break
        page.wait_for_timeout(100)
    assert [m["role"] for m in saved["messages"]] == ["user", "assistant"]
    # a new session: the conversation is still there
    page2 = open_app(ctx, server)
    page2.evaluate(f"window.__rb.chatAboutTopics([{{slug: '{t}', name: 'Sheath physics'}}])")
    expect(page2.locator("#chat .msg.user")).to_contain_text("What do they say about the sheath?")
    assert not page.errors and not page2.errors


def test_general_chat_web_switch_attachments_and_the_one_time_warning(ctx, server):
    """Ask anything: the web switch is off while Settings forbid web search and works once they allow it, and
    the General conversation is kept across modes. Attaching a published paper with the web on sends without
    a warning; attaching your own note asks once, then answers and remembers the choice."""
    clear_vault(server)
    api(server, "PUT", "/api/privacy/settings", {"web_search": False, "web_with_attachments": False})
    api(server, "PUT", "/api/chats/general", {"messages": []})
    api(server, "POST", "/api/notes", {"type": "paper", "title": "Sheath formation at a floating wall", "abstract": "Bohm criterion"})
    n = api(server, "POST", "/api/notes", {"type": "note", "title": "My presheath idea", "body": "an idea"})["id"]
    api(server, "PUT", f"/api/notes/{n}", {"private": False})
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.generalAttach')")
    page.evaluate("RB.commands.run('chat.general')")
    expect(page.locator("#chat .chat-title")).to_have_text("Ask anything")
    expect(page.locator(".chat-modes button[data-mode=general]")).to_have_class(re.compile(r"\bon\b"))
    expect(page.locator("#cs-web")).to_be_disabled()  # web search is off in Settings
    page.fill("#chat-input", "What is a Debye length?")
    page.click("#btn-chat-send")
    expect(page.locator("#chat .msg.assistant").last).to_contain_text("fake Claude", timeout=20_000)
    page.wait_for_function("!window.__rb.S.chatBusy")  # modes don't switch mid-answer
    # allowed in Settings: the switch works
    api(server, "PUT", "/api/privacy/settings", {"web_search": True})
    page.locator(".chat-modes button[data-mode=library]").click()
    page.locator(".chat-modes button[data-mode=general]").click()
    expect(page.locator("#cs-web")).to_be_enabled()
    expect(page.locator("#chat .msg.user")).to_contain_text("Debye length")  # the General conversation is kept
    page.check("#cs-web")
    page.fill("#chat-input", "Recent reviews on sheaths?")
    page.click("#btn-chat-send")
    expect(page.locator("#chat .msg.assistant").last).to_contain_text("fake Claude", timeout=20_000)
    page.wait_for_function("!window.__rb.S.chatBusy")
    # attach a published paper
    page.click("#cs-attach")
    page.fill(".ca-sheet .ca-q", "sheath")
    page.locator(".ca-sheet .ca-row", has_text="Sheath formation").click()
    page.click(".ca-sheet button:has-text('Done')")
    expect(page.locator("#cs-chips .cs-chip")).to_have_count(1)
    expect(page.locator("#chat-general .cs-note")).to_contain_text("item you attached")
    # web + a published paper: no warning
    expect(page.locator("#cs-web")).to_be_checked()
    page.fill("#chat-input", "Newer work on this?")
    page.click("#btn-chat-send")
    expect(page.locator("#chat .msg.assistant").last).to_contain_text("fake Claude", timeout=20_000)
    page.wait_for_function("!window.__rb.S.chatBusy")
    expect(page.locator("#modal")).to_be_hidden()
    # web + your own note: asks once, then answers
    page.click("#cs-attach")
    page.fill(".ca-sheet .ca-q", "presheath")
    page.locator(".ca-sheet .ca-row", has_text="My presheath idea").click()
    page.click(".ca-sheet button:has-text('Done')")
    page.fill("#chat-input", "Is my idea new?")
    page.click("#btn-chat-send")
    expect(page.locator("#modal")).to_be_visible()
    expect(page.locator("#modal-title")).to_contain_text("own notes")
    page.click("#modal-ok")
    expect(page.locator("#chat .msg.user").last).to_contain_text("Is my idea new?")
    expect(page.locator("#chat .msg.assistant").last).to_contain_text("fake Claude", timeout=20_000)
    assert api(server, "GET", "/api/privacy")["settings"]["web_with_attachments"] is True
    api(server, "PUT", "/api/privacy/settings", {"web_search": False, "web_with_attachments": False})
    assert not page.errors


def test_right_click_empty_map_adds_things_there(ctx, server):
    clear_vault(server)
    for t in ("Map paper one", "Map paper two"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 2")
    page.wait_for_timeout(1500)
    box = page.locator("#graph").bounding_box()
    x, y = box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.3   # an empty corner of the map
    page.mouse.click(x, y, button="right")
    menu = page.locator(".node-menu")
    expect(menu).to_contain_text("Add here")
    expect(menu).to_contain_text("Add a paper by DOI")
    expect(menu).to_contain_text("Import a PDF")
    want = page.evaluate("([sx, sy]) => window.__rb.fg.screen2GraphCoords(sx, sy)", [x - box["x"], y - box["y"]])
    menu.locator(".menu-item", has_text="Write a note here").click()
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.type === 'note'")
    nid = page.evaluate("window.__rb.S.cur.id")
    page.wait_for_timeout(1500)
    n = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y, !!n.__userPin]; }", nid)
    assert n[2] and abs(n[0] - want["x"]) < 15 and abs(n[1] - want["y"]) < 15, (n, want)
    # "Add a paper…" opens the Add box with the cursor in it
    page.evaluate("window.__rb.closePanel()")
    ex, ey = box["x"] + box["width"] * 0.15, box["y"] + box["height"] * 0.8
    page.mouse.move(ex, ey, steps=8)  # like a real pointer: hover moves off the note first
    page.wait_for_timeout(300)
    page.mouse.click(ex, ey, button="right")
    page.locator(".node-menu .menu-item", has_text="Add a paper by DOI").click()
    expect(page.locator("#add-menu")).to_be_visible()
    expect(page.locator("#lookup-input")).to_be_focused()
    assert not page.errors


def test_a_long_right_click_menu_scrolls_instead_of_closing(ctx, server):
    """A right-click menu taller than the window fits inside it and scrolls: the wheel over the menu scrolls it (it
    used to close the menu), the wheel over the map still closes it."""
    clear_vault(server)
    for t in ("Menu paper one", "Menu paper two"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = ctx.new_page()
    page.set_viewport_size({"width": 1100, "height": 260})
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 2")
    page.wait_for_timeout(1200)
    box = page.locator("#graph").bounding_box()
    page.mouse.click(box["x"] + box["width"] * 0.25, box["y"] + box["height"] * 0.5, button="right")  # an empty spot
    menu = page.locator(".node-menu")
    expect(menu).to_be_visible()
    r = menu.bounding_box()
    assert r["y"] >= 0 and r["y"] + r["height"] <= 260 + 0.5, r  # inside the window
    assert page.evaluate("(() => { const m = document.querySelector('.node-menu'); return m.scrollHeight > m.clientHeight; })()")
    page.mouse.move(r["x"] + r["width"] / 2, r["y"] + r["height"] / 2)
    page.mouse.wheel(0, 200)
    page.wait_for_timeout(300)
    expect(menu).to_be_visible()
    assert page.evaluate("document.querySelector('.node-menu').scrollTop") > 0
    page.mouse.move(box["x"] + box["width"] - 40, box["y"] + box["height"] / 2)
    page.mouse.wheel(0, 100)
    expect(menu).to_have_count(0)
    assert not page.errors


def test_right_click_moves_a_paper_to_another_region_and_chooses_its_main_one(ctx, server):
    """Right-click a paper: Move to region takes it out of its region and it is drawn in the new one (Put in region
    used to leave it where it was); in two regions, Make … its main region decides where it's drawn; Remove from
    takes it out. A click on empty map clears the search."""
    clear_vault(server)
    kr = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Krypton collisional radiative model {i}",
                                             "abstract": "collisional radiative model krypton emission"})["id"] for i in range(5)]
    ha = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster breathing mode {i}",
                                             "abstract": "hall thruster breathing oscillation discharge"})["id"] for i in range(3)]
    api(server, "POST", "/api/topics", {"name": "Krypton models", "members": kr})
    api(server, "POST", "/api/topics", {"name": "Hall thrusters", "members": ha})
    p = kr[0]
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 8")
    page.wait_for_timeout(2000)
    cl = lambda i: page.evaluate("(id) => window.__rb.S.nodes.get(id).cluster", i)

    def right_click(i):
        sx, sy = page.evaluate("(id) => { const fg = window.__rb.fg, n = window.__rb.S.nodes.get(id); const q = fg.graph2ScreenCoords(n.x, n.y); return [q.x, q.y]; }", i)
        box = page.locator("#graph").bounding_box()
        page.mouse.move(box["x"] + sx - 30, box["y"] + sy - 20)
        page.mouse.move(box["x"] + sx, box["y"] + sy, steps=6)  # like a real pointer: the map knows the dot under it
        page.wait_for_timeout(200)
        page.mouse.click(box["x"] + sx, box["y"] + sy, button="right")
        menu = page.locator(".node-menu")
        expect(menu.locator(".menu-regions .menu-label").first).to_be_visible()
        return menu

    assert cl(p) == cl(kr[1])
    menu = right_click(p)
    expect(menu).to_contain_text("Move to region")
    expect(menu).to_contain_text("Remove from Krypton models")
    menu.locator(".menu-regions .menu-item", has_text="Hall thrusters").first.click()
    page.wait_for_function(f"window.__rb.S.nodes.get('{p}').cluster === window.__rb.S.nodes.get('{ha[0]}').cluster")
    assert [t["name"] for t in api(server, "GET", f"/api/notes/{p}/regions")["topics"]] == ["Hall thrusters"]

    # in both regions: drawn in the main one
    api(server, "POST", f"/api/notes/{p}/region", {"to": "", "out_of": []})
    kr_slug = next(t["slug"] for t in api(server, "GET", "/api/topics") if t["name"] == "Krypton models")
    api(server, "POST", f"/api/topics/{kr_slug}/notes", {"ids": [p]})
    page.evaluate("window.__rb.refreshGraph()")
    page.wait_for_function(f"window.__rb.S.nodes.get('{p}').cluster === window.__rb.S.nodes.get('{kr[1]}').cluster")  # the bigger one
    menu = right_click(p)
    expect(menu).to_contain_text("Its regions")
    menu.locator(".menu-item", has_text="Make Hall thrusters its main region").click()
    page.wait_for_function(f"window.__rb.S.nodes.get('{p}').cluster === window.__rb.S.nodes.get('{ha[0]}').cluster")
    menu = right_click(p)
    expect(menu).to_contain_text("Main region: Hall thrusters")
    menu.locator(".menu-item", has_text="Remove from Hall thrusters").click()
    page.wait_for_function(f"window.__rb.S.nodes.get('{p}').cluster === window.__rb.S.nodes.get('{kr[1]}').cluster")
    assert api(server, "GET", f"/api/notes/{p}/regions")["main_region"] is None

    # search, then a click on empty map clears it
    page.fill("#search-input", "krypton")
    page.wait_for_function("window.__rb.S.search === 'krypton'")
    box = page.locator("#graph").bounding_box()
    page.mouse.click(box["x"] + 30, box["y"] + 120)
    page.wait_for_function("window.__rb.S.search === ''")
    expect(page.locator("#search-input")).to_have_value("")
    assert not page.errors


def test_the_map_opens_with_every_dot_on_screen_also_after_a_resize_while_it_glides_in(ctx, server):
    """The first view is framed around where the dots come to rest (not a layout worked out before the forces had
    the dots, which came out cramped), however slowly the library loads and if the window is resized meanwhile."""
    clear_vault(server)
    ids = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Opening view paper {i}"})["id"] for i in range(24)]
    region = {nid: i % 4 for i, nid in enumerate(ids)}
    spots = {f"l:opening region {c}": xy for c, xy in enumerate(([-90, -50], [90, -50], [-90, 60], [90, 60]))}

    def graph(wait):
        def handle(route):
            resp = route.fetch()
            g = resp.json()
            for n in g["nodes"]:
                n["cluster"], n["sub"] = region.get(n["id"], -1), -1
            g["clusters"] = [{"id": c, "label": f"Opening region {c}", "size": 6, "topic": None, "near": []} for c in range(4)]
            time.sleep(wait)
            route.fulfill(response=resp, json=g)
        return handle
    off = """() => { const fg = window.__rb.fg, el = document.getElementById('graph');
      return [...window.__rb.S.nodes.values()].filter(n => { const p = fg.graph2ScreenCoords(n.x, n.y);
        return !(p.x > 0 && p.y > 0 && p.x < el.clientWidth && p.y < el.clientHeight); }).length; }"""
    for wait, resize in ((0, None), (0.8, None), (0, (950, 650))):
        page = ctx.new_page()
        page.errors = []
        page.on("pageerror", lambda e: page.errors.append(str(e)))
        page.add_init_script(f"localStorage.setItem('rb.regionPos', {json.dumps(json.dumps(spots))});")
        page.route("**/api/graph", graph(wait))
        page.goto(server["base"] + "/")
        page.wait_for_function("window.__rb && window.__rb.fg && window.__rb.S.nodes.size >= 24")
        if resize:
            page.wait_for_timeout(600)  # still gliding in
            page.set_viewport_size({"width": resize[0], "height": resize[1]})
        page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)
        page.wait_for_timeout(1500)
        assert page.evaluate(off) == 0, (wait, resize)
        assert not page.errors
        page.close()


def test_dot_size_is_a_subtle_hint(ctx, server):
    """Opening a paper a lot, or writing about it, makes its dot a little larger, never several times larger."""
    page = open_app(ctx, server)
    plain, opened, most = page.evaluate("""() => [{}, { opens: 100 }, { mass: 5000, opens: 500, degree: 60, parts: 20 }]
      .map(n => window.__rb.nodeRadius({ type: 'paper', ...n }))""")
    assert plain < opened < plain * 1.25
    assert opened < most <= plain * 1.7


def test_zooming_in_stops_only_when_nothing_would_be_left_on_screen(ctx, server):
    clear_vault(server)
    for t in ("Zoom paper one", "Zoom paper two", "Zoom paper three"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)  # the layout has come to rest
    page.wait_for_timeout(1200)  # and the view has been fitted to it
    box = page.locator("#graph").bounding_box()
    in_view = """() => { const fg = window.__rb.fg, el = document.getElementById('graph');
      return [...window.__rb.S.nodes.values()].some(n => { const p = fg.graph2ScreenCoords(n.x, n.y);
        return p.x >= 0 && p.y >= 0 && p.x <= el.clientWidth && p.y <= el.clientHeight; }); }"""
    # over a dot: far past the old limit of 10
    sx, sy = page.evaluate("() => { const fg = window.__rb.fg, n = [...window.__rb.S.nodes.values()][0]; const p = fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }")
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    for _ in range(40):
        page.mouse.wheel(0, -400)
        page.wait_for_timeout(20)
    page.wait_for_timeout(400)
    assert page.evaluate("window.__rb.fg.zoom()") > 10
    assert page.evaluate(in_view)
    # back out, then into an empty corner: it stops while something is still in view
    for _ in range(40):
        page.mouse.wheel(0, 400)
        page.wait_for_timeout(20)
    page.wait_for_timeout(400)
    page.evaluate("() => { window.__rb.fg.zoomToFit(0, 80); }")
    page.wait_for_timeout(300)
    page.mouse.move(box["x"] + 8, box["y"] + 8)
    for _ in range(60):
        page.mouse.wheel(0, -400)
        page.wait_for_timeout(20)
    page.wait_for_timeout(400)
    assert page.evaluate(in_view)
    k = page.evaluate("window.__rb.fg.zoom()")
    page.mouse.wheel(0, 800)  # zooming out is never stopped
    page.wait_for_timeout(300)
    assert page.evaluate("window.__rb.fg.zoom()") < k
    assert not page.errors


def test_ask_about_topics_select_all(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "First topic paper"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Second topic paper"})["id"]
    c = api(server, "POST", "/api/notes", {"type": "paper", "title": "Third topic paper"})["id"]
    for name, members in (("Alpha topic", [a]), ("Beta topic", [b, c])):
        api(server, "POST", "/api/topics", {"name": name, "members": members})
    page = open_app(ctx, server)
    page.evaluate("RB.commands.run('chat.aboutTopics')")
    sheet = page.locator(".tp-sheet")
    go = sheet.locator(".tp-acts .btn-primary")
    expect(go).to_be_disabled()
    sheet.locator("#tp-all").check()
    expect(sheet.locator(".tp-list input:checked")).to_have_count(2)
    expect(go).to_have_text("Ask about all 2 regions (3 papers)")
    sheet.locator(".tp-row", has_text="Alpha topic").locator("input").uncheck()
    assert page.evaluate("document.getElementById('tp-all').indeterminate")
    expect(go).to_have_text("Ask about 1 region (2 papers)")
    sheet.locator("#tp-all").check()
    go.click()
    expect(page.locator("#chat-scope .cs-title")).to_contain_text("Alpha topic")
    expect(page.locator("#chat-scope .cs-title")).to_contain_text("Beta topic")
    expect(page.locator("#chat .chat-title")).to_have_text("These regions")
    assert not page.errors


def test_panels_can_be_made_wider(ctx, server):
    clear_vault(server)
    nid = api(server, "POST", "/api/notes", {"type": "paper", "title": "Wide panel paper"})["id"]
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.panelW'); localStorage.removeItem('rb.chatW')")
    page.evaluate(f"window.__rb.openNote('{nid}')")
    panel = page.locator("#panel")
    expect(panel).to_be_visible()
    vw = page.evaluate("window.innerWidth")
    w0 = panel.bounding_box()["width"]
    grip = page.locator("#panel-resizer").bounding_box()
    gx, gy = grip["x"] + grip["width"] / 2, grip["y"] + grip["height"] / 2
    page.mouse.move(gx, gy)
    page.mouse.down()
    page.mouse.move(40, gy, steps=10)  # far to the left: as wide as it may go
    page.mouse.up()
    w1 = panel.bounding_box()["width"]
    assert w1 > w0 and w1 > vw * 0.75, (w0, w1, vw)  # wider than the old 75% limit
    assert w1 <= vw - 240 + 1  # but the map keeps some room
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.fg !== undefined")
    page.evaluate(f"window.__rb.openNote('{nid}')")
    expect(panel).to_be_visible()
    assert abs(panel.bounding_box()["width"] - w1) < 2  # remembered
    page.evaluate("window.__rb.closePanel()")
    # the chat, with the keyboard
    page.evaluate("window.__rb.openChat()")
    chat = page.locator("#chat")
    c0 = chat.bounding_box()["width"]
    page.locator("#chat-resizer").focus()
    page.keyboard.press("Shift+ArrowLeft")
    page.keyboard.press("Shift+ArrowLeft")
    assert chat.bounding_box()["width"] >= c0 + 150
    page.locator("#chat-resizer").dblclick()  # back to the default
    assert abs(chat.bounding_box()["width"] - c0) < 2
    assert not page.errors


def test_map_labels_first_author_and_year_by_default(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Kinetic simulations of Hall thruster plasma",
                                           "authors": ["Boeuf, J.-P.", "Garrigues, L."], "year": 2018})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Molecular forces", "authors": ["Johannes van der Waals"], "year": 1873})["id"]
    n = api(server, "POST", "/api/notes", {"type": "note", "title": "My own idea"})["id"]
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.labelMode')")
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 3")
    lab = lambda i: page.evaluate("(id) => window.__rb.mapLabel(window.__rb.S.nodes.get(id))", i)
    assert lab(a) == "Boeuf, 2018" and lab(b) == "van der Waals, 1873" and lab(n) == "My own idea"
    page.click("#btn-view-menu")
    page.select_option("#label-mode", "authorTitle")
    assert lab(a).startswith("Boeuf, 2018 · Kinetic simulations")
    page.select_option("#label-mode", "title")
    assert lab(a).startswith("Kinetic simulations of Hall")
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 3")
    assert lab(a).startswith("Kinetic simulations")  # the preference is kept
    page.evaluate("localStorage.removeItem('rb.labelMode')")
    assert not page.errors


def test_hover_card_pops_out_and_dots_make_room(ctx, server):
    """Hovering a dot opens a card with its title and authors; dots under the card move aside while it's open,
    it closes as soon as the pointer leaves the dot, and afterwards every dot is exactly where it was."""
    clear_vault(server)
    main = api(server, "POST", "/api/notes", {"type": "paper", "title": "A long title about electron transport in low-temperature plasmas with anomalous collisions",
                                               "year": 2018, "authors": ["Jean-Pierre Boeuf", "Laurent Garrigues"]})["id"]
    for i in range(6):
        nid = api(server, "POST", "/api/notes", {"type": "paper", "title": f"Neighbour {i}", "year": 2010 + i})["id"]
        api(server, "PUT", f"/api/notes/{nid}", {"links": [main]})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 7")
    # let the layout come to rest (it eases to a stop over several seconds), then the last ease off a line
    page.wait_for_function("() => (window.__rb.S.stops || 0) >= 1", timeout=30000)
    page.wait_for_timeout(1000)
    before = page.evaluate("() => Object.fromEntries([...window.__rb.S.nodes.values()].map(n => [n.id, [n.x, n.y]]))")
    box = page.locator("#graph").bounding_box()
    sx, sy = page.evaluate(f"() => {{ const n = window.__rb.S.nodes.get('{main}'), p = window.__rb.fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }}")
    page.mouse.move(5, box["y"] + 5)
    page.mouse.move(box["x"] + sx, box["y"] + sy, steps=6)
    page.wait_for_function(f"() => window.__rb.cards.some(c => c.id === '{main}' && c.p === 1)", timeout=4000)
    # no other dot sits under the open card
    under = page.evaluate(f"""() => {{
      const c = window.__rb.cards.find(c => c.id === '{main}'), [x0, y0, w, h] = c.box;
      return [...window.__rb.S.nodes.values()].filter(n => n.id !== '{main}')
        .filter(n => {{ const x = n.x + (n.__dx || 0), y = n.y + (n.__dy || 0); return x > x0 && x < x0 + w && y > y0 && y < y0 + h; }}).length;
    }}""")
    assert under == 0
    # a moved dot that slides under the pointer doesn't take over (its card moved others there, and so on for ever)
    kept = page.evaluate(f"""() => {{ const rb = window.__rb, on = rb.fg.onNodeHover(), main = rb.S.nodes.get('{main}');
      const moved = [...rb.S.nodes.values()].find(n => n.__dx || n.__dy) || Object.assign([...rb.S.nodes.values()].find(n => n !== main), {{ __dx: 9 }});
      if (!rb.S.shifted.length) rb.S.shifted = [moved];
      const out = []; for (const n of [moved, null]) {{ on(n, main); out.push(rb.S.hover); }} on(main, null); return out; }}""")
    assert kept == [main, main]
    # the card is its dot's too: it stays open with the pointer on it, and a click on it opens the paper
    x0, y0, w, h = page.evaluate(f"() => window.__rb.cards.find(c => c.id === '{main}').box")
    mid = page.evaluate(f"() => window.__rb.fg.graph2ScreenCoords({x0 + w * 0.5}, {y0 + h * 0.6})")
    page.mouse.move(box["x"] + mid["x"], box["y"] + mid["y"], steps=3)
    page.wait_for_timeout(500)
    assert page.evaluate(f"() => window.__rb.cards.some(c => c.id === '{main}') && window.__rb.S.hover === '{main}'")
    page.mouse.click(box["x"] + mid["x"], box["y"] + mid["y"])
    page.wait_for_function(f"() => window.__rb.S.cur && window.__rb.S.cur.id === '{main}'", timeout=4000)
    page.keyboard.press("Escape")
    # moving away closes it, and nothing has moved for real
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] - 5, steps=6)
    page.wait_for_function("() => window.__rb.cards.length === 0", timeout=4000)
    page.wait_for_timeout(400)
    after = page.evaluate("() => Object.fromEntries([...window.__rb.S.nodes.values()].map(n => [n.id, [n.x, n.y, n.__dx || 0, n.__dy || 0]]))")
    for nid, (x, y) in before.items():
        ax, ay, dx, dy = after[nid]
        assert abs(ax - x) < 0.5 and abs(ay - y) < 0.5 and dx == 0 and dy == 0, nid
    assert not page.errors


def test_no_dot_sits_on_a_line_that_isnt_its_own(ctx, server):
    clear_vault(server)
    q = api(server, "POST", "/api/notes", {"type": "question", "title": "Why do cross sections matter?"})["id"]
    # distinct titles: papers that all read alike are joined by similarity lines between every pair, and a hub
    # in the middle of its papers then has those lines across it wherever it goes; a real library isn't like that
    topics = ["Electron impact ionization of xenon", "Hollow cathode plume oscillations", "Sputter yield of boron nitride",
              "Hall thruster wall erosion", "Charge exchange collisions in plumes", "Ion optics grid lifetime",
              "Magnetic nozzle detachment", "Plasma sheath kinetics near walls"]
    linked = [api(server, "POST", "/api/notes", {"type": "paper", "title": t})["id"] for t in topics]
    for p in linked:
        api(server, "POST", f"/api/notes/{q}/link", {"target": p})
    others = [api(server, "POST", "/api/notes", {"type": "paper", "title": t})["id"] for t in (
        "Spacecraft charging in geostationary orbit", "Langmuir probe interpretation", "Vacuum facility backpressure effects",
        "Propellant feed system design", "Thrust stand calibration methods", "Radiation tolerant power processing",
        "Mission analysis for orbit raising", "Optical emission spectroscopy diagnostics")]
    for a, b in ((others[0], others[1]), (others[0], linked[3]), (others[2], linked[6])):
        api(server, "POST", f"/api/notes/{a}/link", {"target": b})
    page = open_app(ctx, server)
    page.wait_for_function("(n) => window.__rb.S.nodes.size >= n", arg=17)
    page.wait_for_function("() => window.__rb.S.links.filter(l => l.kind === 'manual' && typeof l.source === 'object').length >= 11")
    # put every dot that isn't on a line right onto the middle of one, then let the map settle
    page.evaluate("""() => { const rb = window.__rb, S = rb.S;
      const lines = S.links.filter(l => l.kind !== 'part' && l.kind !== 'similar' && typeof l.source === 'object');
      if (!lines.length) throw new Error('no lines: ' + JSON.stringify(S.links.map(l => l.kind)));
      let i = 0;
      for (const n of S.nodes.values()) {
        if (lines.some(l => l.source.id === n.id || l.target.id === n.id)) continue;
        const g = rb.linkGeom(lines[i++ % lines.length]); if (!g) continue;
        n.x = (g.pts[0] + g.pts[g.pts.length - 2]) / 2; n.y = (g.pts[1] + g.pts[g.pts.length - 1]) / 2; n.vx = 0; n.vy = 0;
      }
      // lay the map out again from there: however it got there, it must end with every dot clear
      window.__stops0 = S.stops || 0; rb.relayout(); }""")
    page.wait_for_function("() => (window.__rb.S.stops || 0) > window.__stops0", timeout=15000)
    # and still: the layout can start again (new data arrives) and stop again, each time ending with settle
    page.wait_for_function("""() => { const S = window.__rb.S, now = JSON.stringify([...S.nodes.values()].map(n => [n.x, n.y]));
      const same = window.__still === now; window.__still = now; return same; }""", polling=500, timeout=20000)
    worst = page.evaluate("""() => { const rb = window.__rb, S = rb.S; let worst = [Infinity, ''];
      for (const l of S.links) {
        if (l.kind === 'part' || (l.kind === 'similar' && !(S.showSim && l.top && l.score >= S.threshold)) || (l.kind === 'cites' && !S.showCites)) continue;
        const g = rb.linkGeom(l); if (!g) continue;
        for (const n of S.nodes.values()) {
          if (n.type === 'fragment' || n.id === l.source.id || n.id === l.target.id) continue;
          let best = Infinity;
          for (let i = 0; i + 3 < g.pts.length; i += 2) {
            const [px, py, qx, qy] = g.pts.slice(i, i + 4), vx = qx - px, vy = qy - py, L = vx * vx + vy * vy;
            let t = L ? ((n.x - px) * vx + (n.y - py) * vy) / L : 0; t = Math.max(0, Math.min(1, t));
            best = Math.min(best, Math.hypot(n.x - (px + vx * t), n.y - (py + vy * t)));
          }
          const gap = best - rb.nodeRadius(n);
          if (gap < worst[0]) worst = [gap, n.id];
        }
      }
      return worst; }""")
    assert worst[0] > 0.5, worst  # every dot's edge is clear of every line it isn't on
    assert not page.errors


def test_a_click_on_the_map_closes_the_open_note_but_dragging_it_doesnt(ctx, server):
    clear_vault(server)
    nid = api(server, "POST", "/api/notes", {"type": "paper", "title": "Hollow cathode plume oscillations"})["id"]
    page = open_app(ctx, server)
    page.wait_for_function("(id) => window.__rb.S.nodes.has(id)", arg=nid)
    page.wait_for_timeout(1500)  # let the layout settle
    page.evaluate("(id) => window.__rb.openNote(id)", nid)
    expect(page.locator("#panel")).to_be_visible()
    box = page.locator("#graph").bounding_box()
    # a spot of empty map well away from the dot, left of the panel
    sx, sy = page.evaluate(f"() => {{ const n = window.__rb.S.nodes.get('{nid}'), p = window.__rb.fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }}")
    x, y = box["x"] + max(60, sx - 180), box["y"] + min(box["height"] - 60, sy + 120)
    # dragging the map around keeps the note open
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x + 80, y + 40, steps=8)
    page.mouse.up()
    page.wait_for_timeout(300)
    expect(page.locator("#panel")).to_be_visible()
    # a click on empty map closes it, and the reopen tab brings it back
    page.mouse.click(x - 30, y - 30)
    expect(page.locator("#panel")).to_be_hidden()
    page.locator(".reopen-tab").click()
    expect(page.locator("#panel")).to_be_visible()
    assert not page.errors


def test_save_now_and_nothing_lost_when_a_save_fails(ctx, server):
    """Mod+S saves at once (and the Save button with it); when a save fails, switching to another note asks
    instead of throwing the edits away; closing with unsaved edits asks too."""
    clear_vault(server)
    other = api(server, "POST", "/api/notes", {"type": "note", "title": "Another note"})["id"]
    page = open_app(ctx, server)
    nid = new_note(page, "Saving test")
    page.wait_for_function("() => !window.__rb.unsaved()")
    ta = body_editor(page)
    ta.fill("Typed and saved at once")
    expect(page.locator("#btn-save")).to_be_visible()
    page.keyboard.press("ControlOrMeta+s")
    page.wait_for_function("() => !window.__rb.unsaved()", timeout=600)  # well before the 800 ms autosave
    assert api(server, "GET", f"/api/notes/{nid}")["body"] == "Typed and saved at once"
    expect(page.locator("#btn-save")).to_be_hidden()
    # the server stops answering saves: the edit stays, and leaving the note asks
    page.route(f"**/api/notes/{nid}", lambda route: route.fulfill(status=500, body='{"detail":"disk unplugged"}',
                                                                  content_type="application/json")
               if route.request.method == "PUT" else route.continue_())
    ta.fill("This edit must not be lost")
    page.locator("#btn-save").click()
    expect(page.locator("#btn-save")).to_have_text("Try again")
    page.evaluate("(id) => { window.__rb.openNote(id); }", other)
    expect(page.locator("#modal")).to_be_visible()
    expect(page.locator("#modal-title")).to_contain_text("isn’t saved")
    page.click("#modal-cancel")  # stay on it
    expect(page.locator("#modal")).to_be_hidden()
    assert page.evaluate("window.__rb.S.cur.id") == nid and page.evaluate("window.__rb.unsaved()")
    # still failing: Try again keeps asking, nothing is dropped
    page.evaluate("(id) => { window.__rb.openNote(id); }", other)
    expect(page.locator("#modal")).to_be_visible()
    page.click("#modal-ok")
    expect(page.locator("#modal-title")).to_contain_text("isn’t saved")
    # the server is back: Try again saves it, then the other note opens
    page.unroute(f"**/api/notes/{nid}")
    page.click("#modal-ok")
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id", arg=other)
    assert api(server, "GET", f"/api/notes/{nid}")["body"] == "This edit must not be lost"
    # closing the page with an unsaved edit asks first (the browser's own box, outside the Mac window)
    body_editor(page).fill("Unsaved when closing")
    asked = []
    page.on("dialog", lambda d: (asked.append(d.type), d.dismiss()))
    page.close(run_before_unload=True)
    page.wait_for_timeout(500)
    assert asked == ["beforeunload"]
    assert not page.errors


def test_with_no_ai_set_up_the_top_bar_says_so(ctx, server):
    """No AI chosen and none installed: the top bar says "No AI set up" (not the name of one you don't have),
    and AI buttons explain how to set one up."""
    import json as _json

    def no_ai(route):
        r = route.fetch()
        st = r.json()
        st["claude"] = {"available": False, "path": None, "detail": "No AI is set up yet. Gemini is free."}
        st["agent"] = {**st.get("agent", {}), "id": "claude", "name": "Claude Code", "available": False, "none": True, "model": ""}
        route.fulfill(response=r, body=_json.dumps(st), content_type="application/json")
    page = ctx.new_page()
    page.route("**/api/status", no_ai)
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server["base"] + "/")
    expect(page.locator("#status .status-text")).to_have_text("No AI set up")
    assert "Claude" not in page.locator("#status").get_attribute("aria-label")
    assert page.evaluate("window.RB_AI") == "your AI"
    # a sentence that starts with the AI's name starts with a capital (the chat's intro said "your AI answers ...")
    assert page.evaluate("RB.AIName()") == "Your AI"
    assert page.evaluate("RB.aiText('{AI} compares it. Ask {AI} again.')") == "Your AI compares it. Ask your AI again."
    page.keyboard.press("a")
    expect(page.locator("#chat")).to_contain_text("Your AI answers from your papers and notes")
    assert "your AI answers" not in page.locator("#chat").inner_text()
    assert not page.errors




# ------------------------------------------------------------------- sections on the map (was test_sections_e2e.py)
PLASMA = "Hall thruster plasma discharge {w} ionization breathing mode oscillations anomalous electron transport xenon"
STARS = "Stellar spectra of cool dwarf stars {w} atmospheres metallicity abundances exoplanet hosts photometry"


def test_map_draws_few_lines_until_you_point(ctx, server):
    """The resting map: no line runs from section to section and no pair of papers has two lines; pointing at a paper
    shows its line across; Lines set to All shows everything. Every section's name sits right above its papers."""
    clear_vault(server)
    ids = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster oscillations {w}", "abstract": PLASMA.format(w=w)})["id"]
           for w in ("alpha", "beta", "gamma", "delta")]
    stars = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Cool dwarf atmospheres {w}", "abstract": STARS.format(w=w)})["id"]
             for w in ("one", "two", "three", "four")]
    api(server, "POST", f"/api/notes/{ids[0]}/link", {"target": stars[0]})
    api(server, "POST", f"/api/notes/{ids[0]}/link", {"target": ids[1]})
    page = open_app(ctx, server)
    page.wait_for_function("(window.__rb.S.stops || 0) >= 1 && (window.__rb.S.regionLabels || []).filter((r) => r.box).length >= 2", timeout=30000)
    drawn = """() => { const {S, fg} = window.__rb, vis = fg.linkVisibility(), id = (x) => x.id || x;
      return S.links.filter((l) => l.kind !== 'part' && vis(l)).map((l) => [l.kind, id(l.source), id(l.target),
        S.nodes.get(id(l.source)).cluster !== S.nodes.get(id(l.target)).cluster]); }"""
    across = "() => " + "(" + drawn + ")().some((l) => l[3])"
    rest = page.evaluate(drawn)
    assert not [x for x in rest if x[3]], rest
    pairs = [tuple(sorted(x[1:3])) for x in rest]
    assert len(pairs) == len(set(pairs)), rest
    assert ["manual", ids[0], ids[1], False] in rest or ["manual", ids[1], ids[0], False] in rest
    # names: above their papers, within reach of them, never over a dot
    bad = page.evaluate("""() => { const {S, fg} = window.__rb, k = fg.zoom();
      return S.regionLabels.filter((r) => { const b = r.box; if (!b) return true;
        const mem = [...S.nodes.values()].filter((n) => n.cluster === r.cid), top = Math.min(...mem.map((n) => n.y));
        const on = [...S.nodes.values()].some((n) => n.x > b[0] && n.x < b[2] && n.y > b[1] && n.y < b[3]);
        return on || b[3] > top || (top - b[3]) * k > 60; }).map((r) => r.label); }""")
    assert not bad, bad
    sx, sy = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id), p = window.__rb.fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }", ids[0])
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    page.wait_for_function(across)
    page.mouse.move(box["x"] + 5, box["y"] + box["height"] - 120)
    page.wait_for_function("() => !(" + across + ")()")
    # Show all connections, on the map: every connection without pointing at anything, and the setting follows
    page.click("#btn-lines")
    page.wait_for_function(across)
    assert page.get_attribute("#btn-lines", "aria-pressed") == "true" and page.input_value("#lines-mode") == "all"
    page.click("#btn-lines")
    page.wait_for_function("() => !(" + across + ")()")
    assert page.get_attribute("#btn-lines", "aria-pressed") == "false" and page.input_value("#lines-mode") == "few"
    page.evaluate("() => window.__rb.setLines('all')")
    page.wait_for_function(across)
    assert page.get_attribute("#btn-lines", "aria-pressed") == "true"
    page.evaluate("() => window.__rb.setLines('few')")


def test_map_names_hold_still(ctx, server):
    """Panning and zooming move no name against its dot or region: no jumping to another side, sliding along the
    window's edge or rewrapping. Panning lets no name in or out either, and zooming in only lets names in. Zoomed out
    below the whole-map view the regions' names shrink, in place and in proportion."""
    clear_vault(server)
    for w in ("alpha", "beta", "gamma", "delta", "epsilon", "zeta"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster oscillations {w}", "abstract": PLASMA.format(w=w)})
    for w in ("one", "two", "three", "four"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Cool dwarf atmospheres {w}", "abstract": STARS.format(w=w)})
    page = open_app(ctx, server)
    page.wait_for_function("(window.__rb.S.stops || 0) >= 1 && (window.__rb.S.regionLabels || []).filter((r) => r.box).length >= 2", timeout=30000)
    page.evaluate("""() => { const draw = CanvasRenderingContext2D.prototype.fillText; window.__drawn = null;
      CanvasRenderingContext2D.prototype.fillText = function (t, x, y) {
        if (window.__drawn && this.canvas.isConnected && this.globalAlpha > 0.5) { const m = this.getTransform(), d = devicePixelRatio;
          window.__drawn.push([t, (m.a * x + m.e) / d, (m.d * y + m.f) / d, this.measureText(t).width * m.a / d]); }
        return draw.apply(this, arguments); }; }""")
    frame = """async ([k, dx]) => { const {S, fg} = window.__rb, wait = (ms) => new Promise((r) => setTimeout(r, ms));
      window.__c0 = window.__c0 || fg.centerAt(); fg.zoom(k); fg.centerAt(window.__c0.x + dx, window.__c0.y);
      window.__rb.kick(900); await wait(500); window.__drawn = []; await wait(120);
      const last = {}; for (const t of window.__drawn) last[t[0]] = t; window.__drawn = null;
      const out = {};
      for (const n of S.nodes.values()) { const t = last[window.__rb.mapLabel(n)]; if (!t) continue;
        const p = fg.graph2ScreenCoords(n.x, n.y); out[n.id] = [t[1] - p.x, t[2] - p.y, t[3]]; }
      const names = new Set([...S.nodes.values()].map((n) => window.__rb.mapLabel(n)));
      for (const r of S.regionLabels) for (const t of Object.values(last)) if (!names.has(t[0]) && r.label.toLowerCase().startsWith(t[0].toLowerCase().slice(0, 6))) {
        const p = fg.graph2ScreenCoords(r.x, r.top); out['section ' + r.cid + t[0]] = [t[1] - p.x, t[2] - p.y, t[3]]; }
      return out; }"""
    k0 = page.evaluate("window.__rb.fg.zoom()")
    still = lambda a, b, tol: [(i, a[i], b[i]) for i in a if i in b and any(abs(p - q) > tol for p, q in zip(a[i], b[i]))]
    pan = [page.evaluate(frame, [k0, dx]) for dx in (0, 6, 12, -8)]
    assert any(i.startswith("section") for i in pan[0]), pan[0]
    for f in pan[1:]:
        assert not still(pan[0], f, 1.0), still(pan[0], f, 1.0)
        assert set(f) == set(pan[0])
    zoom = [page.evaluate(frame, [k0 * z, 0]) for z in (0.5, 0.7, 0.85, 1.0)]
    for a, b in zip(zoom, zoom[1:]):
        papers = lambda d: {i: v for i, v in d.items() if not i.startswith("section")}
        assert not still(papers(a), papers(b), 4.0), still(papers(a), papers(b), 4.0)  # a dot's own edge grows a little; nothing else
        assert set(a) <= set(b)
        for i in a:
            if i.startswith("section"):
                f = b[i][2] / a[i][2]
                assert f >= 0.99 and all(abs(a[i][j] * f - b[i][j]) < 2.0 for j in (0, 1)), (i, a[i], b[i])
    for f in (page.evaluate(frame, [k0 * z, 0]) for z in (1.05, 1.1)):
        assert not still({i: v for i, v in f.items() if i.startswith("section")}, {i: v for i, v in zoom[-1].items()}, 1.5) or all(
            f[i][2] >= zoom[-1][i][2] for i in f if i in zoom[-1] and i.startswith("section"))


def test_map_lines_can_be_reached(ctx, server):
    """The lines a paper brings out stay while you go to one of them: along the line, resting on it (which says what it
    is), and while its explanation is open. A paper only swept over lets go at once."""
    clear_vault(server)
    a, a2, *_ = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster oscillations {w}", "abstract": PLASMA.format(w=w)})["id"]
             for w in ("alpha", "beta", "gamma", "delta")]
    b, *_ = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Cool dwarf atmospheres {w}", "abstract": STARS.format(w=w)})["id"]
             for w in ("one", "two", "three", "four")]
    api(server, "POST", f"/api/notes/{a}/link", {"target": b})
    api(server, "POST", f"/api/notes/{a}/link", {"target": a2})
    page = open_app(ctx, server)
    page.wait_for_function("(window.__rb.S.stops || 0) >= 1 && (window.__rb.S.regionLabels || []).filter((r) => r.box).length >= 2", timeout=30000)
    page.wait_for_timeout(300)
    pts = page.evaluate("""(a) => { const rb = window.__rb, r = document.getElementById('graph').getBoundingClientRect();
      window.__l = rb.S.links.find((l) => l.kind === 'manual' && l.source.cluster !== l.target.cluster); const p = rb.linkGeom(window.__l).pts, out = [];
      for (let i = 0; i + 3 < p.length; i += 2) for (let j = 0; j <= 20; j++) { const t = j / 20,
        q = rb.fg.graph2ScreenCoords(p[i] + (p[i + 2] - p[i]) * t, p[i + 1] + (p[i + 3] - p[i + 1]) * t); out.push([q.x + r.x, q.y + r.y]); }
      return window.__l.source.id === a ? out : out.reverse(); }""", a)
    drawn = "() => window.__rb.linkDrawn(window.__l)"
    box = page.locator("#graph").bounding_box()
    away = (box["x"] + 5, box["y"] + box["height"] - 120)
    page.mouse.move(*away)
    assert not page.evaluate(drawn)
    # swept over: gone right away
    page.mouse.move(*pts[0]); page.wait_for_function(drawn); page.mouse.move(*away)
    page.wait_for_function("() => !window.__rb.linkDrawn(window.__l)")
    # rested on, then along its line
    page.mouse.move(*pts[0]); page.wait_for_function(drawn); page.wait_for_timeout(700)
    half = next(i for i, q in enumerate(pts) if ((q[0] - pts[0][0]) ** 2 + (q[1] - pts[0][1]) ** 2) ** 0.5 > 45)
    for q in pts[1:half + 1]:
        page.mouse.move(*q, steps=2); page.wait_for_timeout(25)
        assert page.evaluate(drawn)
    page.wait_for_timeout(2000)  # longer than lines are held for once you've left them
    assert page.evaluate(drawn) and page.evaluate("window.__rb.S.hoverLink === window.__l")
    page.mouse.click(*pts[half])
    expect(page.locator("#explain")).to_be_visible()
    page.mouse.move(*away); page.wait_for_timeout(1800)
    assert page.evaluate(drawn)  # its explanation is open
    page.keyboard.press("Escape")
    page.wait_for_function("() => !window.__rb.linkDrawn(window.__l)")


def test_sections_drag_and_add_into(ctx, server):
    """Sections on the map: drawn as clouds, dragged as a whole by their name tag (only that section moves, and
    it stays where you drop it), and a paper added "into a section" joins it."""
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


# --------------------------------------------------------------- diagrams in answers (was test_diagram_e2e.py)
DIAGRAMS = [
    'flowchart TD\n  A["1. Collect spectra"] --> B["2. Fit the CRM"]\n  B --> C["- check residuals"]',
    'flowchart LR\n  A[1. Measure] -->|2. compare| B(* model)\n  B --> C{# done?}',
    'mindmap\n  root((Cross sections))\n    1. Theory\n    2. Experiment',
]


def test_list_like_labels_draw(ctx, server):
    """Diagrams in answers (static/app.js drawVisuals): mermaid reads labels as Markdown, so a label that starts
    like a list or heading ("1. Fit", "- check", "# done?") used to come out as "Unsupported markdown: list".
    They draw with their own text now."""
    clear_vault(server)
    ans = "\n\n".join(f"```mermaid\n{d}\n```" for d in DIAGRAMS)
    api(server, "PUT", "/api/chats/library", {"messages": [{"role": "user", "content": "Draw it"},
                                                              {"role": "assistant", "content": ans}], "title": "Ask your library"})
    page = open_app(ctx, server)
    page.click("#btn-chat")
    page.wait_for_function("document.querySelectorAll('#chat .md-visual-mermaid svg').length === 3", timeout=20000)
    texts = page.evaluate("""() => [...document.querySelectorAll('#chat .md-visual-mermaid')].map((v) =>
      [...v.querySelectorAll('svg .nodeLabel, svg .edgeLabel, svg text')].map((t) => t.textContent).join(' ').replace(/\\u00a0/g, ' '))""")
    joined = " ".join(texts)
    assert "Unsupported markdown" not in joined
    for label in ["1. Collect spectra", "- check residuals", "2. compare", "* model", "# done?", "1. Theory"]:
        assert label in joined, label
    assert not page.errors


# ------------------------------------------------------------------------- idea cards (was test_cards_e2e.py)
TEXT_A = """[[page 1]]
Magnetic shielding strongly reduces erosion of the discharge channel walls in this thruster.

[[page 2]]
The electron temperature in the plume was 20 eV at the exit plane.

We used a Langmuir probe mounted on a radial arm for every measurement here.
"""
TEXT_B = """[[page 3]]
Ion beam focusing improved markedly when the magnetic field was optimised for this study.

[[page 4]]
The electron temperature in the plume was 35 eV at the exit plane.

A Langmuir probe measured the plasma properties across the whole plume.
"""


def _paper_with_text(server, title, text):
    n = api(server, "POST", "/api/notes", {"type": "paper", "title": title})
    (Path(server["vault"]) / "text" / f"{n['id']}.txt").write_text(text, encoding="utf-8")
    return n["id"]


def _extract_cards_now(server, pid):
    j = api(server, "POST", f"/api/papers/{pid}/cards/extract")
    t0 = time.time()
    while time.time() - t0 < 20:
        s = api(server, "GET", f"/api/cards/jobs/{j['job_id']}")
        if s["state"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("extraction didn't finish")


def test_ideas_review_flow_and_view(ctx, server):
    """Idea cards: suggest ideas from a paper (fake agent), review them with the keyboard (J/K move, Y confirm,
    X reject, E edit), add one by hand, then the Ideas view: the quantity table with a conflict badge,
    sorting, the CSV link, and Esc."""
    clear_vault(server)
    a = _paper_with_text(server, "Shielded thruster plume study", TEXT_A)
    b = _paper_with_text(server, "Optimised field plume study", TEXT_B)
    page = open_app(ctx, server)
    assert page.evaluate("RB.commands.conflicts()") == []
    page.wait_for_function("(id) => window.__rb.S.nodes.has(id)", arg=a)
    page.evaluate("(id) => window.__rb.openNote(id)", a)
    sec = page.locator("#ideas-sec")
    expect(sec).to_be_visible()
    expect(sec).to_contain_text("Suggest them from the paper, or add your own")

    # suggest: the fake agent proposes 4 cards; the one whose quote isn't in the paper is dropped
    sec.locator("[data-act=ideas-suggest]").click()
    cards = sec.locator(".ic[data-card]")
    expect(cards).to_have_count(3)
    expect(sec.locator(".ic.sug")).to_have_count(3)
    expect(sec).to_contain_text("The electron temperature in the plume was 20 eV")
    expect(sec.locator(".ic-meta .tnum", has_text="p. 2").first).to_be_visible()  # no PDF here, so not a link

    # keyboard review
    blur(page)
    page.keyboard.press("j")
    expect(cards.nth(0)).to_have_class("ic sug on")
    page.keyboard.press("y")
    expect(cards.nth(0)).to_have_attribute("data-status", "confirmed")
    # the next suggestion (the quantity) is selected; J skips to the method, X rejects it (hidden, can be shown)
    expect(sec.locator(".ic.on")).to_have_attribute("data-status", "suggested")
    expect(sec.locator(".ic.on")).to_contain_text("20 eV")
    page.keyboard.press("j")
    expect(sec.locator(".ic.on")).to_contain_text("Langmuir probe")
    page.keyboard.press("x")
    expect(sec.locator(".ic[data-card]")).to_have_count(2)
    expect(sec.get_by_role("button", name="Show 1 rejected")).to_be_visible()
    # back on the quantity: edit it (an edited suggestion counts as confirmed)
    expect(sec.locator(".ic.on")).to_contain_text("20 eV")
    page.keyboard.press("k")
    expect(sec.locator(".ic.on")).to_have_attribute("data-status", "confirmed")
    page.keyboard.press("j")
    page.keyboard.press("e")
    form = sec.locator("form.ic-form")
    expect(form).to_be_visible()
    ta = form.locator("textarea").first
    ta.fill("My wording of this idea.")
    ta.press(f"{MOD}+Enter")
    expect(form).to_have_count(0)
    expect(sec).to_contain_text("My wording of this idea.")
    statuses = sorted(sec.locator(".ic[data-card]").evaluate_all("els => els.map((e) => e.dataset.status)"))
    assert statuses == ["confirmed", "edited"], statuses

    # add a card by hand
    sec.locator("[data-act=ideas-add]").click()
    form = sec.locator("form.ic-form")
    form.locator("select").select_option("claim")
    form.locator("textarea").first.fill("The sheath is what matters most here.")
    form.get_by_role("button", name="Add card").click()
    expect(sec).to_contain_text("The sheath is what matters most here.")
    expect(sec.locator(".ic", has_text="The sheath is what matters").locator(".st")).to_have_text("Yours")
    sec.scroll_into_view_if_needed()
    shot(page, "ideas-panel")

    # the second paper disagrees (35 eV vs 20 eV): the Ideas view flags it
    assert _extract_cards_now(server, b)["added"] == 3
    blur(page)
    page.keyboard.press("g")
    page.keyboard.press("i")
    view = page.locator("section.iv")
    expect(view).to_be_visible()
    rows = view.locator("tbody tr")
    expect(rows).to_have_count(2)
    expect(view.locator(".bad", has_text="Disagrees")).to_have_count(2)
    expect(view.locator("th", has_text="SI")).to_be_visible()
    shot(page, "ideas-view")
    view.locator("th[data-col=value]").click()
    first = rows.nth(0).locator("td").nth(1)
    expect(first).to_have_text("20")
    view.locator("th[data-col=value]").click()
    expect(rows.nth(0).locator("td").nth(1)).to_have_text("35")
    href = view.locator("[data-act=ideas-csv]").get_attribute("href")
    assert href.startswith("/api/cards/export.csv?") and "kind=quantity" in href
    view.locator("input[type=search]").fill("optimised field")
    expect(rows).to_have_count(1)
    view.locator(".seg button", has_text="Claims").click()
    view.locator("input[type=search]").fill("")
    expect(view.locator("tbody tr").first).to_be_visible()
    blur(page)
    page.keyboard.press("Escape")
    expect(view).to_have_count(0)
    assert page.evaluate("RB.commands.conflicts()") == []
    assert not page.errors, page.errors


# ----------------------------------------------------- dropping a folder on the window (was test_drop_folder_e2e.py)
# a folder "Plasma" holding a.pdf, notes.txt and a subfolder "Hall" with b.pdf; and a loose c.pdf beside it
DROP = """(withLoose) => {
  const pdf = (n) => new File(['%PDF-1.4 x'], n, { type: 'application/pdf' });
  const fileEntry = (n, f) => ({ name: n, isFile: true, isDirectory: false, file: (ok) => ok(f) });
  const dirEntry = (n, kids) => ({ name: n, isFile: false, isDirectory: true, createReader() {
    let done = false; return { readEntries: (ok) => { const batch = done ? [] : kids; done = true; setTimeout(() => ok(batch), 0); } }; } });
  const hall = dirEntry('Hall', [fileEntry('b.pdf', pdf('b.pdf'))]);
  const root = dirEntry('Plasma', [fileEntry('a.pdf', pdf('a.pdf')), fileEntry('notes.txt', new File(['x'], 'notes.txt')), hall]);
  const folderFile = new File([], 'Plasma');
  const loose = pdf('c.pdf');
  const items = [{ kind: 'file', webkitGetAsEntry: () => root }];
  const files = [folderFile];
  if (withLoose) { items.push({ kind: 'file', webkitGetAsEntry: () => fileEntry('c.pdf', loose) }); files.push(loose); }
  window.__importedLoose = [];
  const ev = new Event('drop', { bubbles: true, cancelable: true });
  Object.defineProperty(ev, 'dataTransfer', { value: { types: ['Files'], files, items } });
  window.dispatchEvent(ev);
}"""
WIN_DROP = """
window.chrome = window.chrome || {};
window.chrome.webview = { __l: [], postMessage() {},
  addEventListener(t, f) { if (t === 'message') this.__l.push(f); },
  postMessageWithAdditionalObjects(msg, files) {  // as app/winwindow.py answers: where each dropped item is
    const paths = Array.from(files).map((f) => ({ name: f.name, path: 'C:\\\\Users\\\\me\\\\Documents\\\\' + f.name }));
    setTimeout(() => this.__l.forEach((f) => f({ data: { rb: { dropped: msg.rb.dropped, paths } } })), 10);
  } };"""
MAC_DROP = """
window.webkit = { messageHandlers: { rb: { postMessage(s) {  // as app/macwindow.py answers through __rbDropped
  const m = JSON.parse(s);
  if (m.dropped) setTimeout(() => window.__rbDropped({ dropped: m.dropped,
    paths: m.names.map((n) => ({ name: n, path: '/Users/me/Papers/' + n })) }), 10);
} } } };"""
SILENT_MAC = "window.webkit = { messageHandlers: { rb: { postMessage() {} } } };"  # never answers


def test_dropping_a_folder_links_it_in_the_app_window_and_imports_it_in_a_browser(ctx, server):
    """Dropping a folder on the window (static/app.js droppedFolders, static/live.js droppedPaths). In research
    brain's own window (Mac or Windows) the window says where the folder is, and it's added like Add folders of
    papers (kept where it is); in a browser, or when the window doesn't answer, its PDFs are read from the drop
    and offered like Import a folder (copied). The drop is synthetic: Playwright can't drag a real folder in,
    so its DataTransfer and directory entries are faked."""
    cases = [  # (what, query, init script, linked, copied)
        ("browser", "", "", [], [["Plasma/Hall/b.pdf", "Plasma/a.pdf"]]),
        ("Windows window", "?app=win", WIN_DROP, ["C:\\Users\\me\\Documents\\Plasma"], []),
        ("Mac window", "?app=mac", MAC_DROP, ["/Users/me/Papers/Plasma"], []),
        ("Mac window that doesn't answer", "?app=mac", SILENT_MAC, [], None),
    ]
    for what, query, init, linked, copied in cases:
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        if init:
            page.add_init_script(init)
        page.goto(server["base"] + "/" + query)
        page.wait_for_function("window.__rb && window.__rbLive && window.__rbBulk")
        page.evaluate("""() => {
          window.__linked = []; window.__copied = [];
          window.__rbBulk.linkDialog = (p) => window.__linked.push(p);
          window.__rbBulk.folderDialog = (fs) => window.__copied.push(fs.map((f) => f.webkitRelativePath));
        }""")
        page.evaluate(DROP, False)
        if linked:
            page.wait_for_function("window.__linked.length === 1")
        else:
            page.wait_for_function("window.__copied.length === 1", timeout=6000)
        assert page.evaluate("window.__linked") == linked, what
        if copied is None:  # the fallback: copied, whatever it read
            assert len(page.evaluate("window.__copied")) == 1, what
        else:
            assert [sorted(c) for c in page.evaluate("window.__copied")] == copied, what
        assert not errors, (what, errors)
        page.close()


# -------------------------------------------------------- the Windows window's title bar (was test_win_window_e2e.py)
FAKE_WEBVIEW = """
window.__sent = [];
window.chrome = window.chrome || {};
window.chrome.webview = {
  postMessage: (m) => window.__sent.push(JSON.parse(JSON.stringify(m))),
  // like WebView2: every 'message' listener hears each message
  __listeners: [],
  addEventListener(type, f) { if (type === 'message') this.__listeners.push(f); },
};
window.__rbMsg = (e) => window.chrome.webview.__listeners.forEach((f) => f(e));
"""


def _open_win(ctx, server, query="?app=win&titlebar=custom", viewport=None):
    """The page in research brain's own Windows window, in Playwright's Chromium with WebView2's channel
    (window.chrome.webview) faked."""
    page = ctx.new_page()
    if viewport:
        page.set_viewport_size(viewport)
    page.add_init_script(FAKE_WEBVIEW)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    page.goto(server["base"] + "/" + query)
    page.wait_for_function("window.__rb && window.__rb.fg !== undefined && window.__rbLive")
    return page


def _sent(page, key):
    return page.evaluate(f"window.__sent.filter((m) => m.rb && {key!r} in m.rb).map((m) => m.rb[{key!r}])")


def _win_shot(page, name):
    shot(page, name, clip={"x": 0, "y": 0, "width": 1400, "height": 120})


def test_windows_window_header_is_the_title_bar_and_the_mic_uses_voice_typing(ctx, server):
    """With no title bar (?app=win&titlebar=custom, app/winwindow.py): the header is the title bar, the window
    buttons sit at its right end and say what was clicked, the window's messages swap and dim them, and the
    header's color is sent so the window can match it; the chat's mic uses Windows voice typing. With Windows'
    own title bar (old WebView2): no buttons drawn, no drag region."""
    page = _open_win(ctx, server)
    assert page.evaluate("document.documentElement.classList.contains('win-window')")
    assert page.evaluate("location.search") == ""  # stripped, kept for reloads
    assert page.evaluate("sessionStorage.getItem('rb.winTitlebar')") == "custom"
    buttons = page.locator(".win-caption button")
    expect(buttons).to_have_count(3)
    assert [buttons.nth(i).get_attribute("aria-label") for i in range(3)] == ["Minimize", "Maximize", "Close"]
    # the header is a drag region, its buttons and fields aren't, and nothing in it sits under the window buttons
    assert page.evaluate("getComputedStyle(document.getElementById('topbar')).webkitAppRegion") == "drag"
    assert page.evaluate("getComputedStyle(document.querySelector('#topbar button')).webkitAppRegion") == "no-drag"
    assert page.evaluate("getComputedStyle(document.querySelector('.win-caption')).webkitAppRegion") == "no-drag"
    caption_left = page.evaluate("document.querySelector('.win-caption').getBoundingClientRect().left")
    right = page.evaluate("""Math.max(...[...document.querySelectorAll('#topbar *')].map((e) => e.getBoundingClientRect())
        .filter((r) => r.width && r.height).map((r) => r.right))""")
    assert right <= caption_left, (right, caption_left)
    box = buttons.nth(2).bounding_box()
    assert box["width"] == 46 and box["height"] == 52 and box["x"] + box["width"] == 1400 and box["y"] == 0
    # the header's color, for the window's edge above the page
    chrome = _sent(page, "chrome")
    assert chrome and chrome[-1].startswith("#") and len(chrome[-1]) == 7
    _win_shot(page, "win-titlebar-light")

    for i, what in enumerate(["minimize", "maximize", "close"]):
        buttons.nth(i).click()
    assert _sent(page, "caption") == ["minimize", "maximize", "close"]

    # the window says it's maximized: the middle button restores; inactive: dimmed
    page.evaluate("window.__rbMsg({data: {rb: {window: 'maximized'}}})")
    expect(buttons.nth(1)).to_have_attribute("aria-label", "Restore")
    assert buttons.nth(1).text_content() == "\ue923"  # the restore glyph (Segoe Fluent Icons)
    page.evaluate("window.__rbMsg({data: {rb: {window: 'normal'}}})")
    expect(buttons.nth(1)).to_have_attribute("aria-label", "Maximize")
    page.evaluate("window.__rbMsg({data: {rb: {active: false}}})")
    assert page.evaluate("document.documentElement.classList.contains('win-inactive')")
    page.mouse.move(700, 600)
    assert page.evaluate("getComputedStyle(document.querySelector('.wc-min')).opacity") == "0.45"
    page.evaluate("window.__rbMsg({data: {rb: {active: true}}})")
    assert not page.evaluate("document.documentElement.classList.contains('win-inactive')")

    # dark theme: the color is sent again
    before = _sent(page, "chrome")[-1]
    page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    page.wait_for_function(f"window.__sent.filter((m) => m.rb && m.rb.chrome).length > {len(_sent(page, 'chrome')) - 1}"
                           f" && window.__sent.filter((m) => m.rb && m.rb.chrome).pop().rb.chrome !== {before!r}")
    page.locator(".wc-close").hover()
    _win_shot(page, "win-titlebar-dark-close-hover")
    assert page.evaluate("getComputedStyle(document.querySelector('.wc-close')).backgroundColor") == "rgb(196, 43, 28)"

    # the chat's mic: Windows voice typing
    assert _sent(page, "dictate") == []
    page.evaluate("document.querySelector('#btn-chat').click()")
    mic = page.locator("#btn-chat-mic")
    expect(mic).to_be_visible()
    mic.click()
    assert _sent(page, "dictate") == [True]
    expect(mic).to_have_attribute("aria-pressed", "true")
    mic.click()  # stopping never presses Win+H again (a toggle that also stops by itself)
    assert _sent(page, "dictate") == [True]
    expect(mic).to_have_attribute("aria-pressed", "false")
    assert not page.errors

    # Windows' own title bar
    native = _open_win(ctx, server, "?app=win")
    assert not native.evaluate("document.documentElement.classList.contains('win-window')")
    expect(native.locator(".win-caption")).to_have_count(0)
    assert native.evaluate("getComputedStyle(document.getElementById('topbar')).webkitAppRegion") != "drag"
    assert not native.errors


def test_nothing_in_the_windows_header_sits_under_the_window_buttons(ctx, server):
    """A small laptop screen opens the window narrower (app/winwindow.py _fit): the header gives way so its
    buttons, search and status never slide under minimize, maximize and close, at every width."""
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "paper", "title": "Hall thruster oscillations", "abstract": "Breathing mode."})  # the whole header, not the empty library's
    for width, busy in ((1400, False), (1326, False), (1240, False), (1100, False), (984, False), (900, False),
                        # the header at its fullest: papers due for review and new evidence on a question (on Windows
                        # at 970px the gear covered minimize once Review showed up)
                        (1400, True), (1240, True), (1100, True), (1024, True), (984, True), (970, True), (940, True), (900, True)):
        page = _open_win(ctx, server, viewport={"width": width, "height": 700})
        page.wait_for_timeout(300)
        if busy:
            page.evaluate("""() => {
              const r = document.getElementById('btn-review'); r.hidden = false; r.querySelector('.review-n').textContent = '12';
              const b = document.querySelector('#btn-questions .oq-badge'); if (b) { b.hidden = false; b.textContent = '3'; }
            }""")
            page.wait_for_timeout(100)
        under = page.evaluate("""() => {
          const cap = document.querySelector('.win-caption').getBoundingClientRect();
          return [...document.querySelectorAll('#topbar *')].filter((e) => {
            const r = e.getBoundingClientRect(), s = getComputedStyle(e);
            if (!r.width || !r.height || s.visibility === 'hidden' || e.closest('.menu, [hidden]')) return false;
            return r.right > cap.left + 1 && r.left < cap.right && r.top < cap.bottom;
          }).map((e) => (e.id || e.className || e.tagName) + ' ' + Math.round(e.getBoundingClientRect().right));
        }""")
        assert under == [], f"under the window buttons at {width}px{' (busy)' if busy else ''}: {under}"
        shot(page, f"win-header-{width}{'-busy' if busy else ''}")
        page.close()


def test_sections_sit_close_their_papers_evenly_spaced_and_clean_up_tidies_the_map(ctx, server):
    """Sections pack close (no section far from its nearest neighbour), each section's papers are spread evenly
    over it (no two crowded, none flung out), and Clean up puts a dragged dot back into its section."""
    import math
    clear_vault(server)
    groups = {
        "Sheaths": ["Bohm criterion sheath wall", "presheath ion flow sheath wall", "secondary emission sheath wall",
                    "floating wall sheath potential", "sheath edge ion velocity wall"],
        "Language": ["transformer attention language tokens", "scaling laws language model tokens",
                     "language model pretraining tokens", "attention heads language transformer"],
        "Proteins": ["protein folding structure prediction", "protein structure amino acid folding",
                     "folding energy landscape protein"]}
    ids = {}
    for name, titles in groups.items():
        mem = [api(server, "POST", "/api/notes", {"type": "paper", "title": t, "abstract": t + " " + t})["id"] for t in titles]
        api(server, "POST", "/api/topics", {"name": name, "members": mem})
        ids[name] = mem
    page = open_app(ctx, server)
    page.wait_for_function("(n) => window.__rb.S.nodes.size >= n", arg=12)
    still = """() => { const S = window.__rb.S, now = JSON.stringify([...S.nodes.values()].map(n => [n.x, n.y]));
      const same = window.__still === now; window.__still = now; return same && !!S.stops; }"""
    page.wait_for_function(still, polling=500, timeout=20000)
    pos = lambda: page.evaluate("() => Object.fromEntries([...window.__rb.S.nodes.values()].map(n => [n.id, [n.x, n.y, n.cluster, window.__rb.nodeRadius(n)]]))")
    P = pos()

    def check(P):
        secs = {}
        for i, (x, y, c, r) in P.items():
            if c >= 0:
                secs.setdefault(c, []).append((x, y, r))
        assert len(secs) == 3, secs
        cen = {c: (sum(p[0] for p in m) / len(m), sum(p[1] for p in m) / len(m)) for c, m in secs.items()}
        rad = {c: max(math.hypot(p[0] - cen[c][0], p[1] - cen[c][1]) + p[2] for p in m) for c, m in secs.items()}
        for c in secs:  # each section's nearest neighbour is close: the room for titles and names, and no more
            gap = min(math.hypot(cen[c][0] - cen[o][0], cen[c][1] - cen[o][1]) - rad[c] - rad[o] for o in secs if o != c)
            assert gap < 110, (c, gap)
        for c, m in secs.items():  # spread over the section, loosely: none crowded, none flung out
            nn = [min(math.hypot(a[0] - b[0], a[1] - b[1]) for b in m if b is not a) for a in m]
            assert min(nn) > 2 * max(p[2] for p in m), (c, nn)
            assert max(nn) < 4 * min(nn), (c, nn)
            assert len({round(d, 1) for d in nn}) > 1, (c, nn)  # not a perfect grid
    check(P)

    # pointing at a paper lights up its connections: its lines at full strength, unrelated papers fade
    hub = ids["Language"][0]
    sx, sy = page.evaluate("(id) => { const fg = window.__rb.fg, n = window.__rb.S.nodes.get(id); const p = fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }", hub)
    box = page.locator("#graph").bounding_box()
    # like a real pointer (and the other hover tests): off the map first, then a short glide onto the dot
    page.mouse.move(5, box["y"] + 5)
    page.wait_for_timeout(300)
    page.mouse.move(box["x"] + sx, box["y"] + sy, steps=6)
    page.wait_for_function("(id) => window.__rb.S.hover === id", arg=hub)
    page.wait_for_timeout(600)
    lit = page.evaluate("""(id) => { const S = window.__rb.S, mine = new Set([id]);
      for (const l of S.links) { if (!window.__rb.fg.linkVisibility()(l)) continue;
        const a = l.source.id, b = l.target.id; if (a === id) mine.add(b); if (b === id) mine.add(a); }
      const far = [...S.nodes.values()].filter(n => !mine.has(n.id) && n.type !== 'fragment');
      return { hover: S.hover, mine: mine.size, near: [...mine].map(i => S.nodes.get(i).__a), far: far.map(n => n.__a) }; }""", hub)
    assert lit["mine"] > 1 and min(lit["near"]) > 0.9 and max(lit["far"]) < 0.5, lit
    page.mouse.move(box["x"] + 5, box["y"] + 5)

    # drag one paper far out of its section, then Clean up: it goes back in, and the map is tidy again
    victim = ids["Sheaths"][0]
    page.evaluate("""(id) => { const rb = window.__rb, n = rb.S.nodes.get(id); n.__home = [n.x + 400, n.y + 300];
      n.__userPin = true; n.__pinRel = [400, 300]; n.x += 400; n.y += 300; rb.kick(100); }""", victim)
    page.locator("#btn-cleanup").click()
    expect(page.locator(".toast", has_text="Tidied the map")).to_be_visible()
    page.wait_for_function(still, polling=500, timeout=20000)
    check(pos())
    assert not page.errors

"""End-to-end browser tests: a real server (fake Claude, TF-IDF embeddings, temp vault) driven by
Playwright + Chromium.

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
import shutil
import time
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import note_file, run_server

ROOT = Path(__file__).resolve().parents[2]

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed (pip install playwright && python -m playwright install chromium)",
                allow_module_level=True)

try:
    import reportlab  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("reportlab is not installed (needed to build a test PDF)", allow_module_level=True)

TIMEOUT = 15_000


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


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    vault = tmp_path_factory.mktemp("vault")
    with run_server(vault, log_path=vault.parent / "server.log") as srv:
        yield {"base": srv.base, "vault": vault}


def _chrome():
    for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    return None


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        kw = {"headless": not os.environ.get("RB_E2E_HEADED")}
        try:
            b = pw.chromium.launch(**kw)  # Playwright's own Chromium (CI)
        except PWError as e1:
            exe = _chrome()
            if not exe:
                pytest.skip(f"no browser for Playwright: run `python -m playwright install chromium` ({str(e1).splitlines()[0]})")
            b = pw.chromium.launch(executable_path=exe, **kw)
        yield b
        b.close()


@pytest.fixture()
def ctx(browser, server):
    # bypass_csp: Playwright evaluates wait_for_function("...") strings with eval, which the app's CSP
    # forbids. tests/e2e/test_csp_e2e.py checks the app itself without the bypass.
    c = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    c.set_default_timeout(TIMEOUT)
    external = []

    def route(r):
        url = r.request.url
        if url.startswith(server["base"]) or url.startswith(("data:", "blob:")):
            r.continue_()
        else:
            external.append(url)
            r.abort()
    c.route("**/*", route)
    c.external = external
    yield c
    c.close()
    assert not external, f"the app tried to reach the network: {external}"


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
def test_empty_state(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    expect(page.locator("#empty-state")).to_be_visible()
    expect(page.locator("#empty-state h1")).to_have_text("Start your library")
    expect(page.locator("#empty-state")).to_contain_text("Drop a PDF here, or add a paper by DOI, arXiv id or title.")
    expect(page.locator("#status .status-text")).to_contain_text("Claude")
    # real, self-hosted fonts are in use
    assert page.evaluate("document.fonts.check('500 13px \"Archivo\"')")
    assert page.evaluate("document.fonts.check('italic 500 20px \"Bodoni Moda\"')")
    assert not page.errors


def test_new_note_autosave(ctx, server):
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
    assert not page.errors


def test_latex_autocomplete(ctx, server):
    clear_vault(server)
    page = open_app(ctx, server)
    nid = new_note(page, "Math note")
    ta = body_editor(page)
    ta.click()
    ta.type("Angle $\\alph")
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


def test_pdf_import_viewer_fragment(ctx, server, tmp_path):
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


def test_chat_fake(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Hall thrusters", "body": "Breathing mode oscillations."})
    page = open_app(ctx, server)
    page.click("#btn-chat")
    expect(page.locator("#chat")).to_be_visible()
    page.locator("#chat-input").fill("What causes the breathing mode?")
    page.keyboard.press("Enter")
    log = page.locator("#chat-log")
    expect(log.locator(".msg.user")).to_contain_text("breathing mode")
    expect(log).to_contain_text("fake Claude")
    expect(log.locator(".katex").first).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#chat")).to_be_hidden()


def test_research_mode_and_memory(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "title": "Hall thrusters", "body": "Breathing mode oscillations near 20 kHz."})
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.chatMode')")
    page.click("#btn-chat")
    expect(page.locator(".rs-seg button", has_text="Quick")).to_have_attribute("aria-pressed", "true")  # Quick is the default
    expect(page.locator(".rs-notice")).to_be_hidden()
    page.locator(".rs-seg button", has_text="Research").click()
    expect(page.locator(".rs-notice")).to_contain_text("Claude can only read your library here")
    page.locator("#chat-input").fill("What frequency is the breathing mode?")
    page.keyboard.press("Enter")
    log = page.locator("#chat-log")
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


def test_search_with_slash(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "note", "title": "Langmuir probe analysis"})
    api(server, "POST", "/api/notes", {"type": "note", "title": "Transformer attention"})
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


def test_export_from_view_menu(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "note", "private": False, "title": "Langmuir probe analysis", "summary": "$T_e$ from the I-V curve."})
    api(server, "POST", "/api/notes", {"type": "note", "private": False, "title": "Transformer attention"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 2")
    page.locator("#search-input").fill("langmuir")
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


def test_settings_tabs_and_search(ctx, server):
    """Library settings: tabs keep kinds of settings apart; the search box finds a setting in any tab."""
    page = open_app(ctx, server)
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
    saved = page.evaluate("JSON.parse(localStorage.getItem('rb.pins'))")
    assert nid in saved
    # not frozen (no fixed position), but it settles where you dropped it
    page.evaluate("() => { window.__rb.fg.d3ReheatSimulation(); }")
    page.wait_for_timeout(2500)
    n = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y, n.fx === undefined, !!n.__userPin]; }", nid)
    assert n[2] and n[3]
    assert abs(n[0] - saved[nid][0]) < 15 and abs(n[1] - saved[nid][1]) < 15, (n, saved[nid])
    page.reload()
    page.wait_for_function("(id) => window.__rb && window.__rb.S.nodes.has(id)", arg=nid)
    page.wait_for_timeout(1500)
    after = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y, !!n.__userPin]; }", nid)
    assert after[2] and abs(after[0] - saved[nid][0]) < 15 and abs(after[1] - saved[nid][1]) < 15, (after, saved[nid])
    page.evaluate("RB.commands.run('graph.unpinAll')")
    assert page.evaluate("(id) => !window.__rb.S.nodes.get(id).__home", nid)
    assert not page.errors


def test_linked_dots_follow_a_dragged_dot(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Main dot"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Linked dot"})["id"]
    c = api(server, "POST", "/api/notes", {"type": "paper", "title": "Unrelated dot about something else"})["id"]
    api(server, "POST", f"/api/notes/{a}/link", {"target": b})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    page.wait_for_timeout(2500)
    pos = lambda i: page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y]; }", i)
    (bx0, by0), (cx0, cy0) = pos(b), pos(c)
    sx, sy = page.evaluate("(id) => { const fg = window.__rb.fg, n = window.__rb.S.nodes.get(id); const p = fg.graph2ScreenCoords(n.x, n.y); return [p.x, p.y]; }", a)
    k = page.evaluate("window.__rb.fg.zoom()")
    box = page.locator("#graph").bounding_box()
    page.mouse.move(box["x"] + sx, box["y"] + sy)
    page.mouse.down()
    for i in range(1, 21):
        page.mouse.move(box["x"] + sx + 10 * i, box["y"] + sy + 6 * i)
    page.mouse.up()
    moved = (200 / k, 120 / k)  # how far the main dot went, in graph units
    (bx1, by1), (cx1, cy1) = pos(b), pos(c)
    # the linked dot came most of the way; the unrelated one didn't jump with it
    assert (bx1 - bx0) > 0.5 * moved[0] and (by1 - by0) > 0.5 * moved[1], ((bx0, by0), (bx1, by1), moved)
    assert abs(cx1 - cx0) < 0.3 * moved[0], ((cx0, cy0), (cx1, cy1))
    assert not page.errors


def test_resizing_the_window_refits_the_map(ctx, server):
    clear_vault(server)
    for i in range(8):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Resize paper {i} on topic {i % 3}"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 8")
    page.wait_for_timeout(2500)
    inside = """() => { const fg = window.__rb.fg, el = document.getElementById('graph');
      return [...window.__rb.S.nodes.values()].every(n => { const p = fg.graph2ScreenCoords(n.x, n.y);
        return p.x >= 0 && p.y >= 0 && p.x <= el.clientWidth && p.y <= el.clientHeight; }); }"""
    for size in ({"width": 700, "height": 500}, {"width": 1600, "height": 1000}, {"width": 900, "height": 700}):
        page.set_viewport_size(size)
        page.wait_for_timeout(1200)  # the refit runs once resizing stops
        assert page.evaluate(inside), size
    assert not page.errors


def test_ask_about_topics_and_the_conversation_is_remembered(ctx, server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Sheath paper", "abstract": "sheath ions wall"})["id"]
    b = api(server, "POST", "/api/notes", {"type": "paper", "title": "Wall paper", "abstract": "wall emission sheath"})["id"]
    t = api(server, "POST", "/api/topics", {"name": "Sheath physics", "members": [a, b]})["slug"]
    api(server, "PUT", "/api/chats/library", {"messages": []})
    page = open_app(ctx, server)
    page.evaluate("RB.commands.run('chat.aboutTopics')")
    page.locator(".ct-sheet .ct-row", has_text="Sheath physics").click()
    page.locator(".ct-sheet .ct-acts .btn-primary").click()
    expect(page.locator("#chat .chat-title")).to_have_text("This topic")
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


def test_general_chat_and_the_web_switch(ctx, server):
    api(server, "PUT", "/api/privacy/settings", {"web_search": False})
    api(server, "PUT", "/api/chats/general", {"messages": []})
    page = open_app(ctx, server)
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
    api(server, "PUT", "/api/privacy/settings", {"web_search": False})
    assert not page.errors


def test_general_chat_attachments_and_the_one_time_warning(ctx, server):
    clear_vault(server)
    api(server, "PUT", "/api/privacy/settings", {"web_search": True, "web_with_attachments": False})
    api(server, "PUT", "/api/chats/general", {"messages": []})
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Sheath formation at a floating wall", "abstract": "Bohm criterion"})["id"]
    n = api(server, "POST", "/api/notes", {"type": "note", "title": "My presheath idea", "body": "an idea"})["id"]
    api(server, "PUT", f"/api/notes/{n}", {"private": False})
    page = open_app(ctx, server)
    page.evaluate("localStorage.removeItem('rb.generalAttach')")
    page.evaluate("RB.commands.run('chat.general')")
    page.click("#cs-attach")
    page.fill(".ca-sheet .ca-q", "sheath")
    page.locator(".ca-sheet .ca-row", has_text="Sheath formation").click()
    page.click(".ca-sheet button:has-text('Done')")
    expect(page.locator("#cs-chips .cs-chip")).to_have_count(1)
    expect(page.locator("#chat-general .cs-note")).to_contain_text("item you attached")
    # web + a published paper: no warning
    page.check("#cs-web")
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


def test_zooming_in_stops_only_when_nothing_would_be_left_on_screen(ctx, server):
    clear_vault(server)
    for t in ("Zoom paper one", "Zoom paper two", "Zoom paper three"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    page.wait_for_timeout(2500)
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
    sheet = page.locator(".ct-sheet")
    go = sheet.locator(".ct-acts .btn-primary")
    expect(go).to_be_disabled()
    sheet.locator("#ct-all").check()
    expect(sheet.locator(".ct-list input:checked")).to_have_count(2)
    expect(go).to_have_text("Ask about all 2 topics (3 papers)")
    sheet.locator(".ct-row", has_text="Alpha topic").locator("input").uncheck()
    assert page.evaluate("document.getElementById('ct-all').indeterminate")
    expect(go).to_have_text("Ask about 1 topic (2 papers)")
    sheet.locator("#ct-all").check()
    go.click()
    expect(page.locator("#chat-scope .cs-title")).to_contain_text("Alpha topic")
    expect(page.locator("#chat-scope .cs-title")).to_contain_text("Beta topic")
    expect(page.locator("#chat .chat-title")).to_have_text("These topics")
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


def test_moving_to_a_screen_with_another_pixel_density(ctx, server):
    """Dragging the window to a Retina or a regular monitor changes the pixel density, not the size: the map's
    canvas must follow, or it draws at the wrong scale and the pointer finds the wrong dots."""
    clear_vault(server)
    for t in ("Density paper one", "Density paper two"):
        api(server, "POST", "/api/notes", {"type": "paper", "title": t})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 2")
    page.wait_for_timeout(2500)  # let the layout settle
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


def test_resizing_never_flashes_a_blank_map(ctx, server):
    """Each step of a window resize used to wipe the map until the next frame (a flicker). Right after the
    app handles a resize, the canvas must still show the map."""
    clear_vault(server)
    for i in range(8):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Flash paper {i} topic {i % 3}"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 8")
    page.wait_for_timeout(2500)
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
    assert not page.errors


def test_resizing_back_and_forth_doesnt_shrink_the_map(ctx, server):
    """Shrinking the window and growing it back must leave the map as it was: step-by-step zoom factors used to
    ratchet it smaller with every resize until it almost disappeared."""
    clear_vault(server)
    for i in range(8):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Ratchet paper {i} topic {i % 3}"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 8")
    page.wait_for_timeout(2500)
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
    assert page.evaluate("""() => { const fg = window.__rb.fg, el = document.getElementById('graph');
      return [...window.__rb.S.nodes.values()].every(n => { const p = fg.graph2ScreenCoords(n.x, n.y);
        return p.x >= 0 && p.y >= 0 && p.x <= el.clientWidth && p.y <= el.clientHeight; }); }""")
    assert not page.errors

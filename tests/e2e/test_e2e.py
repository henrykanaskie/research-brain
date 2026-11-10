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

from rb_testserver import run_server

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
    assert (server["vault"] / "notes" / f"{nid}.md").read_text().count("Bohm speed") == 1
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
    assert api(server, "GET", f"/api/notes/{p['id']}")["critique"]["text"]


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
    api(server, "POST", "/api/notes", {"type": "note", "title": "Langmuir probe analysis", "summary": "$T_e$ from the I-V curve."})
    api(server, "POST", "/api/notes", {"type": "note", "title": "Transformer attention"})
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
    html = path.read_text()
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
    assert not (server["vault"] / "notes" / f"{n['id']}.md").exists()
    assert (server["vault"] / ".trash" / f"{n['id']}.md").exists() or list((server["vault"] / ".trash").rglob(f"{n['id']}*"))

"""Phones and tablets (static/mobile.js, the 900px-and-narrower CSS in static/style.css), the installable web app
(static/sw.js, /share) and device pairing in a real browser (app/pairing.py).

The phone is Playwright's iPhone 14 and Pixel 7 descriptors (touch, small viewport, mobile user agent) and an
iPad-sized touch viewport. Pairing uses a second host name, phone.test, mapped to 127.0.0.1 inside Chromium: the
server sees a loopback peer but a non-loopback Host, which is exactly what a phone behind `tailscale serve` looks
like, so it must pair first.
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import run_server

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

TIMEOUT = 15_000
PHONE_HOST = "phone.test"


def call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    vault = tmp_path_factory.mktemp("mvault")
    env = {"RB_ALLOWED_HOSTS": PHONE_HOST, "RB_WATCH": "0", "RB_MEMORY_WATCH": "0"}
    with run_server(vault, env=env, log_path=vault.parent / "mserver.log") as srv:
        base = srv.base
        ids = []
        for i, (t, st) in enumerate([("Hall thruster breathing mode", "reading"), ("Bohm criterion in collisional sheaths", "to-read"),
                                     ("Krypton collisional-radiative model", "to-read"), ("Physics-informed neural networks", "read"),
                                     ("A very long title about anomalous electron transport in magnetically shielded Hall thrusters on krypton", "later")]):
            n = call(base, "POST", "/api/notes", {"type": "paper", "title": t, "authors": ["A. Author", "B. Coauthor"], "year": 2010 + i,
                                                  "status": st, "summary": f"My take on {t}: the growth rate is $\\gamma \\propto \\sqrt{{n_e}}$ and "
                                                  "$$\\frac{\\partial n}{\\partial t} + \\nabla\\cdot(n\\mathbf{v}) = S_{iz} - \\frac{n}{\\tau_w} + \\sum_{k=1}^{N}\\alpha_k e^{-E_k/T_e} + \\beta n^2 + \\gamma n^3$$",
                                                  "abstract": "We study the mode. " * 20})
            ids.append(n["id"])
        call(base, "POST", "/api/notes", {"type": "note", "title": "Questions for Thursday", "body": "Ask about probe data."})
        yield {"base": base, "port": srv.port, "ids": ids, "phone": f"http://{PHONE_HOST}:{srv.port}"}


@pytest.fixture(scope="module")
def pw():
    with sync_playwright() as p:
        yield p


@pytest.fixture(scope="module")
def browser(pw):
    kw = {"headless": not os.environ.get("RB_E2E_HEADED"), "args": [f"--host-resolver-rules=MAP {PHONE_HOST} 127.0.0.1"]}
    try:
        b = pw.chromium.launch(**kw)
    except PWError as e:
        exe = next((p for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium") if p and Path(p).exists()), None)
        if not exe:
            pytest.skip(f"no browser for Playwright ({str(e).splitlines()[0]})")
        b = pw.chromium.launch(executable_path=exe, **kw)
    yield b
    b.close()


def context(browser, pw, server, device=None, **kw):
    opts = dict(pw.devices[device]) if device else {}
    opts.update(kw)
    c = browser.new_context(bypass_csp=True, **opts)
    c.set_default_timeout(TIMEOUT)
    external = []

    def route(r):
        u = r.request.url
        if u.startswith((server["base"], server["phone"], "data:", "blob:")):
            r.continue_()
        else:
            external.append(u)
            r.abort()
    c.route("**/*", route)
    c.external = external
    return c


def open_app(c, server, path="/"):
    page = c.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    page.goto(server["base"] + path)
    page.wait_for_function("window.__rbMobile && document.body.classList.contains('rb-m') && document.querySelectorAll('.m-row').length > 0"
                           " || (window.__rbMobile && !document.body.classList.contains('rb-m') && window.__rb.S.nodes.size > 0)")
    return page


PHONES = ["iPhone 14", "Pixel 7"]


def no_hscroll(page, what):
    w = page.evaluate("[document.documentElement.scrollWidth, document.body.scrollWidth, window.innerWidth]")
    assert max(w[0], w[1]) <= w[2] + 1, f"{what}: the page scrolls sideways ({w})"
    wide = page.evaluate("""[...document.querySelectorAll('.m-view:not([hidden]), #panel:not([hidden]), #chat:not([hidden]), .rv')]
        .filter(e => e.scrollWidth > e.clientWidth + 1 && getComputedStyle(e).overflowX !== 'auto').map(e => e.id || e.className)""")
    assert not wide, f"{what}: content wider than its view: {wide}"


@pytest.mark.parametrize("device", PHONES)
def test_tab_bar_navigation(browser, pw, server, device):
    c = context(browser, pw, server, device)
    page = open_app(c, server)
    tabs = page.locator("#m-tabs .m-tab")
    expect(tabs).to_have_count(5)
    expect(page.locator("#m-library")).to_be_visible()
    expect(page.locator("#m-title")).to_have_text("Library")
    expect(page.locator(".m-row-title", has_text="Bohm criterion")).to_be_visible()
    # desktop chrome is out of the way
    expect(page.locator("#search-input")).to_be_hidden()
    expect(page.locator("#btn-chat")).to_be_hidden()
    no_hscroll(page, "library")

    page.locator(".m-tab[data-tab=graph]").tap()
    expect(page.locator("#m-library")).to_be_hidden()
    expect(page.locator("#graph canvas").first).to_be_visible()
    expect(page.locator("#m-title")).to_have_text("Graph")
    no_hscroll(page, "graph")

    page.locator(".m-tab[data-tab=add]").tap()
    expect(page.locator("#m-add")).to_be_visible()
    expect(page.locator("#m-link")).to_be_visible()
    no_hscroll(page, "add")

    page.locator(".m-tab[data-tab=ask]").tap()
    expect(page.locator("#chat")).to_be_visible()
    box = page.locator("#chat").bounding_box()
    vw = page.viewport_size["width"]
    assert box["x"] == 0 and abs(box["width"] - vw) < 2
    no_hscroll(page, "ask")

    page.locator(".m-tab[data-tab=library]").tap()
    expect(page.locator("#chat")).to_be_hidden()
    expect(page.locator("#m-library")).to_be_visible()
    # filters: To read shows the triage buttons, and they work
    page.locator(".m-chip[data-f='to-read']").tap()
    rows = page.locator(".m-row")
    expect(rows).to_have_count(2)
    page.locator(".m-row", has_text="Krypton").locator(".m-triage button", has_text="Later").tap()
    expect(page.locator(".m-row")).to_have_count(1)
    expect(page.locator(".toast", has_text="Parked for later")).to_be_visible()
    kr = [n for n in call(server["base"], "GET", "/api/graph")["nodes"] if n["title"].startswith("Krypton")][0]
    assert kr["status"] == "later"
    call(server["base"], "PUT", f"/api/notes/{kr['id']}", {"status": "to-read"})
    assert not page.errors and not c.external
    c.close()


@pytest.mark.parametrize("device", PHONES)
def test_note_opens_as_sheet_and_swipes_closed(browser, pw, server, device):
    c = context(browser, pw, server, device)
    page = open_app(c, server)
    page.locator(".m-chip[data-f='all']").tap()
    page.locator(".m-row-main", has_text="A very long title").tap()
    panel = page.locator("#panel")
    expect(panel).to_be_visible()
    expect(page.locator(".title-input")).to_have_value("A very long title about anomalous electron transport in magnetically shielded Hall thrusters on krypton")
    vw, vh = page.viewport_size["width"], page.viewport_size["height"]
    # the sheet slides up; measure it once it has settled (slower machines catch it mid-transition)
    page.wait_for_function("""(() => { const r = document.querySelector('#panel').getBoundingClientRect();
        return Math.abs(r.bottom - innerHeight) < 2 && r.width > 0; })()""", timeout=5000)
    box = panel.bounding_box()
    assert box["x"] == 0 and abs(box["width"] - vw) < 2 and box["y"] < 60 and abs(box["y"] + box["height"] - vh) < 2
    # the title isn't clipped
    page.wait_for_function("(() => { const t = document.querySelector('.title-input'); return t.scrollHeight <= t.clientHeight + 2; })()", timeout=5000)
    # reading text: 17px STIX; display maths scrolls inside itself
    assert page.evaluate("getComputedStyle(document.querySelector('#panel .md') || document.querySelector('#panel .field')).fontSize") == "17px"
    no_hscroll(page, "note sheet")
    # drag the sheet's top edge down: it closes
    head = page.locator("#panel .panel-head").bounding_box()
    x, y = head["x"] + head["width"] / 2, head["y"] + 10
    page.mouse.move(x, y)
    page.mouse.down()
    page.mouse.move(x, y + 120, steps=6)
    page.mouse.move(x, y + 260, steps=6)
    page.mouse.up()
    expect(panel).to_be_hidden()
    expect(page.locator(".reopen-tab")).to_be_hidden()
    assert not page.errors
    c.close()


def test_share_target_saves_a_link(browser, pw, server):
    c = context(browser, pw, server, "iPhone 14")
    page = open_app(c, server, "/share?title=Sheath%20article&text=&url=https%3A%2F%2Fexample.org%2Farticles%2Fsheath-physics")
    expect(page.locator("#m-add")).to_be_visible()
    expect(page.locator(".m-shared")).to_contain_text("Sheath article")
    expect(page.locator("#m-link")).to_have_value("https://example.org/articles/sheath-physics")
    assert page.evaluate("location.pathname") == "/"  # the shared data is out of the address bar
    page.locator(".m-cap button[type=submit]", has_text="Save to read").tap()
    expect(page.locator("#panel")).to_be_visible(timeout=30_000)
    nodes = call(server["base"], "GET", "/api/graph")["nodes"]
    saved = [n for n in nodes if "sheath" in (n.get("url") or "") + n["title"].lower() and n["status"] == "to-read"]
    assert saved, [(n["title"], n.get("url"), n["status"]) for n in nodes]
    # a quick note from the Add tab
    page.locator("#btn-panel-close").tap()
    page.locator(".m-tab[data-tab=add]").tap()
    page.fill("#m-note", "Check the Mach number\nagainst our probe data")
    page.locator(".m-cap button[type=submit]", has_text="Save note").tap()
    expect(page.locator(".title-input")).to_have_value("Check the Mach number")
    assert not page.errors and not c.external
    c.close()


def test_review_with_touch(browser, pw, server):
    c = context(browser, pw, server, "Pixel 7")
    page = open_app(c, server)
    page.locator(".m-tab[data-tab=review]").tap()
    rv = page.locator(".rv")
    expect(rv).to_be_visible()
    expect(page.locator(".m-tab[data-tab=review]")).to_have_class("m-tab on")
    expect(page.locator(".rv textarea")).to_be_visible()
    page.locator(".rv textarea").fill("Ionization front oscillates")
    page.locator(".rv button", has_text="Reveal").tap()
    grade = page.locator(".rv-grade").nth(2)
    expect(grade).to_be_visible()
    assert grade.bounding_box()["height"] >= 44
    no_hscroll(page, "review")
    grade.tap()
    expect(page.locator(".rv textarea, .rv-done").first).to_be_visible()
    # the tab bar stays reachable: Library leaves review
    page.locator(".m-tab[data-tab=library]").tap()
    expect(rv).to_be_hidden()
    expect(page.locator("#m-library")).to_be_visible()
    assert not page.errors
    c.close()


@pytest.mark.parametrize("device", PHONES)
def test_touch_targets_and_no_keyboard_ui(browser, pw, server, device):
    c = context(browser, pw, server, device)
    page = open_app(c, server)
    assert page.evaluate("document.body.classList.contains('rb-touch')")

    def small(sel):
        return page.evaluate(f"""[...document.querySelectorAll({sel!r})].filter(e => e.offsetParent || getComputedStyle(e).position === 'fixed')
            .map(e => e.getBoundingClientRect()).filter(r => r.width && (r.height < 44 || r.width < 44)).length""")
    assert small("#m-tabs .m-tab") == 0
    assert small(".m-row-main") == 0
    assert small("#m-search") == 0
    page.locator(".m-tab[data-tab=add]").tap()
    assert small(".m-cap > .btn, .m-cap-item, #m-link") == 0
    page.locator(".m-tab[data-tab=library]").tap()
    page.locator(".m-row-main").first.tap()
    expect(page.locator("#panel")).to_be_visible()
    assert small("#btn-panel-close") == 0
    page.locator("#btn-panel-close").tap()
    # no keyboard hints anywhere; the View menu offers gesture tips instead of shortcuts
    assert page.evaluate("[...document.querySelectorAll('kbd')].filter(k => k.offsetParent).length") == 0
    page.locator("#btn-view-menu").tap()
    expect(page.locator("#btn-shortcuts")).to_be_hidden()
    expect(page.locator("#btn-tips")).to_be_visible()
    page.locator("#btn-tips").tap()
    expect(page.locator(".m-tips")).to_contain_text("Tap a dot to select it")
    c.close()


def test_ipad_layout(browser, pw, server):
    c = context(browser, pw, server, viewport={"width": 820, "height": 1180}, has_touch=True, is_mobile=True, device_scale_factor=2)
    page = open_app(c, server)
    expect(page.locator("#m-tabs")).to_be_visible()
    page.locator(".m-row-main").first.tap()
    box = page.locator("#panel").bounding_box()
    assert box["width"] <= 762 and box["x"] > 20  # a centred column, the field shows at its sides
    no_hscroll(page, "ipad note")
    c.close()


def test_desktop_layout_unchanged_at_1440(browser, pw, server):
    c = context(browser, pw, server, viewport={"width": 1440, "height": 900})
    page = open_app(c, server)
    assert not page.evaluate("document.body.classList.contains('rb-m')")
    assert not page.evaluate("document.body.classList.contains('rb-touch')")
    for sel in ("#m-tabs", "#m-library", "#m-add", "#m-title"):
        assert page.locator(sel).count() == 0 or not page.locator(sel).is_visible(), sel
    for sel in ("#search-input", "#btn-add-menu", "#btn-view-menu", "#btn-chat", "#legend", ".brand-name"):
        expect(page.locator(sel)).to_be_visible()
    assert page.locator("#topbar").bounding_box()["height"] == 52
    page.evaluate(f"window.__rb.openNote({server['ids'][0]!r})")
    expect(page.locator("#panel")).to_be_visible()
    box = page.locator("#panel").bounding_box()
    assert box["x"] + box["width"] == 1440 and box["y"] == 52 and 330 <= box["width"] <= 1080
    assert page.evaluate("getComputedStyle(document.querySelector('#panel .md, #panel .field')).fontSize") in ("15px", "16px")
    expect(page.locator(".kbd-hint")).to_be_visible()
    c.close()


def test_service_worker_caches_only_the_app_shell(browser, pw, server):
    c = context(browser, pw, server, viewport={"width": 1280, "height": 800})
    page = open_app(c, server)
    page.wait_for_function("navigator.serviceWorker && navigator.serviceWorker.controller || (navigator.serviceWorker.ready.then(() => 0), false)", timeout=1000) if False else None
    page.evaluate("navigator.serviceWorker.ready")
    page.reload()
    page.wait_for_function("navigator.serviceWorker.controller !== null")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size > 0")
    nid = server["ids"][0]
    page.evaluate(f"Promise.all([fetch('/api/graph'), fetch('/api/notes/{nid}'), fetch('/api/status'), fetch('/static/style.css')])")
    page.wait_for_timeout(500)
    urls = page.evaluate("(async () => { const out = []; for (const k of await caches.keys()) { const c = await caches.open(k);"
                         " for (const r of await c.keys()) out.push(new URL(r.url).pathname); } return out; })()")
    assert any(u.startswith("/static/") for u in urls), urls
    assert all(u.startswith("/static/") for u in urls), urls
    assert not [u for u in urls if u.startswith(("/api/", "/pdf/", "/attachments/")) or u == "/"], urls
    manifest = page.evaluate("fetch('/manifest.webmanifest').then(r => r.json())")
    assert manifest["share_target"]["action"] == "/share"
    assert {i["sizes"] for i in manifest["icons"]} >= {"192x192", "512x512"}
    c.close()


def test_pairing_a_phone(browser, pw, server):
    phone = context(browser, pw, server, "iPhone 14")
    p = phone.new_page()
    # unpaired: a friendly page, no app, no data
    r = p.goto(server["phone"] + "/")
    assert r.status == 401
    expect(p.locator("h1")).to_have_text("Pair this device from your computer")
    assert p.evaluate("fetch('/api/graph').then(r => r.status)") == 401
    # the computer makes a code (Library settings, Phones and tablets) and shows a QR code
    laptop = context(browser, pw, server, viewport={"width": 1280, "height": 800})
    lp = open_app(laptop, server)
    lp.evaluate("RB.commands.run('library.settings')")
    sec = lp.locator("#ls-devices")
    expect(sec).to_contain_text("No paired devices")
    sec.locator("button", has_text="Pair a phone").click()
    expect(sec.locator(".m-qr svg")).to_be_visible()
    url = sec.locator(".m-pair-url").text_content()
    assert url.startswith(server["phone"] + "/pair?code=")
    # the phone opens it and pairs
    p.goto(url)
    p.fill("#name", "Test iPhone")
    p.locator("#go").tap()
    p.wait_for_url(server["phone"] + "/")
    p.wait_for_function("window.__rbMobile && document.querySelectorAll('.m-row').length > 0")
    assert p.evaluate("document.body.classList.contains('rb-remote')")
    # the code works once
    other = context(browser, pw, server, "Pixel 7")
    o = other.new_page()
    o.goto(url)
    o.locator("#go").tap()
    expect(o.locator("#msg")).to_contain_text("expired or was already used")
    assert o.evaluate("fetch('/api/graph').then(r => r.status)") == 401
    # lite: computer-only things are hidden or refused on the phone
    p.evaluate("RB.commands.run('library.settings')")
    expect(p.locator("#ls-devices")).to_contain_text("is paired with your computer")
    assert p.evaluate("fetch('/api/pair/start', {method: 'POST'}).then(r => r.status)") == 403
    assert p.evaluate("fetch('/api/devices').then(r => r.status)") == 403
    expect(p.locator("#btn-zotero")).to_be_hidden()
    # revoke from the computer: the phone is locked out at once
    expect(sec.locator("li", has_text="Test iPhone")).to_be_visible(timeout=10_000)
    lp.locator("#ls-devices li", has_text="Test iPhone").locator("button", has_text="Revoke").click()
    lp.locator("#modal-ok").click()
    expect(sec).to_contain_text("No paired devices")
    r = p.goto(server["phone"] + "/")
    assert r.status == 401
    # this computer never needed any of it
    assert call(server["base"], "GET", "/api/device")["local"] is True
    for c in (phone, laptop, other):
        c.close()

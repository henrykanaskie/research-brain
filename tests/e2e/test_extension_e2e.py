"""The Chrome extension, loaded unpacked into real Chrome (headless=new), against a real research_brain
server (fake Claude, TF-IDF, temp vault) and a local "paper" page.

Covers annotate mode on an HTML page (select → toolbar → Highlight / Highlight + note with LaTeX → save;
reload → highlights re-anchor, also after the page text shifts), the side list, Copy citation, the popup
(paper, note count, annotate button, no-AI footer) and the PDF flow (popup → /api/import/url (mocked) →
viewer tab, whose toolbar matches the extension's).

Run:   python -m pytest -q tests/e2e/test_extension_e2e.py
Needs: Playwright + a Chrome that can load extensions (system Chrome at /usr/bin/google-chrome, or
       RB_E2E_CHROME). Skipped when extensions can't be loaded. RB_E2E_SHOTS=<dir> saves screenshots.
"""
from __future__ import annotations

import contextlib
import http.server
import io
import json
import os
import re
import shutil
import signal
import threading
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import run_server

ROOT = Path(__file__).resolve().parents[2]
EXT_SRC = ROOT / "extension"

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

TIMEOUT = 15_000
SHOTS = os.environ.get("RB_E2E_SHOTS")


def _chrome():
    for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    return None


def shot(page, name, **kw):
    if SHOTS:
        Path(SHOTS).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(SHOTS) / f"{name}.png"), **kw)


PAPER_HTML = """<!doctype html><html><head><meta charset="utf-8"><title>[2401.00001] Sheath physics, a primer</title>
<meta name="citation_title" content="Sheath physics, a primer">
<style>body{font:17px/1.6 Georgia,serif;max-width:680px;margin:40px auto;color:#222}</style></head>
<body>{extra}<h1>Sheath physics, a primer</h1>
<h2>2. The Bohm criterion</h2>
<p id="p1">In a collisionless plasma, <span id="t1">the Bohm criterion requires ions to enter the sheath at the sound speed</span>,
which sets the potential drop across the presheath.</p>
<p id="p2">The ion sound speed is <span id="t2">c<sub>s</sub> = (k T<sub>e</sub> / m<sub>i</sub>)<sup>1/2</sup></span>
and the sheath is a few <span id="t3">Debye lengths thick</span>.</p>
<h2>3. Floating potential</h2>
<p id="p3">A floating wall charges negatively until <span id="t4">the electron and ion fluxes balance</span>.</p>
</body></html>"""


class Site:
    """A tiny local web server whose page text the test can change between loads."""

    def __init__(self, port):
        self.pages = {}
        site = self

        class H(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                body, ctype = site.pages.get(self.path.split("?")[0], (None, None))
                if body is None:
                    self.send_response(404); self.end_headers(); return
                self.send_response(200)
                self.send_header("Content-Type", ctype)
                self.send_header("Content-Length", str(len(body)))
                self.end_headers()
                self.wfile.write(body)

            def log_message(self, *a):
                pass

        self.httpd = http.server.ThreadingHTTPServer(("127.0.0.1", port), H)
        self.httpd.daemon_threads = True
        self.port = self.httpd.server_address[1]
        self.base = f"http://127.0.0.1:{self.port}"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()

    def html(self, path, text):
        self.pages[path] = (text.encode(), "text/html; charset=utf-8")

    def close(self):
        self.httpd.shutdown()
        self.httpd.server_close()


def _pdf_bytes(title="Probe theory zqv test document", pages=1) -> bytes:
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(title)
    for i in range(pages):
        c.setFont("Helvetica", 16)
        c.drawString(72, 700, title)
        c.setFont("Helvetica", 13)
        c.drawString(72, 660, "Langmuir probes measure the electron temperature from the I-V curve." + (f" Page {i + 1}." if pages > 1 else ""))
        c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture(scope="module")
def env(tmp_path_factory):
    if not _chrome():
        pytest.skip("no Chrome that can load extensions (set RB_E2E_CHROME)")
    tmp = tmp_path_factory.mktemp("ext")
    vault = tmp / "vault"
    with contextlib.ExitStack() as stack:  # everything started here is torn down, whatever happens
        srv = stack.enter_context(run_server(vault, log_path=tmp / "server.log",
                                             env={"RB_CITES_ON_IMPORT": "0", "RB_WATCH": "0"}))
        base = srv.base
        site = Site(0)  # any free port
        stack.callback(site.close)
        site.html("/paper.html", PAPER_HTML.replace("{extra}", ""))
        site.pages["/probe.pdf"] = (_pdf_bytes(), "application/pdf")
        # a publisher article page (citation_* tags, numbered sections) whose PDF is on the same site
        article = (ROOT / "tests" / "fixtures" / "linked" / "publisher.html").read_text().replace("https://pubs.example.org", site.base)
        site.html("/doi/10.1063/1.4945558", article)
        site.pages["/doi/pdf/10.1063/1.4945558"] = (_pdf_bytes("Breathing modes zqk article PDF"), "application/pdf")
        site.pages["/linked.pdf"] = (_pdf_bytes("Linked PDF zqw text only", pages=2), "application/pdf")

        # a copy of the extension whose host permission names the test server's port
        ext = tmp / "extension"
        shutil.copytree(EXT_SRC, ext)
        m = json.loads((ext / "manifest.json").read_text())
        # In real use `activeTab` grants the current tab when you click the button or press Alt+Shift+A. Playwright
        # can't press the toolbar button, so the test copy also gets the local test site as a host permission
        # (the shipped manifest has none; tests/test_annotate.py checks that).
        # (and the two sites whose tab URL picks the Cite tab's main button; their pages are served by a route)
        m["host_permissions"] = [f"{base}/*", f"{site.base}/*", "https://www.overleaf.com/*", "https://docs.google.com/*"]
        (ext / "manifest.json").write_text(json.dumps(m))
        # the shipped UI lives in a closed shadow root (page scripts can't read your notes); Playwright
        # can only look inside open ones
        ann = ext / "annotate.js"
        assert 'attachShadow({ mode: "closed" })' in ann.read_text()
        ann.write_text(ann.read_text().replace('attachShadow({ mode: "closed" })', 'attachShadow({ mode: "open" })'))

        pw = sync_playwright().start()
        stack.callback(pw.stop)
        profile = str(tmp / "profile")
        try:
            ctx = pw.chromium.launch_persistent_context(
                profile, executable_path=_chrome(), headless=False,
                ignore_default_args=["--disable-extensions"], viewport={"width": 1280, "height": 860},
                args=["--headless=new", f"--disable-extensions-except={ext}", f"--load-extension={ext}", "--no-sandbox"])
        except Exception as e:  # pragma: no cover
            pytest.skip(f"Chrome couldn't load the unpacked extension here: {e}")
        ctx._rb_profile = profile
        stack.callback(_close_context, ctx)
        try:
            sw = ctx.service_workers[0] if ctx.service_workers else ctx.wait_for_event("serviceworker", timeout=10_000)
        except Exception as e:  # pragma: no cover
            pytest.skip(f"Chrome couldn't load the unpacked extension here: {e}")
        ctx.set_default_timeout(TIMEOUT)
        sw.evaluate(f"chrome.storage.local.set({{server: {json.dumps(base)}}})")
        external = []

        def route(r):  # nothing may leave this machine
            u = r.request.url
            # chrome:// = the browser's own PDF viewer resources (a PDF tab), not the network
            if u.startswith(("http://127.0.0.1", "chrome-extension://", "chrome://", "data:", "blob:", "about:")):
                r.continue_()
            else:
                external.append(u)
                r.abort()
        ctx.route("**/*", route)
        yield {"ctx": ctx, "sw": sw, "base": base, "site": site, "ext_id": sw.url.split("/")[2], "external": external,
               "vault": vault}


def _close_context(ctx, timeout=20.0):
    """Close the persistent context, but never wait forever: Chrome can stop answering CDP (seen when an
    extension iframe is torn down mid key event), and BrowserContext.close() has no timeout of its own.
    After `timeout` s the browser processes are killed, so the Playwright driver gets its answer."""
    browser_pids = _chrome_pids(ctx)
    done = threading.Event()
    t = threading.Timer(timeout, lambda: done.is_set() or _kill(browser_pids))
    t.daemon = True
    t.start()
    try:
        ctx.close()
    except Exception:
        pass
    finally:
        done.set()
        t.cancel()


def _chrome_pids(ctx):
    """The pids of this context's Chrome (found through its --user-data-dir), so only our browser is killed."""
    prof = getattr(ctx, "_rb_profile", None)
    if not prof or not Path("/proc").exists():
        return []
    out = []
    for d in Path("/proc").iterdir():
        if d.name.isdigit():
            try:
                if prof.encode() in (d / "cmdline").read_bytes():
                    out.append(int(d.name))
            except OSError:
                pass
    return out


def _kill(pids):
    for pid in pids:
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass


def api(env, method, path, body=None):
    req = urllib.request.Request(env["base"] + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def tab_id(env, url):
    return env["sw"].evaluate(f"chrome.tabs.query({{}}).then(ts => (ts.find(t => t.url === {json.dumps(url)}) || {{}}).id)")


def annotate(env, url, on=True):
    tid = tab_id(env, url)
    assert tid, url
    r = env["sw"].evaluate(f"chrome.tabs.get({tid}).then(t => toggleAnnotate(t, {json.dumps(on)}))")
    assert r["ok"], r
    return tid


def iso(env, tid, expr):
    """Evaluate `expr` in the content script's isolated world of tab `tid`."""
    return env["sw"].evaluate(
        f"chrome.scripting.executeScript({{target: {{tabId: {tid}}}, func: () => {{ return ({expr}); }}}}).then(r => r[0].result)")


def drag_select(page, sel):
    box = page.locator(sel).bounding_box()
    y = box["y"] + min(box["height"], 24) / 2
    page.mouse.move(box["x"] + 1, y)
    page.mouse.down()
    page.mouse.move(box["x"] + box["width"] - 1, box["y"] + box["height"] - 6, steps=10)
    page.mouse.up()


def hl_count(page):
    return page.evaluate("(CSS.highlights.get('rb-hl')?.size || 0) + (CSS.highlights.get('rb-hl-active')?.size || 0)")


def hl_texts(page):
    return page.evaluate("[...(CSS.highlights.get('rb-hl') || []), ...(CSS.highlights.get('rb-hl-active') || [])]"
                         ".map(r => r.toString().replace(/\\s+/g, ' ').trim()).sort()")


def test_annotate_html_page(env):
    ctx, site = env["ctx"], env["site"]
    url = site.base + "/paper.html"
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(url)
    tid = annotate(env, url)
    host = page.locator("#rb-annotate-host")
    expect(host).to_be_attached()
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("0")

    # 1. select → toolbar → Highlight (button)
    drag_select(page, "#t1")
    bar = page.locator("#rb-annotate-host .bar")
    expect(bar).to_be_visible()
    expect(bar.locator("button")).to_have_text(["Highlight H", "Highlight + note N"])
    shot(page, "ext-toolbar", clip={"x": 200, "y": 120, "width": 820, "height": 260})
    bar.get_by_role("button", name="Highlight H").click()
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("1")
    expect(bar).to_be_hidden()
    assert hl_count(page) == 1

    d = api(env, "GET", f"/api/fragments?url={urllib.request.quote(url, safe='')}")
    assert len(d["fragments"]) == 1
    f1 = d["fragments"][0]
    assert f1["selector"]["exact"] == "the Bohm criterion requires ions to enter the sheath at the sound speed"
    assert f1["selector"]["prefix"].endswith("In a collisionless plasma, ")
    assert f1["selector"]["suffix"].startswith(", which sets")
    assert f1["body"] == "" and f1["locator"] == "2. The Bohm criterion"
    paper = d["paper"]
    assert paper and paper["title"] == "Sheath physics, a primer" and paper["type"] == "note"

    # 2. select → N key → note popover with LaTeX → Mod+Enter saves
    drag_select(page, "#t3")
    expect(bar).to_be_visible()
    page.keyboard.press("n")
    frame = page.frame_locator("#rb-annotate-host .note iframe")
    ta = frame.locator("#note")
    expect(ta).to_be_focused()
    expect(frame.locator("#quote")).to_have_text("Debye lengths thick")
    ta.press_sequentially("Thickness scales as $\\lambda_D = \\sqrt{\\varepsilon_0 k T_e / n e^2}$")
    expect(frame.locator("#prev .katex").first).to_be_visible()
    assert hl_count(page) == 2  # the pending selection is shown while you write
    shot(page, "ext-note-popover", clip={"x": 150, "y": 160, "width": 900, "height": 460})
    ta.press("Control+Enter")
    expect(page.locator("#rb-annotate-host .note")).to_be_hidden()
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("2")
    frags = api(env, "GET", f"/api/fragments?url={urllib.request.quote(url, safe='')}")["fragments"]
    assert frags[1]["body"] == "Thickness scales as $\\lambda_D = \\sqrt{\\varepsilon_0 k T_e / n e^2}$"
    assert frags[1]["selector"]["exact"] == "Debye lengths thick"

    # 3. Esc cancels a note; H key highlights
    drag_select(page, "#t4")
    page.keyboard.press("n")
    expect(frame.locator("#note")).to_be_focused()
    # Esc is queued inside the note iframe rather than sent with page.keyboard: the iframe (an extension page, so
    # an out-of-process frame) removes itself while Chrome is still delivering the key, and Chrome then never
    # acknowledges that Input.dispatchKeyEvent (keyDown or keyUp), so keyboard.press() blocks forever. That was
    # the e2e suite's intermittent hang. The setTimeout lets this evaluate return before the frame goes away;
    # note.js handles the synthetic event exactly as a real key.
    frame.locator("#note").evaluate(
        "el => setTimeout(() => el.dispatchEvent(new KeyboardEvent('keydown', {key: 'Escape', code: 'Escape', bubbles: true})), 0)")
    expect(page.locator("#rb-annotate-host .note")).to_be_hidden()
    assert len(api(env, "GET", f"/api/fragments?url={urllib.request.quote(url, safe='')}")["fragments"]) == 2
    drag_select(page, "#t4")
    expect(bar).to_be_visible()
    page.keyboard.press("Escape")
    expect(bar).to_be_hidden()
    drag_select(page, "#t4")
    page.keyboard.press("h")
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("3")

    # 4. side list: collapsible; clicking an item scrolls to and activates its highlight
    page.locator("#rb-annotate-host .tab").click()
    items = page.locator("#rb-annotate-host .items .it")
    expect(items).to_have_count(3)
    expect(items.nth(1).locator(".b")).to_contain_text("Thickness scales")
    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
    items.nth(0).click()
    expect(items.nth(0)).to_have_class("it on")
    assert page.evaluate("CSS.highlights.get('rb-hl-active').size") == 1
    shot(page, "ext-side-list")

    # 5. click a highlight → card → Copy citation (quote + locator + formatted citation)
    page.locator("#rb-annotate-host .tab").click()  # collapse
    box = page.locator("#t1").bounding_box()
    page.mouse.click(box["x"] + 30, box["y"] + 10)
    card = page.locator("#rb-annotate-host .card")
    expect(card).to_be_visible()
    expect(card.locator(".src")).to_contain_text("Sheath physics, a primer")
    shot(page, "ext-card", clip={"x": 150, "y": 120, "width": 900, "height": 360})
    card.get_by_role("button", name="Copy citation").click()
    expect(page.locator("#rb-annotate-host .toast")).to_have_text("Citation copied")
    copied = iso(env, tid, "window.__rbAnnotate._lastCopied()")
    assert copied.startswith("“the Bohm criterion requires ions to enter the sheath at the sound speed” (2. The Bohm criterion)\n")
    assert "Sheath physics, a primer." in copied and url in copied
    page.keyboard.press("Escape")
    expect(card).to_be_hidden()

    # 6. reload → annotate again → all three re-anchor on the exact text
    page.reload()
    assert hl_count(page) == 0
    tid = annotate(env, url)
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("3")
    page.wait_for_function("(CSS.highlights.get('rb-hl')?.size || 0) === 3")
    assert hl_texts(page) == sorted(["the Bohm criterion requires ions to enter the sheath at the sound speed",
                                     "Debye lengths thick", "the electron and ion fluxes balance"])

    # 7. the page changes: new content above, re-wrapped whitespace, a reworded middle → still found (fuzzy)
    changed = PAPER_HTML.replace("{extra}", "<p>Updated 2026: a new preface paragraph with the sound speed in it.</p>")
    changed = changed.replace("requires ions to enter the sheath at the sound speed",
                              "requires ions to\n   enter   the sheath at the ion sound speed")
    site.html("/paper.html", changed)
    page.reload()
    annotate(env, url)
    page.wait_for_function("(CSS.highlights.get('rb-hl')?.size || 0) === 3")
    texts = hl_texts(page)
    assert "the Bohm criterion requires ions to enter the sheath at the ion sound speed" in texts, texts

    # 8. text removed from the page → listed as not found, others still drawn
    site.html("/paper.html", changed.replace("the electron and ion fluxes balance", "currents cancel"))
    page.reload()
    annotate(env, url)
    page.wait_for_function("(CSS.highlights.get('rb-hl')?.size || 0) === 2")
    page.locator("#rb-annotate-host .tab").click()
    expect(page.locator("#rb-annotate-host .items .miss")).to_have_count(1)

    # 9. toggling off removes everything from the page
    annotate(env, url, on=False)
    expect(host).to_be_hidden()
    assert hl_count(page) == 0
    assert not errors, errors
    assert not env["external"], env["external"]
    site.html("/paper.html", PAPER_HTML.replace("{extra}", ""))
    page.close()


def test_popup_for_page_and_quick_clip(env):
    ctx, site = env["ctx"], env["site"]
    url = site.base + "/paper.html"
    page = ctx.new_page()
    page.goto(url)
    tid = tab_id(env, url)
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 560})
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    expect(pop.locator("#home")).to_be_visible()
    expect(pop.locator("#homeSrc .t")).to_have_text("Sheath physics, a primer")
    expect(pop.locator("#homePaper .pt")).to_have_text("Sheath physics, a primer")
    expect(pop.locator("#noteCount")).to_have_text("3 notes on this page")
    expect(pop.locator("#annotateBtn")).to_have_text("Annotate this page")
    expect(pop.locator("#noAi")).to_have_text(
        "No AI in this extension. Everything you save goes only to research_brain on this computer.")
    shot(pop, "ext-popup-page")

    # Annotate this page (from the popup) turns annotate mode on in that tab
    pop.locator("#annotateBtn").click()
    expect(page.locator("#rb-annotate-host .tab .n")).to_have_text("3")

    # the quick-clip form still works
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 700})
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    pop.locator("#quickBtn").click()
    expect(pop.locator("#form")).to_be_visible()
    # the form focuses the note field 30 ms after it opens; fill the quote only after that, or the
    # deferred focus can take the keyboard mid-fill and the text lands in the note field instead
    expect(pop.locator("#note")).to_be_focused()
    pop.locator("#quote").fill("floating wall")
    pop.locator("#note").fill("quick clip from the popup")
    expect(pop.locator("#targets .opt.sel .ttl")).to_have_text("Sheath physics, a primer")
    pop.locator("#saveBtn").click()
    expect(pop.locator("#done")).to_be_visible()
    frags = api(env, "GET", f"/api/fragments?url={urllib.request.quote(url, safe='')}")["fragments"]
    assert frags[-1]["quote"] == "floating wall" and frags[-1]["selector"] is None

    # the server address can only be this computer
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    pop.locator("#gearBtn").click()
    pop.locator("#serverIn").fill("https://example.com")
    pop.locator("#serverSave").click()
    expect(pop.locator("#settings .hint")).to_contain_text("isn't on this computer")
    assert env["sw"].evaluate("chrome.storage.local.get('server').then(o => o.server)") == env["base"]
    page.close()
    assert not env["external"], env["external"]


def test_pdf_tab_imports_and_opens_viewer(env):
    ctx, site = env["ctx"], env["site"]
    # the paper the (mocked) import resolves to: imported through the normal upload endpoint
    import requests
    r = requests.post(env["base"] + "/api/import/pdf", files={"file": ("probe.pdf", _pdf_bytes(), "application/pdf")},
                      timeout=60)
    assert r.status_code == 201, r.text
    nid = r.json()["id"]

    pdf_url = site.base + "/probe.pdf"
    tab = ctx.new_page()
    tab.goto(pdf_url)
    tid = tab_id(env, pdf_url)
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 520})
    calls = []

    def mock_import(route):  # the real endpoint refuses 127.0.0.1 (SSRF guard), so it is mocked here
        calls.append(json.loads(route.request.post_data))
        route.fulfill(status=201, content_type="application/json",
                      body=json.dumps({"id": nid, "title": "Probe theory zqv test document", "viewer": f"/viewer/{nid}"}))
    pop.route(env["base"] + "/api/import/url", mock_import)
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    btn = pop.locator("#annotateBtn")
    expect(btn).to_have_text("Annotate this PDF in research_brain")
    expect(pop.locator("#annotateHint")).to_contain_text("Chrome's PDF viewer can't be annotated")
    shot(pop, "ext-popup-pdf")
    with ctx.expect_page() as newp:
        btn.click()
    viewer = newp.value
    viewer.wait_for_url(f"{env['base']}/viewer/{nid}")
    assert calls == [{"url": pdf_url}]

    # the viewer's selection toolbar is the extension's: Highlight / Highlight + note; H saves a highlight
    span = viewer.locator(".page[data-page='1'] .textLayer span", has_text="Langmuir probes")
    expect(span).to_be_visible(timeout=30_000)
    box = span.bounding_box()
    viewer.mouse.move(box["x"] + 2, box["y"] + box["height"] / 2)
    viewer.mouse.down()
    viewer.mouse.move(box["x"] + box["width"] - 2, box["y"] + box["height"] / 2, steps=8)
    viewer.mouse.up()
    expect(viewer.locator("#selBar")).to_be_visible()
    # the same two buttons as the extension; the app's own viewer adds "Ask" (the extension never talks to an AI)
    expect(viewer.locator("#selBar button")).to_have_text(["Highlight H", "Highlight + note N", "Ask A"])
    shot(viewer, "viewer-toolbar", clip={"x": 0, "y": 0, "width": 1000, "height": 360})
    viewer.keyboard.press("h")
    expect(viewer.locator("#fragItems .frag")).to_have_count(1)
    frags = api(env, "GET", f"/api/notes/{nid}")["fragments"]
    assert len(frags) == 1 and "Langmuir probes" in frags[0]["quote"] and frags[0]["body"] == "" and frags[0]["page"] == 1
    expect(viewer.locator(".textLayer [data-frag]").first).to_be_attached()
    tab.close()
    viewer.close()


def _note_by_url(env, url):
    m = api(env, "GET", f"/api/match?url={urllib.request.quote(url, safe='')}")["note"]
    assert m, url
    return api(env, "GET", f"/api/notes/{m['id']}")


def test_popup_saves_an_article_as_link_and_text(env):
    """On a publisher page the popup's main button saves link + text (no PDF); "Save PDF too" upgrades it."""
    ctx, site = env["ctx"], env["site"]
    url = site.base + "/doi/10.1063/1.4945558"
    page = ctx.new_page()
    page.goto(url)
    tid = tab_id(env, url)
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 640})
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    save = pop.locator("#saveLinkBtn")
    expect(save).to_have_text("Save to research_brain (link + text)")
    expect(save).to_have_class("btn primary wide")
    expect(pop.locator("#annotateBtn")).to_have_class("btn wide")  # annotate is secondary on an article page
    expect(pop.locator("#savePdfBtn")).to_have_text("Save PDF too")
    expect(pop.locator("#noAi")).to_be_visible()
    shot(pop, "ext-popup-article")
    save.click()
    expect(pop.locator("#done")).to_be_visible()
    expect(pop.locator("#doneSub")).to_contain_text("Linked article")
    expect(pop.locator("#doneSub b")).to_have_text("Breathing-mode oscillations in Hall thrusters")
    n = _note_by_url(env, url)
    assert n["type"] == "paper" and n["source"] == "linked" and n["has_text"] and not n["has_pdf"]
    assert n["doi"] == "10.1063/1.4945558" and n["authors"][:2] == ["Ada Physicist", "David J. Bohm"] and n["year"] == 2016
    text = (env["vault"] / "text" / f"{n['id']}.txt").read_text()
    assert "[[section 3.2 Frequency scaling]]" in text and "cookies" not in text
    assert not (env["vault"] / "pdfs" / f"{n['id']}.pdf").exists()
    # one click later: the PDF, fetched in the page with the browser's own access, attached to the same paper
    pdf_btn = pop.locator("#donePdfBtn")
    expect(pdf_btn).to_have_text("Save PDF too")
    pdf_btn.click()
    expect(pdf_btn).to_be_hidden(timeout=60_000)
    expect(pop.locator("#doneSub .kind")).to_have_text("Saved with its PDF.")
    n2 = api(env, "GET", f"/api/notes/{n['id']}")
    assert n2["has_pdf"] and n2["source"] == "pdf"
    assert "[[page 1]]" in (env["vault"] / "text" / f"{n['id']}.txt").read_text()  # the PDF's pages win
    # coming back to the page: the popup knows the paper
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    expect(pop.locator("#homePaper .lab")).to_have_text("In your library")
    # Library settings "Link + text + PDF": the main button says so, the second one is the plain save
    req = urllib.request.Request(env["base"] + "/api/settings", method="PUT", data=b'{"save_mode": "pdf"}',
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=10).close()
    try:
        pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
        expect(pop.locator("#saveLinkBtn")).to_have_text("Save to research_brain (link + text + PDF)")
        expect(pop.locator("#savePdfBtn")).to_have_text("Save link + text only")
    finally:
        req = urllib.request.Request(env["base"] + "/api/settings", method="PUT", data=b'{"save_mode": "link"}',
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=10).close()
    page.close()
    assert not env["external"], env["external"]


def test_popup_saves_a_pdf_tab_as_link_and_text(env):
    """On a PDF tab, link + text reads the PDF in the browser and keeps its page text, not the file."""
    ctx, site = env["ctx"], env["site"]
    url = site.base + "/linked.pdf"
    tab = ctx.new_page()
    tab.goto(url)
    tid = tab_id(env, url)
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 640})
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    save = pop.locator("#saveLinkBtn")
    expect(save).to_have_text("Save to research_brain (link + text)")
    expect(pop.locator("#saveLinkHint")).to_contain_text("without the PDF file")
    save.click()
    expect(pop.locator("#done")).to_be_visible(timeout=60_000)
    n = _note_by_url(env, url)
    assert n["source"] == "linked" and not n["has_pdf"] and n["url"] == url
    text = (env["vault"] / "text" / f"{n['id']}.txt").read_text()
    assert "[[page 1]]" in text and "[[page 2]]" in text and "Linked PDF zqw text only" in text
    assert not (env["vault"] / "pdfs" / f"{n['id']}.pdf").exists()
    tab.close()
    assert not env["external"], env["external"]


def test_extension_id_is_the_pinned_one(env):
    """Chrome derives the unpacked extension's id from manifest "key"; the server allows exactly that origin."""
    from app.security import Policy, extension_id_from_key
    key = json.loads((EXT_SRC / "manifest.json").read_text())["key"]
    assert env["ext_id"] == extension_id_from_key(key)
    assert env["ext_id"] in Policy().extension_ids


# the popup's Cite tab: copies are captured from navigator.clipboard.write (text/html + text/plain)
CAPTURE_CLIPBOARD = """() => {
  window.__copied = [];
  navigator.clipboard.write = async (items) => {
    for (const it of items) {
      const o = {};
      for (const t of it.types) o[t] = await (await it.getType(t)).text();
      window.__copied.push(o);
    }
  };
}"""


def _copied(pop, n):
    pop.wait_for_function("(n) => window.__copied && window.__copied.length >= n", arg=n)
    return pop.evaluate("window.__copied")[n - 1]


def test_popup_cite_tab(env):
    ctx, site = env["ctx"], env["site"]
    a = api(env, "POST", "/api/notes", {"type": "paper", "title": "Probe sheath kinetics zqc", "authors": ["Ann Probe"],
                                        "year": 2020, "venue": "Physics of Plasmas", "doi": "10.1063/zqc.1"})
    b = api(env, "POST", "/api/notes", {"type": "paper", "title": "Sheath turbulence preprint zqc", "authors": ["Bob Sheath"],
                                        "year": 2023, "arxiv": "2301.00099"})
    site.html("/notes-page.html", "<!doctype html><title>Some page</title><p>Writing about sheaths.</p>")
    url = site.base + "/notes-page.html"  # a page that isn't a paper in the library (nothing preselected)
    page = ctx.new_page()
    page.goto(url)
    tid = tab_id(env, url)
    pop = ctx.new_page()
    pop.set_viewport_size({"width": 420, "height": 700})
    pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tid}")
    expect(pop.locator("#home")).to_be_visible()
    pop.evaluate(CAPTURE_CLIPBOARD)
    pop.locator("#citeTabBtn").click()
    expect(pop.locator("#cite")).to_be_visible()
    expect(pop.locator("#home")).to_be_hidden()
    expect(pop.locator("#citeQ")).to_be_focused()
    expect(pop.locator("#citeStyle")).to_have_value("american-physics-society")
    expect(pop.locator("#citeCit")).to_have_class(re.compile(r"\bprimary\b"))  # a plain page: formatted first
    pop.locator("#citeQ").fill("zqc")
    expect(pop.locator("#citeList .opt[data-i]")).to_have_count(2)
    pop.locator("#citeList .opt", has_text="Probe sheath kinetics").click()
    pop.locator("#citeList .opt", has_text="Sheath turbulence preprint").click()
    expect(pop.locator("#citeN")).to_have_text("2 selected")
    shot(pop, "ext-cite-tab")
    pop.locator("#citeLatex").click()
    assert _copied(pop, 1)["text/plain"] == "\\cite{probe2020probe,sheath2023sheath}"
    expect(pop.locator("#citeHint")).to_contain_text("Copied \\cite{probe2020probe,sheath2023sheath}")
    pop.locator("#citeBib").click()
    bib = _copied(pop, 2)
    assert bib["text/plain"].startswith("[1] A. Probe, Probe sheath kinetics zqc, Physics of Plasmas (2020).")
    assert "arXiv:2301.00099" in bib["text/plain"] and '<div class="csl-entry">' in bib["text/html"]
    pop.locator("#citeStyle").select_option("apa")
    pop.locator("#citeCit").click()
    assert _copied(pop, 3)["text/plain"] == "(Probe, 2020; Sheath, 2023)"
    pop.locator("#citeBack").click()
    expect(pop.locator("#home")).to_be_visible()
    expect(pop.locator("#cite")).to_be_hidden()
    api(env, "PUT", "/api/cite/settings", {"style": "american-physics-society"})
    expect(pop.locator("#noAi")).to_have_text(
        "No AI in this extension. Everything you save goes only to research_brain on this computer.")

    # on Overleaf, \cite{} is the main button (the page is served locally under overleaf.com's name)
    ov_url = "https://www.overleaf.com/project/abc123"
    ctx.route("https://www.overleaf.com/**", lambda r: r.fulfill(status=200, content_type="text/html",
                                                                body="<!doctype html><title>thesis - Overleaf</title><p>editor</p>"))
    try:
        ov = ctx.new_page()
        ov.goto(ov_url)
        otid = tab_id(env, ov_url)
        pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={otid}&cite=1")
        pop.evaluate(CAPTURE_CLIPBOARD)
        expect(pop.locator("#cite")).to_be_visible()
        expect(pop.locator("#citeLatex")).to_have_class(re.compile(r"\bprimary\b"))
        expect(pop.locator("#citeHint")).to_contain_text("Overleaf")
        pop.locator("#citeQ").fill("probe sheath kinetics")
        expect(pop.locator("#citeList .opt[data-i]")).to_have_count(1)
        pop.locator("#citeQ").press("Enter")
        pop.locator("#citeQ").press("Control+Enter")
        assert _copied(pop, 1)["text/plain"] == "\\cite{probe2020probe}"
        # Google Docs: a formatted citation first
        gd_url = "https://docs.google.com/document/d/xyz/edit"
        ctx.route("https://docs.google.com/**", lambda r: r.fulfill(status=200, content_type="text/html", body="<title>Doc</title>"))
        gd = ctx.new_page()
        gd.goto(gd_url)
        pop.goto(f"chrome-extension://{env['ext_id']}/clip.html?tab={tab_id(env, gd_url)}&cite=1")
        expect(pop.locator("#citeCit")).to_have_class(re.compile(r"\bprimary\b"))
        expect(pop.locator("#citeHint")).to_contain_text("Google Docs")
        ov.close()
        gd.close()
    finally:
        ctx.unroute("https://www.overleaf.com/**")
        ctx.unroute("https://docs.google.com/**")
    page.close()
    assert not env["external"], env["external"]

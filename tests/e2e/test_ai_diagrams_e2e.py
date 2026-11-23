"""Diagrams and charts in AI answers (static/visuals.js), in the main app's chat and in the PDF viewer's Ask
panel, in Chromium (Windows' WebView2) and WebKit (the Mac window), under the app's real Content-Security-Policy
(no Playwright CSP bypass). The fake AI answers with exactly the Markdown a question carries
("FAKE-ANSWER:<base64>", app/claude_cli.py _fake), streamed in pieces, so a half-written diagram is on screen
for a while: it must show as a placeholder, never as an error."""
from __future__ import annotations

import base64
import io
import json
import urllib.request

import pytest

from rb_testserver import run_server

try:
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)
try:
    import reportlab  # noqa: F401
except ImportError:  # pragma: no cover
    pytest.skip("reportlab is not installed (needed to build a test PDF)", allow_module_level=True)

FLOW = 'flowchart TD\n  A["Raw spectra (n=40)"] -->|"fit"| B["CRM model"]\n  B --> C["Rates"]'
CHART = 'xychart-beta\n  title "Accuracy"\n  x-axis ["Model A", "Model B"]\n  y-axis "Percent" 0 --> 100\n  bar [72, 85]'
SEQ = "sequenceDiagram\n  participant U as User\n  U->>S: ask\n  S-->>U: answer"
PIE = 'pie title "Share"\n  "A" : 40\n  "B" : 60'
# the usual AI mistakes, fixed before drawing: unquoted labels with brackets, an unquoted edge label, chart
# categories with spaces
SLOPPY = "flowchart TD\n  A[Start (raw data)] --> B{Is it ok?}\n  B -->|yes (mostly)| C[Done: 5% error]"
SLOPPY_CHART = "xychart-beta\n  title Accuracy by model\n  x-axis [Model A, Model B (big)]\n  y-axis Percent 0 --> 100\n  bar [72, 85]"
BROKEN = "flowchart TD\n  A --> B -->\n  ((( not a diagram"

WATCH = """
window.__csp = [];
window.__sawPending = false; window.__sawErrWhileLive = false;
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sourceFile || '') + ':' + e.lineNumber);
});
new MutationObserver(() => {
  if (document.querySelector('.md-visual-pending')) window.__sawPending = true;
  if (document.querySelector('.chat-live .md-visual-err, .txt.live .md-visual-err')) window.__sawErrWhileLive = true;
}).observe(document, { childList: true, subtree: true });
"""


def fence(src: str) -> str:
    return f"```mermaid\n{src}\n```"


def ask_for(md: str) -> str:
    return "Draw it FAKE-ANSWER:" + base64.urlsafe_b64encode(md.encode()).decode()


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, "Bohm criterion for the sheath edge")
    c.showPage()
    c.save()
    return buf.getvalue()


def _upload(base) -> dict:
    b = "rbdiagramtest"
    body = (f"--{b}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + _pdf() + f"\r\n--{b}--\r\n".encode()
    req = urllib.request.Request(base + "/api/import/pdf", method="POST", data=body,
                                 headers={"Content-Type": f"multipart/form-data; boundary={b}"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def _put(base, path, body):
    req = urllib.request.Request(base + path, method="PUT", data=json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    vault = tmp_path_factory.mktemp("diagrams") / "vault"
    # slow pieces: each answer takes a second or two to arrive, so the half-written state is really seen
    with run_server(vault, env={"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0", "RB_FAKE_STREAM_DELAY": "0.3"}) as s:
        s.paper = _upload(s.base)
        yield s


@pytest.fixture(scope="module", params=["chromium", "webkit"])
def engine(request):
    with sync_playwright() as pw:
        try:
            b = getattr(pw, request.param).launch()
        except Exception as e:  # noqa: BLE001
            pytest.skip(f"no {request.param} for Playwright: {str(e).splitlines()[0]}")
        yield b
        b.close()


@pytest.fixture()
def page_in(engine, srv):
    """A page factory: no CSP bypass, every request off the test server blocked and recorded, and console
    errors and page errors collected."""
    ctx = engine.new_context(viewport={"width": 1300, "height": 850})
    ctx.set_default_timeout(20_000)
    outside = []
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith((srv.base, "data:", "blob:"))
              else (outside.append(r.request.url), r.abort()))
    ctx.add_init_script(WATCH)
    pages = []

    def make(path):
        p = ctx.new_page()
        p.problems = []
        p.on("pageerror", lambda e: p.problems.append(f"page error: {e}"))
        p.on("console", lambda m: p.problems.append(f"console {m.type}: {m.text}") if m.type == "error" else None)
        p.goto(srv.base + path)
        pages.append(p)
        return p
    yield make
    try:
        for p in pages:
            assert not p.problems, p.problems
            assert p.evaluate("window.__csp") == [], p.evaluate("window.__csp")
        assert not outside, f"the page tried to reach the network: {outside}"
    finally:
        ctx.close()


def _no_stray(p):
    # mermaid draws in a scratch element in <body>; a failed draw used to leave a big "Syntax error" there
    assert p.evaluate("[...document.body.children].filter((e) => /rb-mm/.test(e.id)).length") == 0


def _ask_main(p, md):
    p.click("#btn-chat")
    p.fill("#chat-input", ask_for(md))
    p.click("#btn-chat-send")


def _ask_viewer(p, md):
    p.click("#tabAsk")
    p.fill("#askInput", ask_for(md))
    p.click("#askSend")


def test_main_chat_draws_diagram_and_chart(page_in, srv):
    _put(srv.base, "/api/chats/library", {"messages": []})
    p = page_in("/")
    _ask_main(p, f"Here is the pipeline:\n\n{fence(FLOW)}\n\nand the results:\n\n{fence(CHART)}\n\n{fence(SEQ)}\n\n{fence(PIE)}\n\nDone.")
    answer = p.locator("#chat .msg.assistant").last
    expect(answer.locator(".md-visual-mermaid .md-visual-fig svg")).to_have_count(4, timeout=30_000)
    expect(answer).to_contain_text("Done.")
    expect(answer.locator(".md-visual-err")).to_have_count(0)
    text = answer.locator(".md-visual-mermaid").first.inner_text()
    assert "Raw spectra (n=40)" in text and "CRM model" in text
    assert answer.locator(".md-visual-mermaid").nth(1).locator("svg rect").count() >= 2  # the chart's bars
    assert p.evaluate("window.__sawPending") is True        # a placeholder while it was being written
    assert p.evaluate("window.__sawErrWhileLive") is False  # never an error for half a diagram
    _no_stray(p)


def test_viewer_ask_draws_diagram_and_chart(page_in, srv):
    p = page_in(f"/viewer/{srv.paper['id']}")
    expect(p.locator(".page[data-page='1'] canvas")).to_be_attached(timeout=30_000)
    p.evaluate(f"localStorage.removeItem('rb.paperChat.{srv.paper['id']}')")
    _ask_viewer(p, f"The method:\n\n{fence(FLOW)}\n\nResults:\n\n{fence(CHART)}\n\nDone.")
    answer = p.locator("#askLog .am.ai").last
    expect(answer.locator(".md-visual-mermaid .md-visual-fig svg")).to_have_count(2, timeout=30_000)
    expect(answer.locator(".md-visual-err")).to_have_count(0)
    assert "Raw spectra (n=40)" in answer.locator(".md-visual-mermaid").first.inner_text()
    assert p.evaluate("window.__sawPending") is True
    assert p.evaluate("window.__sawErrWhileLive") is False
    # drawn again from the saved conversation after a reload
    p.reload()
    p.click("#tabAsk")
    expect(p.locator("#askLog .am.ai .md-visual-mermaid .md-visual-fig svg")).to_have_count(2, timeout=30_000)
    _no_stray(p)


@pytest.mark.parametrize("where", ["main", "viewer"])
def test_mistakes_fixed_or_shown_as_code(page_in, srv, where):
    """Common mistakes are fixed before drawing; a diagram that still can't be drawn shows its code with a
    short note, and nothing else on the page breaks."""
    md = f"{fence(SLOPPY)}\n\n{fence(SLOPPY_CHART)}\n\n{fence(BROKEN)}\n\nAfter the diagrams."
    if where == "main":
        _put(srv.base, "/api/chats/library", {"messages": []})
        p = page_in("/")
        _ask_main(p, md)
        answer = p.locator("#chat .msg.assistant").last
    else:
        p = page_in(f"/viewer/{srv.paper['id']}")
        p.evaluate(f"localStorage.removeItem('rb.paperChat.{srv.paper['id']}')")
        _ask_viewer(p, md)
        answer = p.locator("#askLog .am.ai").last
    boxes = answer.locator(".md-visual-mermaid")
    expect(boxes).to_have_count(3, timeout=30_000)
    expect(boxes.nth(0).locator(".md-visual-fig svg")).to_be_attached(timeout=30_000)
    expect(boxes.nth(1).locator(".md-visual-fig svg")).to_be_attached()
    assert "Start (raw data)" in boxes.nth(0).inner_text()
    err = boxes.nth(2).locator(".md-visual-err")
    expect(err).to_contain_text("Couldn’t draw this diagram")
    expect(boxes.nth(2).locator(".md-visual-code")).to_be_visible()
    expect(boxes.nth(2).locator(".md-visual-code")).to_contain_text("not a diagram")
    expect(boxes.nth(2).locator(".md-visual-fig svg")).to_have_count(0)
    expect(answer).to_contain_text("After the diagrams.")
    _no_stray(p)

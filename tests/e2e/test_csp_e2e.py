"""The app's Content-Security-Policy (app/security.py) in a real browser, without Playwright's CSP bypass:
the graph, a note with math and links, and the PDF viewer must work with no CSP violation, and an image
from the internet written into a note must be blocked."""
from __future__ import annotations

import io
import urllib.request
import json

import pytest

from rb_testserver import run_server

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

from test_e2e import browser  # noqa: E402,F401  (fixture used by name)

WATCH = """
window.__csp = [];
document.addEventListener('securitypolicyviolation', (e) => {
  window.__csp.push(e.violatedDirective + ' ' + (e.blockedURI || '') + ' ' + (e.sourceFile || '') + ':' + e.lineNumber);
});
"""


def _pdf() -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, "Bohm criterion for the sheath edge")
    c.showPage()
    c.save()
    return buf.getvalue()


def _post(base, path, body=None, data=None, ctype="application/json"):
    req = urllib.request.Request(base + path, method="POST", data=data if data is not None else json.dumps(body).encode(),
                                 headers={"Content-Type": ctype})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _upload(base, pdf: bytes) -> dict:
    boundary = "rbcsptest"
    body = (f"--{boundary}\r\nContent-Disposition: form-data; name=\"file\"; filename=\"s.pdf\"\r\n"
            "Content-Type: application/pdf\r\n\r\n").encode() + pdf + f"\r\n--{boundary}--\r\n".encode()
    return _post(base, "/api/import/pdf", data=body, ctype=f"multipart/form-data; boundary={boundary}")


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    vault = tmp_path_factory.mktemp("csp") / "vault"
    with run_server(vault, env={"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0"}) as s:
        yield s


def test_app_and_viewer_run_under_csp(browser, srv):  # noqa: F811
    base = srv.base
    note = _post(base, "/api/notes", {"title": "CSP probe", "type": "note",
                                      "body": "Math $E = mc^2$ and $$\\int_0^1 x\\,dx$$, a [link](https://example.org), "
                                              "and a remote image ![t](https://tracker.example/pixel.png)."})
    paper = _upload(base, _pdf())
    ctx = browser.new_context(viewport={"width": 1300, "height": 850})  # no bypass_csp
    blocked = []
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith((base, "data:", "blob:"))
              else (blocked.append(r.request.url), r.abort()))
    ctx.add_init_script(WATCH)
    try:
        page = ctx.new_page()
        errors = []
        page.on("pageerror", lambda e: errors.append(str(e)))
        page.on("console", lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None)
        page.goto(f"{base}/#note={note['id']}")
        expect(page.locator("#panel-body .title-input")).to_have_value("CSP probe", timeout=20_000)
        expect(page.locator("#panel .katex").first).to_be_attached(timeout=20_000)  # math rendered
        assert page.evaluate("document.querySelectorAll('canvas').length") >= 1  # the graph drew
        viewer = ctx.new_page()
        viewer.on("pageerror", lambda e: errors.append(str(e)))
        viewer.on("console", lambda m: errors.append(m.text) if "Content Security Policy" in m.text else None)
        viewer.goto(f"{base}/viewer/{paper['id']}")
        expect(viewer.locator(".page[data-page='1'] .textLayer span", has_text="Bohm")).to_be_visible(timeout=30_000)
        violations = page.evaluate("window.__csp") + viewer.evaluate("window.__csp")
        # the only thing the policy may block is the remote image in the note
        assert any("tracker.example" in v and v.startswith("img-src") for v in violations), violations
        unexpected = [v for v in violations if "tracker.example" not in v]
        assert not unexpected, unexpected
        assert not [e for e in errors if "tracker.example" not in e], errors
        assert not [u for u in blocked if "tracker.example" not in u], blocked  # nothing else tried the network
    finally:
        ctx.close()

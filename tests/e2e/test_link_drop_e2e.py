"""Browser tests for links dropped on the window (static/app.js dropLink): a paper dragged from OneDrive on
the web is only a link. In research brain's own window it goes to the window's OneDrive download
(window.__rbLive.cloudFetch, app/cloudfetch.py; stubbed here, it uploads a PDF as the window would); any other
link goes to /api/import/url. A link dropped into a text field, or dragged inside the page, is left alone.

Run:  python -m pytest -q tests/e2e/test_link_drop_e2e.py   (RB_E2E_HEADED=1 to watch)
"""
from __future__ import annotations

import base64
import io
import json

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

ONEDRIVE = ("https://contoso-my.sharepoint.com/personal/ann_contoso_edu/_layouts/15/onedrive.aspx"
            "?id=%2Fpersonal%2Fann_contoso_edu%2FDocuments%2FPapers%2Fsheath.pdf")


def _pdf(title: str) -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(title)
    c.drawString(72, 700, title)
    c.showPage()
    c.save()
    return buf.getvalue()


DROP = """([types, target]) => {
  const dt = new DataTransfer();
  for (const [k, v] of Object.entries(types)) dt.setData(k, v);
  const el = target ? document.querySelector(target) : document.body;
  el.dispatchEvent(new DragEvent('dragenter', { dataTransfer: dt, bubbles: true, cancelable: true }));
  el.dispatchEvent(new DragEvent('dragover', { dataTransfer: dt, bubbles: true, cancelable: true }));
  const shown = !document.querySelector('#drop-overlay').hidden;
  el.dispatchEvent(new DragEvent('drop', { dataTransfer: dt, bubbles: true, cancelable: true }));
  return shown;
}"""


def _page(ctx, server):
    page = ctx.new_page()
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rbLive")
    return page


def test_onedrive_link_goes_to_the_windows_download(ctx, server):
    page = _page(ctx, server)
    b64 = base64.b64encode(_pdf("Sheath edge criteria in OneDrive")).decode()
    page.evaluate("""([b64]) => {
      window.__cf = [];
      window.__rbLive.cloudFetch = async (url, onSignin) => {
        window.__cf.push(url); onSignin();
        window.__signinShown = !!Array.from(document.querySelectorAll('#tasks .t-state')).find((s) => /Sign in to OneDrive/.test(s.textContent));
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const fd = new FormData(); fd.append('file', new File([bin], 'sheath.pdf', { type: 'application/pdf' }));
        const r = await fetch('/api/import/pdf', { method: 'POST', body: fd });
        return r.json();
      };
    }""", [b64])
    shown = page.evaluate(DROP, [{"text/uri-list": "# a comment line\r\n" + ONEDRIVE, "text/plain": ONEDRIVE}, None])
    assert shown, "the drop overlay shows for a dragged link"
    expect(page.locator(".toast", has_text="Added “Sheath edge criteria in OneDrive")).to_be_visible()
    assert page.evaluate("window.__cf") == [ONEDRIVE] and page.evaluate("window.__signinShown")
    expect(page.locator("#drop-overlay")).to_be_hidden()



def test_onedrive_error_is_said_plainly(ctx, server):
    page = _page(ctx, server)
    page.evaluate("""() => { window.__rbLive.cloudFetch = async () => { throw new Error('Sign-in was closed, so nothing was downloaded.'); }; }""")
    page.evaluate(DROP, [{"text/uri-list": ONEDRIVE}, None])
    expect(page.locator(".toast", has_text="Could not add it from OneDrive. Sign-in was closed")).to_be_visible()


def test_other_links_go_to_the_server_and_text_fields_keep_theirs(ctx, server):
    page = _page(ctx, server)
    asked = []

    def import_url(route):
        asked.append(json.loads(route.request.post_data or "{}").get("url"))
        route.fulfill(status=400, content_type="application/json", body=json.dumps({"detail": "That link isn't a PDF."}))
    page.route("**/api/import/url", import_url)
    # a browser's file drag: DownloadURL is "type:name:url"
    page.evaluate(DROP, [{"DownloadURL": "application/pdf:attn.pdf:https://arxiv.org/pdf/1706.03762"}, None])
    expect(page.locator(".toast", has_text="Could not add that link. That link isn't a PDF.")).to_be_visible()
    assert asked == ["https://arxiv.org/pdf/1706.03762"]

    # into the search field: the field takes the text, nothing is imported
    shown = page.evaluate(DROP, [{"text/uri-list": "https://example.org/a.pdf"}, "#search, input[type=search], input"])
    assert not shown
    # dragged inside the page (a link in a note, say): nothing is imported
    page.evaluate("() => window.dispatchEvent(new DragEvent('dragstart', { bubbles: true }))")
    assert not page.evaluate(DROP, [{"text/uri-list": "https://example.org/b.pdf"}, None])
    page.wait_for_timeout(300)
    assert asked == ["https://arxiv.org/pdf/1706.03762"]


MAC_WINDOW = """
window.__sent = [];
window.webkit = { messageHandlers: { rb: { postMessage(s) {  // as app/macwindow.py and app/cloudfetch.py answer
  const m = JSON.parse(s);
  if (!m.cloud) return;
  window.__sent.push(m.cloud);
  setTimeout(() => window.__rbCloud({ cloud: m.cloud.id, signin: true }), 10);
  setTimeout(() => window.__rbCloud({ cloud: m.cloud.id, error: 'OneDrive says this account can\\u2019t open that file. Is it shared with you?' }), 200);
} } } };"""


def test_mac_window_is_asked_and_its_answer_shown(ctx, server):
    page = ctx.new_page()
    page.add_init_script(MAC_WINDOW)
    page.goto(server["base"] + "/?app=mac")
    page.wait_for_function("!!(window.__rb && window.__rbLive && window.__rbLive.cloudFetch)")
    page.evaluate(DROP, [{"text/uri-list": ONEDRIVE}, None])
    expect(page.locator("#tasks .t-state", has_text="Sign in to OneDrive in the window that opened")).to_be_visible()
    expect(page.locator(".toast", has_text="Is it shared with you?")).to_be_visible()
    sent = page.evaluate("window.__sent")
    assert len(sent) == 1 and sent[0]["url"] == ONEDRIVE and sent[0]["id"]
    expect(page.locator("#tasks .task")).to_have_count(0)


def test_window_hands_the_pdf_to_the_server(server):
    """What app/cloudfetch.py does with the downloaded bytes: /api/import/pdf, as a dropped file."""
    from app import cloudfetch
    note = cloudfetch.import_pdf(server["base"], "Ion acoustic waves.pdf", _pdf("Ion acoustic waves from OneDrive"))
    assert note["has_pdf"] and "Ion acoustic" in note["title"]
    with pytest.raises(cloudfetch.CloudError, match="isn't a PDF"):
        cloudfetch.import_pdf(server["base"], "x.pdf", b"<html>nope</html>")

"""Browser tests for writing notes (static/notes-editor.js) and importing them (static/noteimport.js).

Tab nests a bullet and never leaves the note (Esc, then Tab, does); Enter continues a list, ends it on an
empty item; the formatting bar; pasted Google Docs formatting comes in as Markdown; a pasted image becomes a
figure; checkboxes tick in the reading view; the Import notes dialog brings in a Markdown file and a photo of
handwritten pages, shown in the note as its pages.

Run:  python -m pytest -q tests/e2e/test_notes_e2e.py   (RB_E2E_HEADED=1 to watch)
"""
from __future__ import annotations

import base64
import io
import json
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _open(ctx, server, body=""):
    n = _call(server["base"], "POST", "/api/notes", {"type": "note", "title": "Outline test", "body": body})
    page = ctx.new_page()
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.openNote && window.NotesEditor")
    page.evaluate("id => window.__rb.openNote(id)", n["id"])
    return page, n["id"]


def _body(page):
    ta = page.locator("#panel textarea.body-field")
    if not ta.is_visible():
        page.locator("#panel .section[data-sec='Notes'] .seg button", has_text="Edit").click()
    expect(ta).to_be_visible()
    return ta


def _saved(page, server, nid, want):
    """True once the server has the note's text (saves wait 0.8 s after the last change). Waits with Playwright,
    not time.sleep: the context's request filter runs in this thread, so a sleep would hold the save itself."""
    for _ in range(100):
        if _call(server["base"], "GET", f"/api/notes/{nid}")["body"].rstrip() == want:
            return True
        page.wait_for_timeout(100)
    return _call(server["base"], "GET", f"/api/notes/{nid}")["body"]


def test_tab_nests_and_stays_in_the_note(ctx, server):
    page, nid = _open(ctx, server)
    ta = _body(page)
    ta.click()
    page.keyboard.type("- Main idea")
    page.keyboard.press("Enter")
    page.keyboard.type("detail")
    page.keyboard.press("Tab")
    assert ta.evaluate("el => document.activeElement === el"), "Tab moved focus out of the note"
    page.keyboard.press("Enter")
    page.keyboard.type("deeper")
    page.keyboard.press("Tab")
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")          # empty item: one level up
    page.keyboard.type("back up")
    page.keyboard.press("Shift+Tab")
    assert ta.input_value() == "- Main idea\n    - detail\n        - deeper\n- back up"
    page.keyboard.press("Enter")
    page.keyboard.press("Enter")          # empty top-level item: the list ends
    page.keyboard.type("Plain line")
    assert ta.input_value().endswith("- back up\nPlain line")
    # Esc, then Tab: leaves the field like anywhere else
    page.keyboard.press("Escape")
    page.keyboard.press("Tab")
    assert not ta.evaluate("el => document.activeElement === el")
    assert _saved(page, server, nid, ta.input_value()) is True


def test_toolbar_checklist_and_reading_view(ctx, server):
    page, nid = _open(ctx, server)
    ta = _body(page)
    ta.click()
    page.keyboard.type("buy milk")
    page.locator("#panel .ne-bar .ne-task").click()
    assert ta.input_value() == "- [ ] buy milk"
    page.keyboard.press("End")
    page.keyboard.press("Enter")
    page.keyboard.type("call lab")
    assert ta.input_value() == "- [ ] buy milk\n- [ ] call lab"
    ta.evaluate("el => { const i = el.value.indexOf('call lab'); el.setSelectionRange(i, i + 8); }")
    page.keyboard.press("ControlOrMeta+b")
    assert ta.input_value() == "- [ ] buy milk\n- [ ] **call lab**"
    page.locator("#panel .section[data-sec='Notes'] .seg button", has_text="Preview").click()
    boxes = page.locator("#panel .preview.md li input[type=checkbox]")
    expect(boxes).to_have_count(2)
    boxes.nth(1).click()
    assert _saved(page, server, nid, "- [ ] buy milk\n- [x] **call lab**") is True
    expect(page.locator("#panel .preview.md li input[type=checkbox]").nth(1)).to_be_checked()


def test_paste_google_docs_and_an_image(ctx, server):
    page, nid = _open(ctx, server)
    ta = _body(page)
    ta.click()
    html = ('<b style="font-weight:normal" id="docs-internal-guid-x"><ul><li aria-level="1"><p><span style="font-weight:700">Top</span></p></li>'
            '<li aria-level="2"><p><span>child</span></p></li></ul></b>')
    page.evaluate("""([html]) => {
        const ta = document.querySelector('#panel textarea.body-field');
        const dt = new DataTransfer(); dt.setData('text/html', html); dt.setData('text/plain', 'Top\\nchild');
        ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""", [html])
    page.wait_for_function("document.querySelector('#panel textarea.body-field').value.includes('    - child')")
    assert ta.input_value() == "- **Top**\n    - child"
    png = base64.b64encode(_png()).decode()
    page.evaluate("""([b64]) => {
        const ta = document.querySelector('#panel textarea.body-field');
        const bin = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const dt = new DataTransfer(); dt.items.add(new File([bin], 'image.png', { type: 'image/png' }));
        ta.dispatchEvent(new ClipboardEvent('paste', { clipboardData: dt, bubbles: true, cancelable: true }));
    }""", [png])
    page.wait_for_function("document.querySelector('#panel textarea.body-field').value.includes('](/attachments/figure-')")
    page.locator("#panel .section[data-sec='Notes'] .seg button", has_text="Preview").click()
    img = page.locator("#panel .preview.md img")
    expect(img).to_have_count(1)
    page.wait_for_function("document.querySelector('#panel .preview.md img')?.naturalWidth > 0")


def _png() -> bytes:
    from app.noteimport import _png as enc
    import numpy as np
    a = np.zeros((20, 30, 3), dtype=np.uint8)
    a[:, :, 0] = 200
    return enc(a)


def _jpeg() -> bytes:
    pil = pytest.importorskip("PIL.Image")
    b = io.BytesIO()
    pil.new("RGB", (400, 520), "white").save(b, "JPEG")
    return b.getvalue()


def test_import_dialog_notes_and_handwriting(ctx, server, tmp_path):
    page = ctx.new_page()
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rbNoteImport && window.__rb && window.__rb.S && window.__rb.S.status")
    page.evaluate("window.__rbNoteImport.open()")
    dlg = page.locator(".ni-card")
    expect(dlg).to_be_visible()
    md = tmp_path / "Lecture 1.md"
    md.write_text("---\ntags: [physics]\n---\n- waves\n    - dispersion\n")
    dlg.locator(".ni-notes-input").set_input_files(str(md))
    expect(dlg.locator(".ni-result")).to_have_text("Imported 1 note.")
    expect(dlg.locator(".ni-list button")).to_have_text("Lecture 1")
    dlg.locator(".ni-more").click()
    photo = tmp_path / "IMG_0001.jpg"
    photo.write_bytes(_jpeg())
    dlg.locator(".ni-title-in").fill("Notebook page")
    dlg.locator(".ni-ocr").check()  # the fake Claude reads images
    dlg.locator(".ni-scan-input").set_input_files(str(photo))
    expect(dlg.locator(".ni-result")).to_have_text("Imported 1 note.", timeout=30_000)
    expect(dlg.locator(".ni-tip").first).to_contain_text("Written out as text")
    dlg.locator(".ni-list button", has_text="Notebook page").click()
    expect(page.locator("#panel .scan-page img")).to_have_count(1)
    page.wait_for_function("document.querySelector('#panel .scan-page img')?.naturalWidth > 0")
    expect(page.locator("#panel .preview.md")).to_contain_text("nested point")

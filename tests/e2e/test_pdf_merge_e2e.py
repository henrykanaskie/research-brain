"""One paper, one PDF, in the browser (static/pdfmerge.js): a paper added without its PDF and the PDF imported later
as a separate paper are joined from either paper's panel: pick the other one, confirm, and the first has the PDF."""
from __future__ import annotations

import urllib.error

import pytest
import requests

from test_e2e import TIMEOUT, api, clear_vault, open_app

try:
    from playwright.sync_api import expect
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)
pytest.importorskip("reportlab")


def _pdf(title: str, line: str) -> bytes:
    import io
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.setTitle(title)
    c.drawString(72, 720, title)
    c.drawString(72, 700, line)
    c.showPage()
    c.save()
    return buf.getvalue()


def _two_papers(server):
    clear_vault(server)
    a = api(server, "POST", "/api/notes", {"type": "paper", "title": "Kinetic simulations of plasma sheath formation",
                                           "authors": ["Kenta Kajita"], "year": 2025, "doi": "10.1000/kajita"})
    r = requests.post(server["base"] + "/api/import/pdf",
                      files={"file": ("download.pdf", _pdf("kajita_preprint_final_v3", "Sheaths form in a few plasma periods."),
                                      "application/pdf")}, timeout=30)
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["has_pdf"] and b["id"] != a["id"]
    hl = api(server, "POST", "/api/notes", {"type": "fragment", "parent": b["id"], "quote": "Sheaths form", "body": "fast"})
    return a, b, hl


def _gone(server, nid):
    try:
        api(server, "GET", f"/api/notes/{nid}")
    except urllib.error.HTTPError as e:
        return e.code == 404
    return False


def _join(page, server, a, b, hl, pick):
    page.locator("#pdf-merge-picker li[data-id='%s']" % pick["id"]).click()
    expect(page.locator("#modal-text")).to_contain_text("Kajita 2025 gets this PDF")
    page.locator("#modal-ok").click()
    page.wait_for_function("(id) => window.__rb.S.cur && window.__rb.S.cur.id === id && window.__rb.S.cur.has_pdf",
                           arg=a["id"], timeout=TIMEOUT)
    assert api(server, "GET", f"/api/notes/{a['id']}")["has_pdf"]
    assert _gone(server, b["id"])
    assert api(server, "GET", f"/api/notes/{hl['id']}")["parent"] == a["id"]
    page.wait_for_function("([a, b]) => window.__rb.S.nodes.has(a) && !window.__rb.S.nodes.has(b)", arg=[a["id"], b["id"]])
    expect(page.locator("#panel [data-act='pdf-of']")).to_be_visible()  # it has a PDF now


def test_this_is_the_pdf_of(ctx, server):
    a, b, hl = _two_papers(server)
    page = open_app(ctx, server)
    page.evaluate("(id) => window.__rb.openNote(id)", b["id"])
    page.locator("#panel [data-act='pdf-of']").click()
    rows = page.locator("#pdf-merge-picker li[data-id]")
    expect(rows.first).to_have_attribute("data-id", a["id"])
    expect(rows.first).to_contain_text("Kenta Kajita · 2025")
    _join(page, server, a, b, hl, pick=a)
    assert not page.errors, page.errors


def test_use_a_pdf_already_in_your_library(ctx, server):
    a, b, hl = _two_papers(server)
    page = open_app(ctx, server)
    page.evaluate("(id) => window.__rb.openNote(id)", a["id"])
    page.locator("#panel [data-act='pdf-from-library']").click()
    page.locator("#pdf-merge-picker input").fill("preprint")
    expect(page.locator("#pdf-merge-picker li[data-id]")).to_have_count(1)
    _join(page, server, a, b, hl, pick=b)
    assert not page.errors, page.errors

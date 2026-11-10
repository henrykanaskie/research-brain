"""Linked papers in a real browser:

1. The extension's page-side extractor (extension/extract.js) on fixture pages served at realistic
   addresses (Playwright routes, nothing leaves the machine): a publisher page with citation_* tags and
   numbered sections, an arXiv abstract page, an arXiv HTML (LaTeXML) page, and a page with only JSON-LD.
2. The app: a linked paper's panel (kind pill link glyph, "Linked article", "Open article", "Get the PDF")
   and how citations of linked passages render ("§3.2" / "p. 2" links into the article, not the viewer).

Run:   python -m pytest -q tests/e2e/test_linked_e2e.py
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import run_server

ROOT = Path(__file__).resolve().parents[2]
FIX = ROOT / "tests" / "fixtures" / "linked"
EXTRACT = ROOT / "extension" / "extract.js"

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

TIMEOUT = 15_000


def _chrome():
    for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    return None


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except PWError as e1:
            exe = _chrome()
            if not exe:
                pytest.skip(f"no browser for Playwright ({str(e1).splitlines()[0]})")
            b = pw.chromium.launch(executable_path=exe)
        yield b
        b.close()


def extract(browser, fixture: str, url: str) -> dict:
    ctx = browser.new_context()
    external = []

    def route(r):
        if r.request.url == url:
            r.fulfill(status=200, content_type="text/html; charset=utf-8", body=(FIX / fixture).read_text())
        else:
            if r.request.resource_type in ("fetch", "xhr", "websocket", "eventsource"):
                external.append(r.request.url)  # the page's own images may try to load; scripts must not fetch
            r.abort()
    ctx.route("**/*", route)
    try:
        page = ctx.new_page()
        page.goto(url)
        page.add_script_tag(path=str(EXTRACT))
        out = page.evaluate("window.__rbExtract.run()")
        assert not external, external  # the extractor fetches nothing
        return out
    finally:
        ctx.close()


# ------------------------------------------------------------------ 1. extraction
def test_publisher_page_with_citation_meta_and_sections(browser):
    r = extract(browser, "publisher.html", "https://pubs.example.org/doi/10.1063/1.4945558")
    assert r["title"] == "Breathing-mode oscillations in Hall thrusters"
    assert r["authors"] == ["Ada Physicist", "David J. Bohm", "Irving Langmuir"]  # "Last, First" turned round
    assert (r["doi"], r["year"], r["venue"]) == ("10.1063/1.4945558", 2016, "Physics of Plasmas")
    assert r["pdf_url"] == "https://pubs.example.org/doi/pdf/10.1063/1.4945558"
    assert r["url"] == "https://pubs.example.org/doi/10.1063/1.4945558"
    assert r["abstract"].startswith("The breathing mode is a low-frequency oscillation")
    assert r["kind"] == "html" and not r["partial"]
    t = r["text"]
    heads = [ln for ln in t.split("\n") if ln.startswith("[[section ")]
    assert heads == ["[[section Abstract]]", "[[section 1. Introduction]]", "[[section 2. Method]]",
                     "[[section 2.1 Probe analysis]]", "[[section 3. Results]]", "[[section 3.2 Frequency scaling]]",
                     "[[section 4. Conclusion]]"]
    assert "rose from 14 kHz at 200 V to 23 kHz at 400 V" in t
    assert "$T_e = e\\,\\Delta V / \\ln 2$" in t  # MathML alttext kept as TeX
    assert "Fig. 1. Discharge current spectrum" in t  # figure captions kept, images not
    # page furniture and the reference list stay out of the text
    for junk in ("cookies", "Sign in", "Related articles", "Download PDF", "Copyright", "Boeuf", "Ada Physicist, David"):
        assert junk not in t, junk
    assert len(r["references"]) == 3 and "10.1063/1.368529" in r["references"][0]
    assert t.index("[[section Abstract]]") < t.index("[[section 1. Introduction]]")


def test_arxiv_abstract_page(browser):
    r = extract(browser, "arxiv_abs.html", "https://arxiv.org/abs/1706.03762v7")
    assert r["title"] == "Attention Is All You Need" and r["arxiv"] == "1706.03762"
    assert r["authors"][:2] == ["Ashish Vaswani", "Noam Shazeer"] and r["year"] == 2017
    assert r["pdf_url"] == "https://arxiv.org/pdf/1706.03762"
    # only the abstract is on this page: it is the text, and the popup is told so
    assert r["partial"] and r["text"].startswith("[[section Abstract]]\n\nThe dominant sequence transduction models")
    assert "dispensing with recurrence" in r["text"]  # the fuller on-page abstract beats the meta tag
    assert "Submitted on" not in r["text"] and "TeX Source" not in r["text"]


def test_arxiv_html_latexml_page(browser):
    r = extract(browser, "arxiv_html.html", "https://arxiv.org/html/2401.00001v1")
    assert r["arxiv"] == "2401.00001" and not r["partial"]
    heads = [ln for ln in r["text"].split("\n") if ln.startswith("[[section ")]
    assert heads == ["[[section Abstract]]", "[[section 1 Introduction]]", "[[section 2 Theory]]",
                     "[[section 2.1 Floating potential]]"]
    assert "$c_{s}=\\sqrt{kT_{e}/m_{i}}$, which is the Bohm criterion" in r["text"]
    assert "Contents:" not in r["text"] and "Generated by LaTeXML" not in r["text"] and "Irving Langmuir" not in r["text"]
    assert len(r["references"]) == 2 and "doi:10.1088/0022-3727/24/4/001" in r["references"][1]


def test_json_ld_only_page(browser):
    r = extract(browser, "jsonld.html", "https://openplasma.example.net/articles/42")
    assert r["title"] == "Neural surrogate models for plasma sheaths"
    assert r["authors"] == ["Grace Hopper", "Alan Turing"]
    assert (r["doi"], r["year"], r["venue"]) == ("10.5555/opl.2021.042", 2021, "Open Plasma Letters")
    assert r["abstract"].startswith("We train neural networks")
    heads = [ln for ln in r["text"].split("\n") if ln.startswith("[[section ")]
    assert heads == ["[[section Abstract]]", "[[section Background]]", "[[section Training data]]",
                     "[[section Results and discussion]]"]
    assert "twelve thousand one-dimensional sheath simulations" in r["text"]
    for junk in ("Great paper", "Most read this week", "Grace Hopper and Alan Turing"):
        assert junk not in r["text"], junk
    assert r["pdf_url"] == "" and r["references"] == []


# ------------------------------------------------------------------ 2. the app
@pytest.fixture(scope="module")
def server(tmp_path_factory):
    vault = tmp_path_factory.mktemp("linked-vault")
    with run_server(vault, log_path=vault.parent / "linked-server.log", env={"RB_CITES_ON_IMPORT": "0"}) as srv:
        yield {"base": srv.base, "vault": vault}


def api(server, method, path, body=None):
    req = urllib.request.Request(server["base"] + path, method=method,
                                 data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def test_linked_paper_panel_and_citations(browser, server):
    url = "https://pubs.example.org/doi/10.1063/1.4945558"
    text = ("[[section 1. Introduction]]\n\nHall thrusters are electric propulsion devices for station keeping.\n\n"
            "[[section 3.2 Frequency scaling]]\n\nThe breathing-mode frequency rose from 14 kHz to 23 kHz.")
    n = api(server, "POST", "/api/import/linked", {"url": url, "title": "Breathing-mode oscillations", "authors": ["A. B"],
                                                   "doi": "10.1063/1.4945558", "year": 2016, "text": text})
    nid = n["id"]
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    ctx.set_default_timeout(TIMEOUT)
    external = []
    ctx.route("**/*", lambda r: r.continue_() if r.request.url.startswith((server["base"], "data:", "blob:"))
              else (external.append(r.request.url), r.abort()))
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(server["base"] + f"/#note={nid}")
    page.wait_for_function("window.__rb && window.__rb.S.nodes && window.__rb.S.nodes.size > 0")
    expect(page.locator("#panel-type .kind-link svg")).to_be_attached()
    expect(page.locator("#panel-body .linked-line")).to_contain_text("Linked article.")
    open_btn = page.locator("#panel-body .actions a", has_text="Open article")
    expect(open_btn).to_have_attribute("href", url)
    expect(page.locator("#panel-body .actions button", has_text="Get the PDF")).to_be_visible()
    expect(page.locator("#panel-body .actions a", has_text="Open in viewer")).to_have_count(0)

    # citations of a linked article: a section opens the article at the passage, a page at #page=N
    page.evaluate("""() => window.__rb.S.secHints = new Map([[%s, %s]])""" % (
        json.dumps(f"{nid}§3.2"), json.dumps(url + "#:~:text=The%20breathing%2Dmode%20frequency")))
    html = page.evaluate("(s) => window.__rb.renderMD(s)",
                         f"See [[{nid}]] §3.2, and [[{nid}]] (§\"Introduction\") and [[{nid}]] p. 2 and [[{nid}]].")
    page.set_content(f"<div id=t>{html}</div>")
    links = page.locator("#t a.cite.linked")
    expect(links).to_have_count(3)
    assert links.nth(0).get_attribute("href") == url + "#:~:text=The%20breathing%2Dmode%20frequency"
    assert links.nth(0).locator(".cite-page").inner_text() == "§3.2"
    assert links.nth(1).get_attribute("href") == url + "#:~:text=Introduction"
    assert links.nth(2).get_attribute("href") == url + "#page=2"
    assert all(links.nth(i).get_attribute("rel") == "noopener noreferrer" for i in range(3))
    assert page.locator("#t button.cite").count() == 1  # the bare [[id]] still opens the note
    assert "§3.2." not in page.locator("#t").inner_text().replace("§3.2,", "")  # the section isn't left as loose text

    # a chat passage of a linked article links to the article
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes && window.__rb.S.nodes.size > 0")
    box = page.evaluate("""() => { const el = window.__rb.chatPassages({ passages: [
        { id: %s, title: 'B', page: null, section: '3.2', section_title: '3.2 Frequency scaling', src: 'text',
          text: 'The breathing-mode frequency rose', pdf: '', linked: true, url: %s }] });
        return { tag: el.querySelector('.psg').tagName, href: el.querySelector('.psg').getAttribute('href'),
                 pg: el.querySelector('.pg').textContent }; }""" % (json.dumps(nid), json.dumps(url + "#:~:text=The")))
    assert box == {"tag": "A", "href": url + "#:~:text=The", "pg": "§3.2"}
    assert not errors, errors
    assert not external, external
    ctx.close()

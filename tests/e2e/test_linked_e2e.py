"""Linked papers in a real browser:

1. The extension's page-side extractor (extension/extract.js) on fixture pages served at realistic
   addresses (Playwright routes, nothing leaves the machine): a publisher page with citation_* tags and
   numbered sections, an arXiv abstract page, an arXiv HTML (LaTeXML) page, a page with only JSON-LD, and one
   page per publisher a plasma/propulsion researcher reads (APS, AIP, IOP, ScienceDirect, Springer Nature, Wiley,
   Taylor & Francis, AIAA, IEEE Xplore, Cambridge Core, OSTI, NASA NTRS).
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


# ------------------------------------------------------------------ 1b. one fixture per publisher
# tests/fixtures/linked/pub_*.html: handcrafted to each site's markup conventions; each file's first comment says
# which parts are verified (against the publisher's Zotero translator) and which are assumed. Served at the
# publisher's real address shape, so URL-based DOI / id detection is exercised too.
NUM = ["1. Introduction", "2. Method", "3. Results", "4. Conclusion"]
PLAIN = ["Introduction", "Method", "Results", "Conclusion"]
TEX = "$T_e = e\\,\\Delta V / \\ln 2$"
PUBLISHERS = [
    # fixture, url, doi, title, authors, year, venue, extra fields, section heads, n refs, math (or None), sentinel
    ("pub_aps.html", "https://journals.aps.org/pre/abstract/10.1103/PhysRevE.101.063204", "10.1103/PhysRevE.101.063204",
     "Probe measurements of a Hall thruster discharge", ["Johannes D. van der Berg", "Martin King Jr."], 2020, "Physical Review E",
     {"volume": "101", "issue": "6", "pages": "063204", "journal_abbrev": "Phys. Rev. E", "genre": "article-journal"}, NUM, 2, TEX, "APS"),
    ("pub_aip.html", "https://pubs.aip.org/aip/pop/article/27/6/063505/1062925/Sheath-formation", "10.1063/5.0012345",
     "Sheath formation at a floating wall: a kinetic study", ["Irving Langmuir", "Lewi Tonks"], 2020, "Physics of Plasmas",
     {"pages": "063505", "issn": ["1070-664X", "1089-7674"],
      "pdf_url": "https://pubs.aip.org/aip/pop/article-pdf/27/6/063505/123/063505_1_online.pdf"}, NUM, 3, TEX, "AIP"),
    ("pub_iop.html", "https://iopscience.iop.org/article/10.1088/1361-6595/ab9b52/meta", "10.1088/1361-6595/ab9b52",
     "Machine learning for plasma diagnostics: a review", ["Grace Hopper", "Alan Turing"], 2020, "Plasma Sources Science and Technology",
     {"pages": "074001", "journal_abbrev": "Plasma Sources Sci. Technol."}, NUM, 2, "$n_e \\propto I_{sat}$", "IOP"),
    ("pub_sciencedirect.html", "https://www.sciencedirect.com/science/article/pii/S0010465520302447", "10.1016/j.cpc.2020.107441",
     "A fast kinetic solver for Hall thruster discharges", ["Ada Physicist", "Hans Peter de Vries"], 2020,
     "Computer Physics Communications", {"volume": "256", "pages": "107441"}, NUM, 2, "$T_e$", "ScienceDirect"),
    ("pub_springer.html", "https://link.springer.com/article/10.1007/s41614-021-00052-5", "10.1007/s41614-021-00052-5",
     "Electric propulsion for small satellites", ["Dan Lev", "Roger M. Myers"], 2021, "Reviews of Modern Plasma Physics",
     {"volume": "5"}, PLAIN, 2, None, "Springer"),
    ("pub_wiley.html", "https://onlinelibrary.wiley.com/doi/full/10.1002/ctpp.201900104", "10.1002/ctpp.201900104",
     "Dust charging in a low-pressure argon discharge", ["André Melzer", "Alexander Piel"], 2020, "Contributions to Plasma Physics",
     {"pages": "e201900104", "issue": "3"}, NUM, 2, TEX, "Wiley"),
    ("pub_tandf.html", "https://www.tandfonline.com/doi/full/10.1080/15361055.2020.1760647", "10.1080/15361055.2020.1760647",
     "Tritium retention in tungsten divertor tiles", ["Russell P. Doerner", "Matthew J. Baldwin"], 2020,
     "Fusion Science and Technology", {}, PLAIN, 2, None, "Taylor and Francis"),
    ("pub_aiaa.html", "https://arc.aiaa.org/doi/10.2514/6.2023-1234", "10.2514/6.2023-1234",
     "Erosion Modeling of Boron Nitride Walls in Magnetically Shielded Hall Thrusters", ["Ada Physicist", "Richard R. Hofer"], 2023,
     "AIAA SCITECH 2023 Forum", {"genre": "paper-conference", "conference": "AIAA SCITECH 2023 Forum"}, PLAIN, 2, TEX, "AIAA"),
    ("pub_ieee.html", "https://ieeexplore.ieee.org/document/9091562", "10.1109/TPS.2020.2991234",
     "Laser-Induced Fluorescence of Xenon Ions in a Hall Thruster", ["Grace Hopper", "Lewi Tonks"], 2020,
     "IEEE Transactions on Plasma Science", {"volume": "48", "issue": "6", "pages": "2010-2018", "issn": ["0093-3813", "1939-9375"]},
     PLAIN, 2, None, "IEEE"),
    ("pub_cambridge.html", "https://www.cambridge.org/core/journals/journal-of-plasma-physics/article/gyrokinetic-simulations/ABC123",
     "10.1017/S0022377821000763", "Gyrokinetic simulations of ion-scale turbulence in a stellarator", ["Per Helander", "Gabriel G. Plunk"],
     2021, "Journal of Plasma Physics", {"pages": "905870401"}, NUM, 2, TEX, "Cambridge"),
]


@pytest.mark.parametrize("case", PUBLISHERS, ids=[c[0] for c in PUBLISHERS])
def test_publisher_pages(browser, case):
    fixture, url, doi, title, authors, year, venue, extra, heads, nrefs, math, who = case
    r = extract(browser, fixture, url)
    assert (r["doi"], r["title"], r["authors"], r["year"], r["venue"]) == (doi, title, authors, year, venue)
    for k, v in extra.items():
        assert r[k] == v, (k, r[k])
    assert not r["partial"] and r["abstract"].startswith("We measure electron temperature")
    t = r["text"]
    got = [ln[len("[[section "):-2] for ln in t.split("\n") if ln.startswith("[[section ")]
    assert got == ["Abstract"] + heads
    assert f"The {who} sentinel sentence closes the results." in t
    assert len(r["references"]) == nrefs and not any(x in " ".join(r["references"]) for x in ("Google Scholar", "View in Scopus"))
    if math:
        assert math in t
    for junk in ("cookies", "Sign in", "Copyright", "Highlights", "Export citation", "xplGlobal"):
        assert junk not in t, junk
    assert "Boeuf" not in t and "Goebel" not in t and "Helander, P." not in t  # references stay out of the text


def test_report_landing_pages_osti_and_ntrs(browser):
    o = extract(browser, "pub_osti.html", "https://www.osti.gov/biblio/1617000")
    assert (o["osti"], o["genre"], o["title"], o["authors"], o["year"]) == (
        "1617000", "report", "Krypton collisional-radiative model for Hall thruster plumes", ["Lois D. Nominator"], 2020)
    assert o["partial"] and "line ratios in thruster plumes" in o["abstract"]
    n = extract(browser, "pub_ntrs.html", "https://ntrs.nasa.gov/citations/20200002395")
    assert n["ntrs"] == "20200002395" and n["title"] == "Performance of a 12.5 kW Magnetically Shielded Hall Thruster on Krypton"
    assert n["partial"] and "krypton across discharge voltages" in n["abstract"]


def test_mathjax3_data_latex_and_display(browser):
    html = ("<html><body><article><h2>1. Theory</h2><p>" + "Plasma text. " * 150 +
            '<mjx-container class="MathJax" jax="SVG" data-latex="\\omega_{pe}"><svg></svg></mjx-container> and '
            '<mjx-container class="MathJax" jax="CHTML" display="true"><mjx-math aria-hidden="true">x</mjx-math>'
            '<mjx-assistive-mml display="block"><math display="block"><mi>x</mi><annotation encoding="application/x-tex">\\lambda_D</annotation></math>'
            "</mjx-assistive-mml></mjx-container></p></article></body></html>")
    (FIX / "_mjx3.html").write_text(html)
    try:
        r = extract(browser, "_mjx3.html", "https://example.org/mjx3")
    finally:
        (FIX / "_mjx3.html").unlink()
    assert "$\\omega_{pe}$" in r["text"] and "$$\\lambda_D$$" in r["text"]


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
    expect(page.locator('#panel-body .actions button[data-act="refresh-metadata"]')).to_have_text("Refresh metadata")

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

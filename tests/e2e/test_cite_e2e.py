"""Browser test for citing: the Cite picker (Alt+C) copies \\cite{…}, a citation and a bibliography (text/html
+ text/plain on the clipboard), the Cite menu on a paper and on a note that links papers, the topic
drawer's bibliography, and Library settings › Citations (auto-export). Nothing touches the network.

Run:  python -m pytest -q tests/e2e/test_cite_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
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

SHOTS = os.environ.get("RB_E2E_SHOTS")


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("cite_e2e")
    env = {"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0", "RB_DATA_DIR": str(tmp / "data")}
    with run_server(tmp / "vault", env=env, log_path=tmp / "server.log") as srv:
        base = srv.base
        ids = []
        for title, authors, year, venue, doi, arxiv in [
            ("Kinetic simulations of Hall thruster plasma", ["Jean-Pierre Boeuf", "Laurent Garrigues"], 2018,
             "Physics of Plasmas", "10.1063/1.5017033", ""),
            ("Neural operators for plasma turbulence", ["Jane Doe"], 2024, "", "", "2401.01234"),
            ("Ion thruster grid erosion", ["Richard E. Wirz"], 2021, "AIAA Propulsion and Energy Forum", "", ""),
        ]:
            ids.append(_call(base, "POST", "/api/notes", {"type": "paper", "title": title, "authors": authors, "year": year,
                                                          "venue": venue, "doi": doi, "arxiv": arxiv})["id"])
        note = _call(base, "POST", "/api/notes", {"type": "note", "title": "Synthesis",
                                                  "body": f"Compare [[{ids[0]}]] with [[Neural operators for plasma turbulence]]."})
        _call(base, "POST", "/api/topics", {"name": "Thrusters", "members": [ids[2], ids[0]]})
        yield {"base": base, "ids": ids, "note": note["id"], "tmp": tmp}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except PWError as e:
            exe = next((p for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium")
                        if p and Path(p).exists()), None)
            if not exe:
                pytest.skip(f"no browser for Playwright ({str(e).splitlines()[0]})")
            b = pw.chromium.launch(executable_path=exe)
        yield b
        b.close()


@pytest.fixture()
def page(server, browser):
    ctx = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    ctx.grant_permissions(["clipboard-read", "clipboard-write"], origin=server["base"])
    p = ctx.new_page()
    errors = []
    p.on("pageerror", lambda e: errors.append(str(e)))
    p.set_default_timeout(15_000)
    p.goto(server["base"] + "/")
    p.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 4 && window.__rbCite")
    yield p
    assert not errors, errors
    ctx.close()


CLIP = """async () => {
  const out = {};
  try {
    for (const item of await navigator.clipboard.read())
      for (const t of item.types) out[t] = await (await item.getType(t)).text();
  } catch (e) { return {}; }  // being written right now: try again
  return out;
}"""


def clipboard(page):
    return page.evaluate(CLIP)


def wait_clip(page, needle, kind="text/plain", timeout=10.0):
    """The clipboard once its `kind` part contains `needle` (copies finish asynchronously)."""
    import time
    end, clip = time.time() + timeout, {}
    while time.time() < end:
        clip = clipboard(page)
        if needle in clip.get(kind, ""):
            return clip
        page.wait_for_timeout(100)
    raise AssertionError(f"clipboard never contained {needle!r}: {clip}")


def test_picker_copies_cite_citation_and_bibliography(server, page):
    assert page.evaluate("RB.commands.conflicts()") == []
    assert page.evaluate("RB.commands.get('cite.pick').keys") == ["Alt+C"]
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Alt+c")
    expect(page.locator("#cite-picker")).to_be_visible()
    expect(page.locator("#cite-picker .ct-search")).to_be_focused()
    page.keyboard.type("hall thruster")
    expect(page.locator("#cite-picker .ct-list li[data-id]")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page.locator("#cite-picker .ct-list li.sel")).to_have_count(1)
    expect(page.locator("#cite-picker .ct-prev")).to_contain_text("J.-P. Boeuf and L. Garrigues")
    # add the arXiv preprint by clicking it
    page.locator("#cite-picker .ct-search").fill("neural")
    expect(page.locator("#cite-picker .ct-list li[data-id]", has_text="Neural operators")).to_have_count(1)
    page.locator("#cite-picker .ct-list li", has_text="Neural operators").click()
    expect(page.locator("#cite-picker .ct-n").first).to_have_text("2 selected")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/cite-picker.png")
    page.get_by_role("button", name="Copy \\cite{…}").click()
    clip = wait_clip(page, "\\cite{")
    assert clip["text/plain"] == "\\cite{boeuf2018kinetic,doe2024neural}"
    expect(page.locator("#cite-picker")).to_have_count(0)
    # keys are now saved with the papers
    assert _call(server["base"], "GET", f"/api/notes/{server['ids'][0]}")["_extra"]["citekey"] == "boeuf2018kinetic"
    assert "citekey: boeuf2018kinetic" in (server["tmp"] / "vault" / "notes" / f"{server['ids'][0]}.md").read_text(encoding="utf-8")

    # bibliography: formatted html + plain text together (Word / Google Docs take the html)
    page.keyboard.press("Alt+c")
    page.locator("#cite-picker .ct-search").fill("hall")
    expect(page.locator("#cite-picker .ct-list li[data-id]")).to_have_count(1)
    page.locator("#cite-picker .ct-list li[data-id]").first.click()
    page.locator("#cite-picker select").select_option("apa")
    page.get_by_role("button", name="Copy bibliography").click()
    clip = wait_clip(page, "Boeuf, J.-P., & Garrigues, L. (2018)")
    assert clip["text/plain"].startswith("Boeuf, J.-P., & Garrigues, L. (2018). Kinetic simulations of Hall thruster plasma.")
    assert '<div class="csl-entry">' in clip["text/html"] and "<i>Physics of Plasmas</i>" in clip["text/html"]
    # the chosen style became the default
    assert _call(server["base"], "GET", "/api/cite/settings")["style"] == "apa"

    # in-text citation; Mod+Enter copies the primary format
    page.keyboard.press("Alt+c")
    page.locator("#cite-picker .ct-search").fill("grid erosion")
    expect(page.locator("#cite-picker .ct-list li[data-id]")).to_have_count(1)
    page.keyboard.press("Enter")
    page.get_by_role("button", name="Copy citation").click()
    assert wait_clip(page, "(Wirz, 2021)")["text/plain"] == "(Wirz, 2021)"
    page.keyboard.press("Alt+c")
    page.locator("#cite-picker .ct-search").fill("grid erosion")
    expect(page.locator("#cite-picker .ct-list li[data-id]")).to_have_count(1)
    page.keyboard.press("Enter")
    page.keyboard.press("Control+Enter")
    assert wait_clip(page, "Wirz, R. E. (2021)")["text/plain"].startswith("Wirz, R. E. (2021). Ion thruster grid erosion.")
    # Esc closes it
    page.keyboard.press("Alt+c")
    expect(page.locator("#cite-picker")).to_be_visible()
    page.keyboard.press("Escape")
    expect(page.locator("#cite-picker")).to_have_count(0)
    _call(server["base"], "PUT", "/api/cite/settings", {"style": "american-physics-society"})


def test_panel_cite_button_note_and_topic(server, page):
    base, ids = server["base"], server["ids"]
    page.evaluate(f"window.__rb.openNote('{ids[2]}')")
    btn = page.locator("#panel .ct-btn")
    expect(btn).to_have_text("Cite")
    btn.click()
    menu = page.locator(".ct-menu")
    expect(menu).to_be_visible()
    expect(menu.locator(".ct-key input")).to_have_value("wirz2021ion")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/cite-menu.png")
    menu.get_by_role("menuitem", name="Copy reference").click()
    clip = wait_clip(page, "R. E. Wirz")
    assert clip["text/plain"] == "[1] R. E. Wirz, Ion Thruster Grid Erosion, in AIAA Propulsion and Energy Forum (2021)."
    assert "<i>AIAA Propulsion and Energy Forum</i>" in clip["text/html"]
    # edit the key from the menu
    btn.click()
    key = page.locator(".ct-menu .ct-key input")
    expect(key).to_have_value("wirz2021ion")  # the saved key has arrived
    key.fill("wirz:grid")
    key.press("Enter")
    page.wait_for_function(f"fetch('/api/cite/keys', {{method: 'POST', headers: {{'Content-Type': 'application/json'}}, body: JSON.stringify({{ids: ['{ids[2]}']}})}}).then(r => r.json()).then(r => r.latex === '\\\\cite{{wirz:grid}}')")
    page.keyboard.press("Escape")
    expect(page.locator(".ct-menu")).to_have_count(0)
    btn.click()
    page.locator(".ct-menu").get_by_role("menuitem", name="Copy \\cite{…}").click()
    assert wait_clip(page, "wirz:grid")["text/plain"] == "\\cite{wirz:grid}"
    # .bib download
    btn.click()
    with page.expect_download() as dl:
        page.locator(".ct-menu").get_by_role("menuitem", name="Download .bib").click()
    text = Path(dl.value.path()).read_text(encoding="utf-8")
    assert text.startswith("@inproceedings{wirz:grid,") and "booktitle = {AIAA Propulsion and Energy Forum}" in text

    # a note: the bibliography of the papers it links with [[ ]]
    page.evaluate(f"window.__rb.openNote('{server['note']}')")
    page.wait_for_function(f"window.__rb.S.cur && window.__rb.S.cur.id === '{server['note']}'")
    page.locator("#panel .ct-btn").click()
    page.locator(".ct-menu").get_by_role("menuitem", name="Copy bibliography").click()
    clip = wait_clip(page, "J. Doe")
    lines = clip["text/plain"].split("\n")
    assert len(lines) == 2 and lines[0].startswith("[1] J.-P. Boeuf and L. Garrigues") and "arXiv:2401.01234" in lines[1]

    # a topic: Copy bibliography and Export .bib in the drawer
    page.evaluate("window.__rb.topics.open('thrusters')")
    tools = page.locator(".td .td-tools")
    tools.get_by_role("button", name="Copy bibliography").click()
    clip = wait_clip(page, "R. E. Wirz")
    assert clip["text/plain"].count("\n") == 1 and "J.-P. Boeuf" in clip["text/plain"]
    with page.expect_download() as dl:
        tools.get_by_role("button", name="Export .bib").click()
    assert dl.value.suggested_filename == "thrusters.bib"
    bib = Path(dl.value.path()).read_text(encoding="utf-8")
    assert "@article{boeuf2018kinetic," in bib and "@inproceedings{wirz:grid," in bib


def test_settings_citations_auto_export(server, page):
    target = server["tmp"] / "thesis" / "references.bib"
    target.parent.mkdir(exist_ok=True)
    page.evaluate("window.__rbSettings.open('citations')")
    sec = page.locator("#ls-citations")
    expect(sec.locator("select").first).to_have_value("american-physics-society")
    sec.get_by_label("Path of the .bib file").fill("not/absolute.bib")
    sec.get_by_role("button", name="Add auto-export").click()
    expect(page.locator("#library-settings .ls-msg")).to_contain_text("absolute")
    sec.get_by_label("Path of the .bib file").fill(str(target))
    sec.get_by_label("BibTeX format").select_option("bibtex")
    sec.get_by_role("button", name="Add auto-export").click()
    expect(page.locator("#library-settings .ls-msg")).to_contain_text("Wrote 3 entries")
    expect(sec.locator("#ls-auto-exports li")).to_have_count(1)
    expect(sec.locator("#ls-auto-exports li .w")).to_contain_text("written")
    assert "@article{boeuf2018kinetic," in target.read_text(encoding="utf-8")
    if SHOTS:
        page.screenshot(path=f"{SHOTS}/cite-settings.png")
    # a change to a paper rewrites the file (the background exporter)
    _call(server["base"], "PUT", f"/api/notes/{server['ids'][1]}", {"title": "Neural operators for edge turbulence"})
    page.wait_for_function("() => true")
    for _ in range(100):
        if "edge turbulence" in target.read_text(encoding="utf-8"):
            break
        page.wait_for_timeout(100)
    assert "Neural operators for edge turbulence" in target.read_text(encoding="utf-8")
    sec.locator("#ls-auto-exports").get_by_role("button", name="Remove").click()
    expect(page.locator("#library-settings .ls-msg")).to_contain_text("left where it is")
    assert target.exists()

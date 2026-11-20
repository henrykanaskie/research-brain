"""Browser test for the novelty check: from a private question's panel, the preview (library only), the check
running in the background, the report with its bands and Add / In your library, and "Check novelty" on the
right-click menu of selected text in the panel.

Run:  python -m pytest -q tests/e2e/test_novelty_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
"""
from __future__ import annotations

import os

from test_questions_e2e import FIXTURE, _add_paper, _call, browser, server  # noqa: F401

from playwright.sync_api import expect


def test_novelty_check_from_a_question(server, browser):
    base, vault = server["base"], server["vault"]
    for key in ("breathing", "krcrm", "tokamak"):
        _add_paper(base, vault, key)
    q = FIXTURE["questions"]["breathing_crm"]
    n = _call(base, "POST", "/api/notes", {"type": "question", "title": q["title"], "body": q["body"]})
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 3 && window.__rbNovelty")
    assert page.evaluate("window.RB.commands.conflicts()") == []
    page.evaluate(f"window.__rb.openNote('{n['id']}')")
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.type === 'question'")

    # the preview: a private question is checked against the library only, with no searches to send
    page.locator("#oq-novelty").click()
    sheet = page.locator(".nv-sheet")
    expect(sheet.locator(".nv-note.warn")).to_contain_text("private")
    expect(sheet.locator(".nv-row input.nv-text")).to_have_count(1)  # one part, no search rows
    if shots:
        page.screenshot(path=f"{shots}/novelty-preview.png")
    sheet.get_by_role("button", name="Check novelty").click()

    # the report: papers from the library, marked as already there
    expect(sheet.locator(".nv-bands")).to_be_visible(timeout=20_000)
    expect(sheet.locator(".nv-meta")).to_contain_text("your library only")
    expect(sheet.locator(".nv-paper").first).to_be_visible()
    expect(sheet.locator(".nv-paper .btn", has_text="In your library").first).to_be_visible()
    assert FIXTURE["papers"]["krcrm"]["title"] in sheet.inner_text()
    assert FIXTURE["papers"]["tokamak"]["title"] not in sheet.inner_text()
    if shots:
        page.screenshot(path=f"{shots}/novelty-report.png")

    # opening it again shows the last report straight away
    page.keyboard.press("Escape")
    page.locator("#oq-novelty").click()
    expect(page.locator(".nv-sheet .nv-bands")).to_be_visible()
    page.keyboard.press("Escape")

    # right-click on selected text in the panel: Copy and Check novelty
    page.evaluate("""() => {
      const el = document.querySelector('#panel-body .oq-detail');
      el.focus(); el.setSelectionRange(0, 40);
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 10 }));
    }""")
    menu = page.locator(".node-menu")
    expect(menu.get_by_role("button", name="Copy")).to_be_visible()
    menu.get_by_role("button", name="Check novelty").click()
    expect(page.locator(".nv-sheet .nv-idea")).to_contain_text(q["body"][:20])
    assert errors == []


def test_novelty_report_with_parts_years_and_ai(server, browser):
    """A finished online check (as app/novelty.py writes it): part dots, years, the AI's verdict, Background folded."""
    import json as _json
    base, vault = server["base"], server["vault"]
    n = _call(base, "POST", "/api/notes", {"type": "note", "title": "Sparse attention for contact maps", "body": "Idea."})
    parts = [{"name": "Problem", "text": "protein contact maps"}, {"name": "Method", "text": "sparse attention"},
             {"name": "Setting", "text": "no alignments"}]

    def pp(t, band, lv, year, **kw):
        return {"key": "external:t:" + t, "title": t, "authors": ["A. B", "C. D"], "year": year, "venue": "J. Test", "doi": "",
                "arxiv": "2401.00001", "url": "", "access": "open", "abstract": "Abstract.", "score": 0.5, "parts": lv,
                "band": band, "why": kw.get("why", ""), "via": kw.get("via", ["search"]), "cited_by_seeds": kw.get("cb", 0),
                "cites_seeds": 0, "library_id": "", "new": kw.get("new", False), "judged": bool(kw.get("why"))}
    run = {"id": "seeded1", "note_id": n["id"], "title": n["title"], "idea": "Idea.", "parts": parts, "queries": ["a b c"],
           "online": True, "ai": True, "family": "tfidf", "started_at": "2026-11-19T10:00:00+00:00",
           "finished_at": "2026-11-19T10:02:00+00:00", "status": "done", "error": "", "stage": "done", "examined": 212,
           "chars_sent": 9000, "verdict": "Partly done: the method exists, not with this setting.",
           "uncovered": "None of them predicts contacts **without alignments**.", "errors": [],
           "counts": {"close": 1, "partial": 2, "background": 1, "examined": 212, "via_citations": 1, "in_library": 0,
                      "new": 1, "compared": True},
           "years": {"2019": 1, "2022": 1, "2024": 1},
           "papers": [pp("Close one", "close", [2, 2, 1], 2024, why="Same idea, with alignments.", new=True),
                      pp("Partial one", "partial", [2, 0, 0], 2019), pp("Partial two", "partial", [0, 2, 0], 2022),
                      pp("Classic coevolution", "background", [1, 0, 0], 2011, via=["citations"], cb=4)]}
    (vault / "state").mkdir(parents=True, exist_ok=True)
    (vault / "state" / "novelty.json").write_text(_json.dumps({"runs": [run]}), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.goto(base + "/")
    page.wait_for_function("window.__rbNovelty")
    page.evaluate(f"window.__rbNovelty.openRun('{n['id']}', 'seeded1')")
    sheet = page.locator(".nv-sheet")
    expect(sheet.locator(".nv-band.close .n")).to_have_text("1")
    expect(sheet.locator(".nv-new")).to_contain_text("1 close or overlapping paper new")
    expect(sheet.locator(".nv-verdict")).to_contain_text("without alignments")
    expect(sheet.locator(".nv-years span")).to_have_count(6)  # 2019..2024
    first = sheet.locator(".nv-paper").first
    expect(first).to_contain_text("New since last check")
    expect(first.locator(".nv-parts .p2")).to_have_count(2)
    expect(first.locator(".nv-why")).to_have_text("Same idea, with alignments.")
    expect(sheet.locator("details.nv-bg")).not_to_have_attribute("open", "")
    assert "null" not in sheet.inner_text()
    shots = os.environ.get("RB_E2E_SHOTS")
    if shots:
        sheet.locator("details.nv-bg > summary").click()
        page.screenshot(path=f"{shots}/novelty-full-report.png", full_page=True)
    assert errors == []

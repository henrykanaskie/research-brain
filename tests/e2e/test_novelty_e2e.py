"""Browser test for the novelty check, a mode of Ask: type an idea, the preview card (here library only: online
lookups are off in tests), the check running in the background, the short report in the chat and the full
report, the run kept with the conversation, and "Check novelty" on the right-click menu of selected text.

Run:  python -m pytest -q tests/e2e/test_novelty_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
"""
from __future__ import annotations

import os

from test_questions_e2e import FIXTURE, _add_paper, _call, browser, server  # noqa: F401

from playwright.sync_api import expect

IDEA = ("Predict the breathing-mode frequency of a Hall thruster from the electron temperature and density "
        "that a krypton collisional-radiative model gives.")


def test_novelty_mode_in_ask(server, browser):
    base, vault = server["base"], server["vault"]
    for key in ("breathing", "krcrm", "tokamak"):
        _add_paper(base, vault, key)
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 3 && window.__rbNovelty")
    assert page.evaluate("window.RB.commands.conflicts()") == []

    # Novelty sits next to Quick and Research in Ask's composer
    page.evaluate("window.__rb.openChat()")
    seg = page.locator("#chat .rs-seg")
    expect(seg.get_by_role("button", name="Novelty")).to_be_visible()
    seg.get_by_role("button", name="Novelty").click()
    expect(seg.get_by_role("button", name="Novelty")).to_have_attribute("aria-pressed", "true")
    expect(page.locator("#chat-input")).to_have_attribute("placeholder", "Describe your idea: the problem, your approach, the setting")

    # type the idea and send it: the preview card, nothing searched yet
    page.locator("#chat-input").fill(IDEA)
    page.locator("#btn-chat-send").click()
    card = page.locator("#chat-log .nv-msg").last
    expect(card.locator(".nv-row input.nv-text").first).to_be_visible()
    expect(page.locator("#chat-log .msg.user").last).to_contain_text("breathing-mode frequency")
    expect(card.locator(".nv-note.warn")).to_contain_text("Online lookups are turned off")
    if shots:
        page.screenshot(path=f"{shots}/novelty-chat-preview.png")
    card.get_by_role("button", name="Check novelty").click()

    # the card follows the run and ends as a short report
    expect(card.locator(".nv-bands")).to_be_visible(timeout=20_000)
    expect(card.locator(".nv-meta")).to_contain_text("your library only")
    expect(card.locator(".nv-mini").first).to_contain_text(FIXTURE["papers"]["krcrm"]["title"])
    assert FIXTURE["papers"]["tokamak"]["title"] not in card.inner_text()
    if shots:
        page.screenshot(path=f"{shots}/novelty-chat-report.png")
    # saved with the conversation: the run id and a one-line summary for the history
    saved = page.evaluate("window.__rb.S.chat.filter(m => m.novelty).map(m => m.content)")
    assert saved and saved[0].startswith("Novelty check:")

    # Full report: the sheet
    card.get_by_role("button", name="Full report").click()
    sheet = page.locator(".nv-sheet")
    expect(sheet.locator(".nv-bands")).to_be_visible()
    expect(sheet.locator(".nv-paper .btn", has_text="In your library").first).to_be_visible()
    page.keyboard.press("Escape")

    # Quick mode answers normally again
    seg.get_by_role("button", name="Quick").click()
    assert page.evaluate("window.__rbNovelty.wants()") is False

    # right-click on selected text in a note: Check novelty puts it in Ask, in Novelty mode
    n = _call(base, "POST", "/api/notes", {"type": "note", "title": "An idea", "body": "Sparse attention for contact maps without alignments."})
    page.evaluate(f"window.__rb.openNote('{n['id']}')")
    page.wait_for_function(f"window.__rb.S.cur && window.__rb.S.cur.id === '{n['id']}'")
    assert page.locator("#oq-novelty").count() == 0
    page.evaluate("""() => {
      const el = [...document.querySelectorAll('#panel-body textarea')].find(t => t.value.includes('Sparse attention'));
      el.focus(); el.setSelectionRange(0, 16);
      const r = el.getBoundingClientRect();
      el.dispatchEvent(new MouseEvent('contextmenu', { bubbles: true, cancelable: true, clientX: r.left + 20, clientY: r.top + 10 }));
    }""")
    menu = page.locator(".node-menu")
    expect(menu.get_by_role("button", name="Copy")).to_be_visible()
    menu.get_by_role("button", name="Check novelty").click()
    expect(page.locator("#chat-input")).to_have_value("Sparse attention")
    expect(seg.get_by_role("button", name="Novelty")).to_have_attribute("aria-pressed", "true")
    seg.get_by_role("button", name="Quick").click()  # leave the next tests in Quick mode
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
    page.evaluate("window.__rbNovelty.openRun('seeded1')")
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


def test_composer_fits_three_modes(server, browser):
    """Quick, Research and Novelty fit in the composer, none clipped, at the chat's usual and narrow widths."""
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rbNovelty")
    page.evaluate("window.__rb.openChat()")
    for w in (1400, 1000):
        page.set_viewport_size({"width": w, "height": 900})
        box = page.evaluate("""() => {
          const bar = document.querySelector('#chat .cmp-bar').getBoundingClientRect();
          return [...document.querySelectorAll('#chat .rs-seg button, #chat .cmp-right button')].map(b => {
            const r = b.getBoundingClientRect(); return [b.textContent.trim(), r.left >= bar.left - 1 && r.right <= bar.right + 1, b.scrollWidth <= b.clientWidth + 1];
          });
        }""")
        assert all(inside and whole for _, inside, whole in box), (w, box)
    shots = os.environ.get("RB_E2E_SHOTS")
    if shots:
        page.screenshot(path=f"{shots}/novelty-composer-narrow.png")

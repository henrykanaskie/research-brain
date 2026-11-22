"""Browser tests for open questions and the novelty check, on one server.

Open questions: create a question from the Add menu, add papers, watch evidence arrive in the background,
triage it with the keyboard (S / A / D on a focused row), check the new-evidence badge and the Questions list,
and that the triage keys never clash with other shortcuts; then a question's reach and scouted papers.

Novelty, a mode of Ask: type an idea, the preview card (here library only: online lookups are off in tests),
the check running in the background, the short report in the chat and the full report, the run kept with the
conversation, "Check novelty" on the right-click menu of selected text, the composer fitting all three modes,
and a finished online report seeded on disk.

Run:  python -m pytest -q tests/e2e/test_questions_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
"""
from __future__ import annotations

import json
import os
import time
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import run_server

from app import fsio

ROOT = Path(__file__).resolve().parents[2]
FIXTURE = json.loads((ROOT / "tests" / "fixtures" / "questions" / "plasma_corpus.json").read_text(encoding="utf-8"))

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("questions_e2e")
    env = {"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0", "RB_QUESTIONS_DEBOUNCE": "0.3", "RB_QUESTIONS_POLL": "5"}
    with run_server(tmp / "vault", env=env, log_path=tmp / "server.log") as srv:
        yield {"base": srv.base, "vault": tmp / "vault", "papers": {}}


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


def _add_paper(server, key):
    """Add fixture paper `key` with its page text, once per server (a later call returns the same id)."""
    if key in server["papers"]:
        return server["papers"][key]
    base, vault = server["base"], server["vault"]
    p = FIXTURE["papers"][key]
    n = _call(base, "POST", "/api/notes", {"type": "paper", "title": p["title"], "abstract": p["abstract"],
                                           "authors": p["authors"], "year": p["year"]})
    (vault / "text" / f"{n['id']}.txt").write_text("".join(f"[[page {i + 1}]]\n{t}\n\n" for i, t in enumerate(p["pages"])))
    _call(base, "PUT", f"/api/notes/{n['id']}", {"venue": "Test venue"})  # a save: the matcher hears about it
    server["papers"][key] = n["id"]
    return n["id"]


def _wait(fn, timeout=20.0):
    end = time.time() + timeout
    while time.time() < end:
        v = fn()
        if v:
            return v
        time.sleep(0.2)
    raise AssertionError("timed out")


IDEA = ("Predict the breathing-mode frequency of a Hall thruster from the electron temperature and density "
        "that a krypton collisional-radiative model gives.")


def _seeded_novelty_run(note):
    """A finished online novelty check, as app/novelty.py writes it: part levels, years, the AI's verdict."""
    parts = [{"name": "Problem", "text": "protein contact maps"}, {"name": "Method", "text": "sparse attention"},
             {"name": "Setting", "text": "no alignments"}]

    def pp(t, band, lv, year, **kw):
        return {"key": "external:t:" + t, "title": t, "authors": ["A. B", "C. D"], "year": year, "venue": "J. Test", "doi": "",
                "arxiv": "2401.00001", "url": "", "access": "open", "abstract": "Abstract.", "score": 0.5, "parts": lv,
                "band": band, "why": kw.get("why", ""), "via": kw.get("via", ["search"]), "cited_by_seeds": kw.get("cb", 0),
                "cites_seeds": 0, "library_id": "", "new": kw.get("new", False), "judged": bool(kw.get("why"))}
    return {"id": "seeded1", "note_id": note["id"], "title": note["title"], "idea": "Idea.", "parts": parts, "queries": ["a b c"],
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


def test_novelty_mode_in_ask_and_full_report(server, browser):
    """The Novelty mode of Ask from start to finish: the composer fits Quick, Research and Novelty at the chat's
    usual and narrow widths, an idea gets a preview card, then a library-only report in the chat, saved with the
    conversation, and a full report sheet; Quick answers normally again; right-click "Check novelty" on selected
    note text puts it in Ask in Novelty mode; and a finished online run seeded on disk shows part dots, years,
    the AI's verdict and a folded Background section."""
    base, vault = server["base"], server["vault"]
    for key in ("breathing", "krcrm", "tokamak"):
        _add_paper(server, key)
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 3 && window.__rbNovelty")
    assert page.evaluate("window.RB.commands.conflicts()") == []

    # the three modes fit in the composer, none clipped, at the chat's usual and narrow widths
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
    if shots:
        page.screenshot(path=f"{shots}/novelty-composer-narrow.png")
    page.set_viewport_size({"width": 1400, "height": 900})

    # Novelty sits next to Quick and Research in Ask's composer
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
    seg.get_by_role("button", name="Quick").click()
    page.close()

    # a finished online check on disk: part dots, years, the AI's verdict, Background folded
    n = _call(base, "POST", "/api/notes", {"type": "note", "title": "Sparse attention for contact maps", "body": "Idea."})
    (vault / "state").mkdir(parents=True, exist_ok=True)
    (vault / "state" / "novelty.json").write_text(json.dumps({"runs": [_seeded_novelty_run(n)]}), encoding="utf-8")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
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
    if shots:
        sheet.locator("details.nv-bg > summary").click()
        page.screenshot(path=f"{shots}/novelty-full-report.png", full_page=True)
    assert errors == []
    page.close()


def test_question_evidence_triage_badge_list(server, browser):
    base, vault = server["base"], server["vault"]
    for key in ("tokamak", "sheath"):  # unrelated papers already in the library
        _add_paper(server, key)
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 2 && window.__rbQuestions")
    assert page.evaluate("window.RB.commands.conflicts()") == []

    # New question from the Add menu: the title is focused, in the display serif
    page.locator("#btn-add-menu").click()
    page.locator("#btn-new-question").click()
    page.wait_for_function("window.__rb.S.cur && window.__rb.S.cur.type === 'question'")
    expect(page.locator("#panel-type")).to_contain_text("Question")
    title = page.locator("#panel-body .title-input.oq-title")
    expect(title).to_be_focused()
    page.keyboard.press("Control+A")
    q = FIXTURE["questions"]["breathing_crm"]
    page.keyboard.type(q["title"])
    page.locator("#panel-body .oq-detail").fill(q["body"])
    page.locator("#oq-answer").fill("Maybe: $f \\propto \\sqrt{V_i V_n}/L$ and $T_e$ sets the ionization rate.")
    qid = page.evaluate("window.__rb.S.cur.id")
    page.wait_for_function(f"fetch('/api/notes/{qid}').then(r => r.json()).then(n => n.answer.includes('sqrt') && n.title.startsWith('Could'))")
    expect(page.locator("#oq-status .oq-private")).to_contain_text("no AI sees it")
    expect(page.locator("#oq-ai .oq-ai-off")).to_contain_text("private")
    expect(page.locator("#oq-ai button").first).to_be_disabled()
    expect(page.locator("#oq-evidence .oq-empty")).to_contain_text("No evidence yet")

    # add the relevant papers: evidence arrives in the background
    for key in ("breathing", "krcrm", "oes"):
        _add_paper(server, key)
    idea = FIXTURE["notes"]["idea_te"]
    _call(base, "POST", "/api/notes", {"type": "note", "title": idea["title"], "body": idea["body"]})
    ev = _wait(lambda: (lambda r: r if r["counts"]["new"] >= 4 else None)(_call(base, "GET", f"/api/questions/{qid}/evidence")))
    titles = {i["source"]["title"] for i in ev["items"]}
    assert FIXTURE["papers"]["breathing"]["title"] in titles and FIXTURE["papers"]["krcrm"]["title"] in titles
    assert not titles & {FIXTURE["papers"]["tokamak"]["title"], FIXTURE["papers"]["sheath"]["title"]}

    # the badge in the top bar shows the new count
    page.evaluate("window.__rbQuestions.refreshBadge(0)")
    expect(page.locator("#btn-questions .oq-badge")).to_have_text(str(ev["counts"]["new"]))
    page.evaluate(f"window.__rbQuestions.loadEvidence('{qid}')")
    expect(page.locator("#oq-since")).to_contain_text(f"{ev['counts']['new']} new since you last looked")
    rows = page.locator("#oq-evidence .oq-grp[data-v='new'] .oq-ev")
    expect(rows).to_have_count(ev["counts"]["untriaged"])
    expect(page.locator("#oq-evidence .oq-ev .oq-where").first).to_contain_text("p.")
    if shots:
        page.screenshot(path=f"{shots}/question-evidence.png")

    # keyboard triage: focus the first row, S = supports, then A = against on the next, D = dismiss
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.evaluate("window.RB.commands.run('question.triage')")
    first = page.evaluate("document.activeElement.dataset.eid")
    assert first
    page.keyboard.press("s")
    page.wait_for_function(f"document.querySelector('#oq-evidence .oq-grp[data-v=supports] .oq-ev[data-eid=\"{first}\"]')")
    second = page.evaluate("document.activeElement.dataset.eid")
    assert second and second != first  # focus moved on to the next piece to triage
    page.keyboard.press("a")
    page.wait_for_function(f"document.querySelector('#oq-evidence .oq-grp[data-v=against] .oq-ev[data-eid=\"{second}\"]')")
    third = page.evaluate("document.activeElement.dataset.eid")
    page.keyboard.press("d")
    page.wait_for_function(f"document.querySelector('#oq-evidence .oq-grp[data-v=dismissed] .oq-ev[data-eid=\"{third}\"]')")
    # the S / A / D keys did not trigger the panel's other shortcuts (write summary, ask, ...)
    assert page.locator("#chat").is_hidden()
    # the row moves at once and the save follows: wait for it to reach the file
    for _ in range(40):
        # read as the app does: on Windows the file can be mid-replace for an instant (a sharing violation)
        data = json.loads(fsio.read_text(vault / "state" / "questions" / f"{qid}.json"))
        verdicts = {i["id"]: i["verdict"] for i in data["items"]}
        if verdicts[third] == "dismissed":
            break
        page.wait_for_timeout(100)
    assert verdicts[first] == "supports" and verdicts[second] == "against" and verdicts[third] == "dismissed"

    # a note on the kept evidence is saved
    page.locator(f"#oq-evidence .oq-ev[data-eid='{first}'] .oq-note").fill("Frequency set by the ionization zone length")
    page.wait_for_function(f"fetch('/api/questions/{qid}/evidence').then(r => r.json()).then(d => d.items.some(i => i.id === '{first}' && i.user_note.startsWith('Frequency')))")

    # the graph data has the question node and its evidence links (the lead draws them)
    g = _call(base, "GET", "/api/graph")
    qn = next(n for n in g["nodes"] if n["id"] == qid)
    assert qn["type"] == "question" and "new_evidence" in qn
    assert {(l["q"], l["verdict"]) for l in g["question_links"]} >= {(qid, "supports"), (qid, "against")}

    # Mark all seen clears the badge; untriaged evidence stays under To triage
    page.locator("#oq-seen").click()
    expect(page.locator("#oq-since")).to_contain_text("Nothing new")
    page.evaluate("window.__rbQuestions.refreshBadge(0)")
    expect(page.locator("#btn-questions .oq-badge")).to_be_hidden()

    # the Questions list (Shift+Q), with the question and its counts
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("Shift+Q")
    row = page.locator(f".ql-row[data-id='{qid}']")
    expect(row).to_be_visible()
    expect(row.locator(".ql-sub")).to_contain_text("2 kept")
    if shots:
        page.screenshot(path=f"{shots}/questions-list.png")
    row.click()
    page.wait_for_function(f"window.__rb.S.cur && window.__rb.S.cur.id === '{qid}'")

    # the status control: parked questions stop collecting
    page.locator("#oq-status button[data-qs=parked]").click()
    page.wait_for_function(f"fetch('/api/notes/{qid}').then(r => r.json()).then(n => n.question_status === 'parked')")
    # Library settings › Open questions: the threshold and the daily cap
    page.evaluate("window.__rbSettings.open()")
    page.locator("#library-settings .ls-tab", has_text="AI and privacy").click()
    sec = page.locator("#ls-questions")
    expect(sec.locator("input[type=range]")).to_be_visible()
    sec.locator("input[type=number]").fill("4")
    sec.locator("input[type=number]").dispatch_event("change")
    page.wait_for_function("fetch('/api/questions/settings').then(r => r.json()).then(s => s.daily_cap === 4)")
    assert not errors, errors
    page.close()


def test_question_reach_and_scouted_papers(server, browser):
    base = server["base"]
    q = _call(base, "POST", "/api/notes", {"type": "question", "title": "Does wall erosion change the breathing mode?",
                                           "body": "Boron nitride erosion over long tests."})
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.on("dialog", lambda d: d.accept())  # the Scout confirmation
    page.set_default_timeout(15_000)

    def with_external(route):
        r = route.fetch()
        data = r.json()
        data["items"].append({"id": "ext1", "key": "external:doi:10.1/x", "kind": "external", "verdict": "new", "new": True,
                              "score": 0.5, "snippet": "We measured channel widening over 7000 h.", "title": "Erosion and breathing",
                              "where": "", "href": "https://doi.org/10.1/x", "user_note": "",
                              "ref": {"doi": "10.1/x", "arxiv": "", "url": "https://doi.org/10.1/x"},
                              "source": {"id": "", "title": "Erosion and breathing", "type": "paper", "year": 2025, "authors": ["A. B", "C. D"],
                                         "exists": False, "external": True, "venue": "J. Prop.", "access": "closed", "found_via": "OpenAlex",
                                         "doi": "10.1/x", "arxiv": ""}})
        route.fulfill(response=r, json=data)
    page.route(f"**/api/questions/{q['id']}/evidence", with_external)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rbQuestions")
    page.evaluate(f"window.__rb.openNote({json.dumps(q['id'])})")
    reach = page.locator("#oq-status .oq-reach")
    expect(reach.locator("[data-reach=private]")).to_have_attribute("aria-checked", "true")
    reach.locator("[data-reach=library]").click()
    expect(page.locator("#oq-reach-say")).to_contain_text("AI features may read it")
    expect(page.locator("#oq-ai button").first).to_be_enabled()
    reach.locator("[data-reach=scout]").click()
    expect(page.locator(".oq-scout-line")).to_contain_text("Scouting published papers for")
    assert "erosion" in page.locator(".oq-scout-line q").inner_text().lower()
    row = page.locator(".oq-ev[data-kind=external]")
    expect(row.locator(".oq-ext")).to_contain_text("Paywalled")
    expect(row.locator(".oq-ext button")).to_have_text("Add to library")
    expect(row).to_contain_text("A. B et al. · 2025 · J. Prop.")
    reach.locator("[data-reach=agent]").click()
    expect(page.locator("#oq-agent")).to_have_text("Run agent search")
    expect(page.locator(".oq-scout-line").nth(1)).to_contain_text("AI agent search, weekly")
    n = _call(base, "GET", f"/api/notes/{q['id']}")
    assert n.get("private") in (False, None) or n.get("private") is False
    reach.locator("[data-reach=private]").click()
    _wait(lambda: _call(base, "GET", f"/api/questions/{q['id']}/evidence")["question"]["reach"] == "private")
    assert errors == []

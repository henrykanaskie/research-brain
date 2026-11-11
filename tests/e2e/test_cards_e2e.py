"""Browser test for idea cards: suggest ideas from a paper (fake agent), review them with the keyboard
(J/K move, Y confirm, X reject, E edit), add one by hand, then the Ideas view: the quantity table with a
conflict badge, sorting, the CSV link, and Esc. Reuses the server/browser fixtures from test_e2e.py.
"""
from __future__ import annotations

import os
import time
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import api, browser, clear_vault, ctx, open_app, server  # noqa: E402,F401

MOD = "Meta" if os.uname().sysname == "Darwin" else "Control"

TEXT_A = """[[page 1]]
Magnetic shielding strongly reduces erosion of the discharge channel walls in this thruster.

[[page 2]]
The electron temperature in the plume was 20 eV at the exit plane.

We used a Langmuir probe mounted on a radial arm for every measurement here.
"""
TEXT_B = """[[page 3]]
Ion beam focusing improved markedly when the magnetic field was optimised for this study.

[[page 4]]
The electron temperature in the plume was 35 eV at the exit plane.

A Langmuir probe measured the plasma properties across the whole plume.
"""


def paper(server, title, text):
    n = api(server, "POST", "/api/notes", {"type": "paper", "title": title})
    (Path(server["vault"]) / "text" / f"{n['id']}.txt").write_text(text, encoding="utf-8")
    return n["id"]


def extract_now(server, pid):
    j = api(server, "POST", f"/api/papers/{pid}/cards/extract")
    t0 = time.time()
    while time.time() - t0 < 20:
        s = api(server, "GET", f"/api/cards/jobs/{j['job_id']}")
        if s["state"] != "running":
            return s
        time.sleep(0.1)
    raise AssertionError("extraction didn't finish")


def shot(page, name):
    d = os.environ.get("RB_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"))


def blur(page):
    page.evaluate("document.activeElement && document.activeElement.blur()")


def test_ideas_review_flow_and_view(ctx, server):
    clear_vault(server)
    a = paper(server, "Shielded thruster plume study", TEXT_A)
    b = paper(server, "Optimised field plume study", TEXT_B)
    page = open_app(ctx, server)
    assert page.evaluate("RB.commands.conflicts()") == []
    page.wait_for_function("(id) => window.__rb.S.nodes.has(id)", arg=a)
    page.evaluate("(id) => window.__rb.openNote(id)", a)
    sec = page.locator("#ideas-sec")
    expect(sec).to_be_visible()
    expect(sec).to_contain_text("Suggest them from the paper, or add your own")

    # suggest: the fake agent proposes 4 cards; the one whose quote isn't in the paper is dropped
    sec.locator("[data-act=ideas-suggest]").click()
    cards = sec.locator(".ic[data-card]")
    expect(cards).to_have_count(3)
    expect(sec.locator(".ic.sug")).to_have_count(3)
    expect(sec).to_contain_text("The electron temperature in the plume was 20 eV")
    expect(sec.locator(".ic-meta .tnum", has_text="p. 2").first).to_be_visible()  # no PDF here, so not a link

    # keyboard review
    blur(page)
    page.keyboard.press("j")
    expect(cards.nth(0)).to_have_class("ic sug on")
    page.keyboard.press("y")
    expect(cards.nth(0)).to_have_attribute("data-status", "confirmed")
    # the next suggestion (the quantity) is selected; J skips to the method, X rejects it (hidden, can be shown)
    expect(sec.locator(".ic.on")).to_have_attribute("data-status", "suggested")
    expect(sec.locator(".ic.on")).to_contain_text("20 eV")
    page.keyboard.press("j")
    expect(sec.locator(".ic.on")).to_contain_text("Langmuir probe")
    page.keyboard.press("x")
    expect(sec.locator(".ic[data-card]")).to_have_count(2)
    expect(sec.get_by_role("button", name="Show 1 rejected")).to_be_visible()
    # back on the quantity: edit it (an edited suggestion counts as confirmed)
    expect(sec.locator(".ic.on")).to_contain_text("20 eV")
    page.keyboard.press("k")
    expect(sec.locator(".ic.on")).to_have_attribute("data-status", "confirmed")
    page.keyboard.press("j")
    page.keyboard.press("e")
    form = sec.locator("form.ic-form")
    expect(form).to_be_visible()
    ta = form.locator("textarea").first
    ta.fill("My wording of this idea.")
    ta.press(f"{MOD}+Enter")
    expect(form).to_have_count(0)
    expect(sec).to_contain_text("My wording of this idea.")
    statuses = sorted(sec.locator(".ic[data-card]").evaluate_all("els => els.map((e) => e.dataset.status)"))
    assert statuses == ["confirmed", "edited"], statuses

    # add a card by hand
    sec.locator("[data-act=ideas-add]").click()
    form = sec.locator("form.ic-form")
    form.locator("select").select_option("claim")
    form.locator("textarea").first.fill("The sheath is what matters most here.")
    form.get_by_role("button", name="Add card").click()
    expect(sec).to_contain_text("The sheath is what matters most here.")
    expect(sec.locator(".ic", has_text="The sheath is what matters").locator(".st")).to_have_text("Yours")
    sec.scroll_into_view_if_needed()
    shot(page, "ideas-panel")

    # the second paper disagrees (35 eV vs 20 eV): the Ideas view flags it
    assert extract_now(server, b)["added"] == 3
    blur(page)
    page.keyboard.press("g")
    page.keyboard.press("i")
    view = page.locator("section.iv")
    expect(view).to_be_visible()
    rows = view.locator("tbody tr")
    expect(rows).to_have_count(2)
    expect(view.locator(".bad", has_text="Disagrees")).to_have_count(2)
    expect(view.locator("th", has_text="SI")).to_be_visible()
    shot(page, "ideas-view")
    view.locator("th[data-col=value]").click()
    first = rows.nth(0).locator("td").nth(1)
    expect(first).to_have_text("20")
    view.locator("th[data-col=value]").click()
    expect(rows.nth(0).locator("td").nth(1)).to_have_text("35")
    href = view.locator("[data-act=ideas-csv]").get_attribute("href")
    assert href.startswith("/api/cards/export.csv?") and "kind=quantity" in href
    view.locator("input[type=search]").fill("optimised field")
    expect(rows).to_have_count(1)
    view.locator(".seg button", has_text="Claims").click()
    view.locator("input[type=search]").fill("")
    expect(view.locator("tbody tr").first).to_be_visible()
    blur(page)
    page.keyboard.press("Escape")
    expect(view).to_have_count(0)
    assert page.evaluate("RB.commands.conflicts()") == []
    assert not page.errors, page.errors

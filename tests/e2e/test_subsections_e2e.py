"""Subsections (app/subsections.py): named groups of papers inside a region, whose names show only while that region
is focused; the focus bar's Subsections sheet renames and deletes them and asks the AI to organize the region, and the
AI's proposal is a card with Apply. Also: the legend fits again when a side window closes.

Run:  python -m pytest -q tests/e2e/test_subsections_e2e.py
"""
from __future__ import annotations

import json

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import api, clear_vault, open_app  # noqa: E402
from test_region_edit_e2e import _ask_for  # noqa: E402


def test_subsection_names_show_only_in_their_focused_region_and_the_ai_proposes_them(ctx, server):
    clear_vault(server)
    kr = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Krypton collisional radiative model {i}",
                                             "abstract": "collisional radiative model krypton emission"})["id"] for i in range(8)]
    ha = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster breathing mode {i}",
                                             "abstract": "hall thruster breathing oscillation discharge"})["id"] for i in range(4)]
    api(server, "POST", "/api/topics", {"name": "Krypton models", "members": kr})
    api(server, "POST", "/api/topics", {"name": "Hall thrusters", "members": ha})
    s = api(server, "POST", "/api/subsections", {"name": "Metastable levels", "ids": kr[:4]})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 12 && (window.__rb.S.subsections || []).length === 1")
    page.wait_for_timeout(1500)
    assert page.evaluate("window.__rb.S.subShown") == []  # the whole-map view: no subsection names
    cid = page.evaluate(f"window.__rb.S.nodes.get('{kr[0]}').cluster")
    other = page.evaluate(f"window.__rb.S.nodes.get('{ha[0]}').cluster")
    page.evaluate(f"window.__rb.focusRegion({other})")
    page.wait_for_timeout(900)
    assert page.evaluate("window.__rb.S.subShown") == []  # another region focused: still none
    page.evaluate(f"window.__rb.focusRegion({cid})")
    page.wait_for_function("(window.__rb.S.subShown || []).includes('Metastable levels')")
    page.wait_for_timeout(1500)  # the zoom to it has finished
    page.screenshot(path=str(server["vault"].parent / "subsections-focused.png"))

    # pick it (its name in the sheet, or on the map): its papers lead and the region's others step back
    page.click("#rf-subs")
    page.locator(".rf-chooser .rf-sub-pick").click()
    sub = page.evaluate(f"window.__rb.S.nodes.get('{kr[0]}').sub")
    assert page.evaluate("window.__rb.S.focusSub") == sub
    page.wait_for_timeout(600)
    box = page.evaluate("window.__rb.S.subBoxes[0].box")
    g = page.locator("#graph").bounding_box()
    page.mouse.click(g["x"] + (box[0] + box[2]) / 2, g["y"] + (box[1] + box[3]) / 2)  # on the map: let it go
    page.wait_for_function("window.__rb.S.focusSub == null")
    page.mouse.move(g["x"] + (box[0] + box[2]) / 2, g["y"] + (box[1] + box[3]) / 2)
    page.wait_for_function(f"window.__rb.S.hoverSub === {sub}")
    page.mouse.move(g["x"] + 5, g["y"] + 5)
    page.wait_for_function("window.__rb.S.hoverSub == null")

    # the sheet: rename, then delete
    page.click("#rf-subs")
    sheet = page.locator(".rf-chooser")
    expect(sheet).to_contain_text("Subsections of Krypton models")
    expect(sheet.locator(".rf-sub")).to_contain_text("Metastable levels")
    sheet.get_by_role("button", name="Rename").click()
    sheet.locator(".rf-input").fill("Metastables")
    page.keyboard.press("Enter")
    expect(page.locator(".rf-chooser .rf-sub")).to_contain_text("Metastables")
    assert [x["name"] for x in api(server, "GET", "/api/subsections")] == ["Metastables"]
    page.locator(".rf-chooser").get_by_role("button", name="Delete").click()
    expect(page.locator(".rf-chooser")).to_contain_text("None yet")
    assert api(server, "GET", "/api/subsections") == []
    page.wait_for_function("(window.__rb.S.subShown || []).length === 0")

    # Organize with AI asks the library chat (which sees the region's papers), quick, even in Research mode
    page.evaluate("localStorage.setItem('rb.chatMode', JSON.stringify('research'))")
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 12")
    page.evaluate(f"window.__rb.focusRegion({cid})")
    page.click("#rf-subs")
    with page.expect_request("**/api/chat/stream") as req:
        page.locator(".rf-chooser").get_by_role("button", name="Organize with AI").click()
    assert "Organize the region “Krypton models” into subsections." in req.value.post_data_json["question"]
    expect(page.locator("#chat-log .msg.assistant").last).not_to_have_class("pending", timeout=20_000)
    page.wait_for_function("!window.__rb.S.chatBusy")

    # the AI proposes subsections: a card, nothing changes until Apply
    page.evaluate("localStorage.setItem('rb.chatMode', JSON.stringify('quick'))")
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 12")
    block = json.dumps({"changes": [{"op": "subsections", "region": "Krypton models", "subsections": [
        {"name": "Models", "ids": kr[:5]}, {"name": "Measurements", "ids": kr[5:]}]}]})
    page.keyboard.press("Escape")
    page.locator("#btn-chat").click()
    page.locator("#chat-input").fill(_ask_for(f"Two subsections.\n\n```rb-regions\n{block}\n```"))
    page.keyboard.press("Enter")
    card = page.locator("#chat-log .msg.assistant").last.locator(".re-card")
    expect(card).to_contain_text("Subsections for “Krypton models”: 2")
    expect(card).to_contain_text("Measurements (3)")
    assert api(server, "GET", "/api/subsections") == []
    card.get_by_role("button", name="Apply").click()
    expect(card).to_contain_text("Applied.")
    assert sorted(x["name"] for x in api(server, "GET", "/api/subsections")) == ["Measurements", "Models"]
    page.wait_for_function("(window.__rb.S.subsections || []).length === 2")
    card.get_by_role("button", name="Undo").click()
    expect(card).to_contain_text("Undone.")
    assert api(server, "GET", "/api/subsections") == []
    assert not page.errors


def test_the_legend_fits_again_after_the_chat_closes(ctx, server):
    """Worked out while the chat was open (the map reloaded then), the legend stayed that narrow after the chat closed
    and showed only "Paper, un". Without room it now drops whole entries, never half a word."""
    clear_vault(server)
    for i in range(3):
        api(server, "POST", "/api/notes", {"type": "paper", "title": f"Langmuir probe sheath {i}", "abstract": "probe sheath"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 3")
    first = page.locator("#legend > div").first
    expect(first).to_be_visible()
    wide = page.evaluate("document.querySelector('#legend').clientWidth")
    page.locator("#btn-chat").click()
    page.evaluate("window.__rb.refreshGraph()")
    page.wait_for_timeout(500)
    page.evaluate("window.__rb.closeChat()")
    page.wait_for_timeout(600)
    assert page.evaluate("document.querySelector('#legend').clientWidth") >= wide - 2
    cut = page.evaluate("""() => { const lg = document.querySelector('#legend'), r = lg.getBoundingClientRect();
        return [...lg.children].filter(el => el.offsetParent && el.getBoundingClientRect().right > r.right + 1).length; }""")
    assert cut == 0
    page.set_viewport_size({"width": 760, "height": 800})
    page.wait_for_timeout(400)
    cut = page.evaluate("""() => { const lg = document.querySelector('#legend'), r = lg.getBoundingClientRect();
        return [...lg.children].filter(el => el.offsetParent && el.getBoundingClientRect().right > r.right + 1).length; }""")
    assert cut == 0
    assert not page.errors


def test_you_make_subsections_by_hand_in_the_sheet_and_from_a_papers_menu(ctx, server):
    """New subsection: a name and the region's papers ticked; Papers changes which are in it; right-click a paper to
    put it in one, take it out, or start a new one with it."""
    clear_vault(server)
    kr = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Krypton collisional radiative model {i}",
                                             "abstract": "collisional radiative model krypton emission"})["id"] for i in range(8)]
    api(server, "POST", "/api/topics", {"name": "Krypton spectra", "members": kr})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb.S.nodes.size >= 8")
    page.wait_for_timeout(1500)
    cid = page.evaluate(f"window.__rb.S.nodes.get('{kr[0]}').cluster")
    page.evaluate(f"window.__rb.focusRegion({cid})")
    page.click("#rf-subs")
    page.locator(".rf-chooser").get_by_role("button", name="New subsection").click()
    ed = page.locator(".rf-editor")
    expect(ed).to_contain_text("New subsection of Krypton spectra")
    expect(ed.locator(".rf-pick")).to_have_count(8)
    ed.locator(".rf-sub-name").fill("Metastables")
    for t in ("model 0", "model 1", "model 2"):
        ed.locator(".rf-pick", has_text=t).locator("input").check()
    expect(ed).to_contain_text("3 papers")
    ed.locator(".rf-filter").fill("model 7")
    expect(ed.locator(".rf-pick:visible")).to_have_count(1)
    ed.locator(".rf-filter").fill("")
    ed.get_by_role("button", name="Make subsection").click()
    expect(page.locator(".rf-chooser .rf-sub")).to_contain_text("Metastables")
    (s,) = api(server, "GET", "/api/subsections")
    assert s["name"] == "Metastables" and sorted(s["members"]) == sorted(kr[:3])
    page.wait_for_function("(window.__rb.S.subShown || []).includes('Metastables')")

    # Papers: one out, one in
    page.locator(".rf-chooser .rf-sub").get_by_role("button", name="Papers").click()
    ed = page.locator(".rf-editor")
    ed.locator(".rf-pick", has_text="model 0").locator("input").uncheck()
    ed.locator(".rf-pick", has_text="model 5").locator("input").check()
    ed.get_by_role("button", name="Save").click()
    expect(page.locator(".rf-chooser .rf-sub")).to_be_visible()
    (s,) = api(server, "GET", "/api/subsections")
    assert sorted(s["members"]) == sorted([kr[1], kr[2], kr[5]])
    page.keyboard.press("Escape")

    def right_click(i):
        for _ in range(40):  # the layout settles after a change: wait until the dot stays put
            a = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y]; }", i)
            page.wait_for_timeout(250)
            b = page.evaluate("(id) => { const n = window.__rb.S.nodes.get(id); return [n.x, n.y]; }", i)
            if abs(a[0] - b[0]) + abs(a[1] - b[1]) < 0.05:
                break
        sx, sy = page.evaluate("(id) => { const fg = window.__rb.fg, n = window.__rb.S.nodes.get(id); const q = fg.graph2ScreenCoords(n.x, n.y); return [q.x, q.y]; }", i)
        box = page.locator("#graph").bounding_box()
        page.mouse.move(box["x"] + sx - 30, box["y"] + sy - 20)
        page.mouse.move(box["x"] + sx, box["y"] + sy, steps=6)
        page.wait_for_timeout(200)
        page.mouse.click(box["x"] + sx, box["y"] + sy, button="right")
        menu = page.locator(".node-menu")
        expect(menu.locator(".menu-subs .menu-label")).to_be_visible()
        return menu

    menu = right_click(kr[6])
    menu.locator(".menu-subs .menu-item", has_text="Put in Metastables").click()
    page.wait_for_timeout(500)
    assert kr[6] in api(server, "GET", "/api/subsections")[0]["members"]
    menu = right_click(kr[6])
    menu.locator(".menu-subs .menu-item", has_text="Take out of Metastables").click()
    page.wait_for_timeout(500)
    assert kr[6] not in api(server, "GET", "/api/subsections")[0]["members"]
    menu = right_click(kr[7])
    menu.locator(".menu-subs .menu-item", has_text="New subsection").click()
    ed = page.locator(".rf-editor")
    expect(ed.locator(".rf-pick input:checked")).to_have_count(1)
    ed.locator(".rf-sub-name").fill("Benchmarks")
    page.keyboard.press("Enter")
    expect(page.locator(".rf-chooser .rf-sub", has_text="Benchmarks")).to_be_visible()
    assert {x["name"]: x["members"] for x in api(server, "GET", "/api/subsections")}["Benchmarks"] == [kr[7]]
    assert not page.errors

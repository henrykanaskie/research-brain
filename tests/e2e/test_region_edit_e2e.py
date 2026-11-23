"""Ask the chat to change your regions: its answer proposes the changes as a card (not as JSON text), and nothing
changes until you click Apply; Undo puts the regions back (app/region_edit.py, static/regionedit.js).

Run:  python -m pytest -q tests/e2e/test_region_edit_e2e.py
"""
from __future__ import annotations

import base64
import json

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import api, clear_vault  # noqa: E402


def _ask_for(md: str) -> str:
    return "Move it please FAKE-ANSWER:" + base64.urlsafe_b64encode(md.encode()).decode()


def test_the_chat_proposes_region_changes_and_you_apply_and_undo_them(ctx, server):
    clear_vault(server)
    kr = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Krypton collisional radiative model {i}",
                                             "abstract": "collisional radiative model krypton emission"})["id"] for i in range(5)]
    ha = [api(server, "POST", "/api/notes", {"type": "paper", "title": f"Hall thruster breathing mode {i}",
                                             "abstract": "hall thruster breathing oscillation discharge"})["id"] for i in range(3)]
    api(server, "POST", "/api/topics", {"name": "Krypton models", "members": kr})
    api(server, "POST", "/api/topics", {"name": "Hall thrusters", "members": ha})
    p = kr[0]
    block = json.dumps({"changes": [{"op": "move", "ids": [p], "to": "Hall thrusters", "from": "Krypton models"},
                                    {"op": "rename", "region": "Krypton models", "to": "Krypton CR models"}]})
    answer = f"I'd move that paper and rename the region.\n\n```rb-regions\n{block}\n```"

    page = ctx.new_page()
    page.errors = []
    page.on("pageerror", lambda e: page.errors.append(str(e)))
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 8")
    page.evaluate("localStorage.setItem('rb.chatMode', JSON.stringify('quick'))")
    page.locator("#btn-chat").click()
    page.locator("#chat-input").fill(_ask_for(answer))
    page.keyboard.press("Enter")
    msg = page.locator("#chat-log .msg.assistant").last
    card = msg.locator(".re-card")
    expect(card).to_be_visible(timeout=20_000)
    expect(msg.locator(".msg-body")).to_contain_text("I'd move that paper")
    expect(msg.locator(".msg-body")).not_to_contain_text("rb-regions")
    expect(msg.locator(".msg-body")).not_to_contain_text('"op"')
    expect(card).to_contain_text("Move “Krypton collisional radiative model 0” to “Hall thrusters”")
    expect(card).to_contain_text("Rename “Krypton models” to “Krypton CR models”")
    # nothing has changed yet
    assert sorted(t["name"] for t in api(server, "GET", "/api/topics")) == ["Hall thrusters", "Krypton models"]
    same = f"window.__rb.S.nodes.get('{p}').cluster === window.__rb.S.nodes.get('{{}}').cluster"
    assert page.evaluate(same.format(kr[1]))

    card.get_by_role("button", name="Apply").click()
    expect(card).to_contain_text("Applied.")
    page.wait_for_function(same.format(ha[0]))
    assert sorted(t["name"] for t in api(server, "GET", "/api/topics")) == ["Hall thrusters", "Krypton CR models"]
    # still there after the chat is drawn again (the choice is kept on the message)
    page.evaluate("window.__rb.renderChat()")
    expect(msg.locator(".re-card")).to_contain_text("Applied.")
    expect(msg.locator(".re-card")).not_to_contain_text("Can't")  # what was done, not checked again after the rename

    msg.locator(".re-card").get_by_role("button", name="Undo").click()
    expect(msg.locator(".re-card")).to_contain_text("Undone.")
    page.wait_for_function(same.format(kr[1]))
    assert sorted(t["name"] for t in api(server, "GET", "/api/topics")) == ["Hall thrusters", "Krypton models"]
    # copying the answer copies the words, not the JSON
    assert "rb-regions" not in page.evaluate("window.__rbRegionEdit.strip(window.__rb.S.chat[window.__rb.S.chat.length - 1].content)")
    assert not page.errors

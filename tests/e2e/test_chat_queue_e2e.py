"""Queue a message while the AI is answering: Enter adds it under the answer, marked Queued, and it is sent
once the answer finishes, in order. A queued message can be removed or taken back into the box to edit.

Run:  python -m pytest -q tests/e2e/test_chat_queue_e2e.py
"""
from __future__ import annotations

import json
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

# hold chat answers until the test lets them go, so there is time to queue behind one
HOLD = """
(() => {
  const real = window.fetch.bind(window);
  window.__holdChat = () => { window.__held = new Promise((r) => { window.__release = r; }); };
  window.fetch = async (url, opts) => {
    if (String(url).includes('/api/chat') && window.__held) await window.__held;
    return real(url, opts);
  };
})();
"""


def _chat(ctx, server):
    req = urllib.request.Request(server["base"] + "/api/notes", method="POST", headers={"Content-Type": "application/json"},
                                 data=json.dumps({"type": "note", "title": "Hall thrusters", "body": "Breathing mode."}).encode())
    urllib.request.urlopen(req, timeout=30).close()  # chat needs something in the library to answer from
    page = ctx.new_page()
    page.add_init_script(HOLD)
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.S")
    page.evaluate("localStorage.setItem('rb.chatMode', JSON.stringify('quick'))")
    page.locator("#btn-chat").click()
    expect(page.locator("#chat")).to_be_visible()
    return page


def _ask(page, text):
    page.locator("#chat-input").fill(text)
    page.keyboard.press("Enter")


def test_queued_messages_send_in_turn(ctx, server):
    page = _chat(ctx, server)
    log = page.locator("#chat-log")
    page.evaluate("window.__holdChat()")
    _ask(page, "First question")
    page.wait_for_function("window.__rb.S.chatBusy")
    _ask(page, "Second question")
    _ask(page, "Third question")
    _ask(page, "Never mind this one")
    queued = log.locator(".msg.user.queued")
    expect(queued).to_have_count(3)
    expect(queued.first).to_contain_text("Queued")
    expect(page.locator("#chat-input")).to_have_value("")
    # take one out, and one back into the box to edit
    queued.filter(has_text="Never mind").get_by_role("button", name="Remove").click()
    queued.filter(has_text="Third question").get_by_role("button", name="Edit").click()
    expect(queued).to_have_count(1)
    expect(page.locator("#chat-input")).to_have_value("Third question")
    page.locator("#chat-input").fill("Third question, edited")
    page.keyboard.press("Enter")
    expect(queued).to_have_count(2)
    page.evaluate("window.__held = null; window.__release()")
    # each goes out once the one before it is answered
    page.wait_for_function("() => !window.__rb.S.chatBusy && !window.__rb.S.chatQueue.length"
                           " && window.__rb.S.chat.filter(m => m.role === 'assistant' && !m.pending).length === 3", timeout=20000)
    expect(queued).to_have_count(0)
    asked = page.evaluate("window.__rb.S.chat.filter(m => m.role === 'user').map(m => m.content)")
    assert asked == ["First question", "Second question", "Third question, edited"]
    assert page.evaluate("window.__rb.S.chat.every(m => !m.error)")


def test_a_failed_answer_puts_the_queue_back_in_the_box(ctx, server):
    page = _chat(ctx, server)
    page.evaluate("window.__holdChat()")
    page.route("**/api/chat/stream", lambda r: r.fulfill(status=500, body='{"detail": "boom"}', content_type="application/json"))
    page.route("**/api/chat", lambda r: r.fulfill(status=500, body='{"detail": "boom"}', content_type="application/json"))
    _ask(page, "This one fails")
    page.wait_for_function("window.__rb.S.chatBusy")
    _ask(page, "Queued behind it")
    expect(page.locator("#chat-log .msg.user.queued")).to_have_count(1)
    page.evaluate("window.__held = null; window.__release()")
    page.wait_for_function("!window.__rb.S.chatBusy")
    expect(page.locator("#chat-log .msg.user.queued")).to_have_count(0)
    expect(page.locator("#chat-input")).to_have_value("Queued behind it")
    assert page.evaluate("window.__rb.S.chat.filter(m => m.role === 'user').length") == 1

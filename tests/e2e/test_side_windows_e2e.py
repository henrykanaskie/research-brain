"""Side windows close one at a time: the note panel and the chat sit side by side, each with its own X, and closing
or using one never closes the other. Clicking empty map still closes the chat.

Run:  python -m pytest -q tests/e2e/test_side_windows_e2e.py
"""
from __future__ import annotations

import json
import urllib.request

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def _both_open(ctx, server):
    n = _call(server["base"], "POST", "/api/notes", {"type": "note", "title": "Side by side", "body": "text"})
    page = ctx.new_page()
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rb && window.__rb.openNote && window.__rb.fg !== undefined")
    page.evaluate("id => window.__rb.openNote(id)", n["id"])
    expect(page.locator("#panel")).to_be_visible()
    page.locator("#btn-chat").click()
    expect(page.locator("#chat")).to_be_visible()
    return page, n["id"]


def test_closing_the_note_keeps_the_chat(ctx, server):
    page, _ = _both_open(ctx, server)
    page.locator("#panel-body").click(position={"x": 20, "y": 20})  # using the note panel
    expect(page.locator("#chat")).to_be_visible()
    page.locator("#btn-panel-close").click()
    expect(page.locator("#panel")).to_be_hidden()
    expect(page.locator("#chat")).to_be_visible()
    page.locator(".reopen-tab").click()  # the note's reopen tab
    expect(page.locator("#panel")).to_be_visible()
    expect(page.locator("#chat")).to_be_visible()


def test_closing_the_chat_keeps_the_note(ctx, server):
    page, _ = _both_open(ctx, server)
    page.locator("#btn-chat-close").click()
    expect(page.locator("#chat")).to_be_hidden()
    expect(page.locator("#panel")).to_be_visible()

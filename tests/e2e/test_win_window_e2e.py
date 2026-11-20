"""The page in research brain's own Windows window with no title bar (?app=win&titlebar=custom, app/winwindow.py),
in Playwright's Chromium with WebView2's channel (window.chrome.webview) faked: the header is the title bar, the
window buttons sit at its right end and say what was clicked, the window's messages swap and dim them, and the
header's color is sent so the window can match it. Set RB_SHOT_DIR=/some/dir to save screenshots.

Reuses the server/browser fixtures from test_e2e.py. Skipped when Playwright isn't available.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import browser, ctx, server  # noqa: E402,F401  (fixtures are used by name)

FAKE_WEBVIEW = """
window.__sent = [];
window.chrome = window.chrome || {};
window.chrome.webview = {
  postMessage: (m) => window.__sent.push(JSON.parse(JSON.stringify(m))),
  // like WebView2: every 'message' listener hears each message
  __listeners: [],
  addEventListener(type, f) { if (type === 'message') this.__listeners.push(f); },
};
window.__rbMsg = (e) => window.chrome.webview.__listeners.forEach((f) => f(e));
"""


def shot(page, name):
    d = os.environ.get("RB_SHOT_DIR")
    if d:
        Path(d).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(d) / f"{name}.png"), clip={"x": 0, "y": 0, "width": 1400, "height": 120})


def open_win(ctx, server, query="?app=win&titlebar=custom"):
    ctx.add_init_script(FAKE_WEBVIEW)
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.errors = errors
    page.goto(server["base"] + "/" + query)
    page.wait_for_function("window.__rb && window.__rb.fg !== undefined && window.__rbLive")
    return page


def sent(page, key):
    return page.evaluate(f"window.__sent.filter((m) => m.rb && {key!r} in m.rb).map((m) => m.rb[{key!r}])")


def test_header_is_the_title_bar_with_window_buttons(ctx, server):
    page = open_win(ctx, server)
    assert page.evaluate("document.documentElement.classList.contains('win-window')")
    assert page.evaluate("location.search") == ""  # stripped, kept for reloads
    assert page.evaluate("sessionStorage.getItem('rb.winTitlebar')") == "custom"
    buttons = page.locator(".win-caption button")
    expect(buttons).to_have_count(3)
    assert [buttons.nth(i).get_attribute("aria-label") for i in range(3)] == ["Minimize", "Maximize", "Close"]
    # the header is a drag region, its buttons and fields aren't, and nothing in it sits under the window buttons
    assert page.evaluate("getComputedStyle(document.getElementById('topbar')).webkitAppRegion") == "drag"
    assert page.evaluate("getComputedStyle(document.querySelector('#topbar button')).webkitAppRegion") == "no-drag"
    assert page.evaluate("getComputedStyle(document.querySelector('.win-caption')).webkitAppRegion") == "no-drag"
    caption_left = page.evaluate("document.querySelector('.win-caption').getBoundingClientRect().left")
    right = page.evaluate("""Math.max(...[...document.querySelectorAll('#topbar *')].map((e) => e.getBoundingClientRect())
        .filter((r) => r.width && r.height).map((r) => r.right))""")
    assert right <= caption_left, (right, caption_left)
    box = buttons.nth(2).bounding_box()
    assert box["width"] == 46 and box["height"] == 52 and box["x"] + box["width"] == 1400 and box["y"] == 0
    # the header's color, for the window's edge above the page
    chrome = sent(page, "chrome")
    assert chrome and chrome[-1].startswith("#") and len(chrome[-1]) == 7
    shot(page, "win-titlebar-light")

    for i, what in enumerate(["minimize", "maximize", "close"]):
        buttons.nth(i).click()
    assert sent(page, "caption") == ["minimize", "maximize", "close"]

    # the window says it's maximized: the middle button restores; inactive: dimmed
    page.evaluate("window.__rbMsg({data: {rb: {window: 'maximized'}}})")
    expect(buttons.nth(1)).to_have_attribute("aria-label", "Restore")
    assert buttons.nth(1).text_content() == ""
    page.evaluate("window.__rbMsg({data: {rb: {window: 'normal'}}})")
    expect(buttons.nth(1)).to_have_attribute("aria-label", "Maximize")
    page.evaluate("window.__rbMsg({data: {rb: {active: false}}})")
    assert page.evaluate("document.documentElement.classList.contains('win-inactive')")
    page.mouse.move(700, 600)
    assert page.evaluate("getComputedStyle(document.querySelector('.wc-min')).opacity") == "0.45"
    page.evaluate("window.__rbMsg({data: {rb: {active: true}}})")
    assert not page.evaluate("document.documentElement.classList.contains('win-inactive')")

    # dark theme: the color is sent again
    before = sent(page, "chrome")[-1]
    page.evaluate("document.documentElement.setAttribute('data-theme', 'dark')")
    page.wait_for_function(f"window.__sent.filter((m) => m.rb && m.rb.chrome).length > {len(sent(page, 'chrome')) - 1}"
                           f" && window.__sent.filter((m) => m.rb && m.rb.chrome).pop().rb.chrome !== {before!r}")
    page.locator(".wc-close").hover()
    shot(page, "win-titlebar-dark-close-hover")
    assert page.evaluate("getComputedStyle(document.querySelector('.wc-close')).backgroundColor") == "rgb(196, 43, 28)"
    assert not page.errors


def test_mic_uses_windows_voice_typing(ctx, server):
    page = open_win(ctx, server)
    page.evaluate("document.querySelector('#btn-chat').click()")
    mic = page.locator("#btn-chat-mic")
    expect(mic).to_be_visible()
    mic.click()
    assert sent(page, "dictate") == [True]
    expect(mic).to_have_attribute("aria-pressed", "true")
    mic.click()  # stopping never presses Win+H again (a toggle that also stops by itself)
    assert sent(page, "dictate") == [True]
    expect(mic).to_have_attribute("aria-pressed", "false")
    assert not page.errors


def test_native_title_bar_without_custom(ctx, server):
    """The window with Windows' own title bar (old WebView2): no buttons drawn, no drag region."""
    page = open_win(ctx, server, "?app=win")
    assert not page.evaluate("document.documentElement.classList.contains('win-window')")
    expect(page.locator(".win-caption")).to_have_count(0)
    assert page.evaluate("getComputedStyle(document.getElementById('topbar')).webkitAppRegion") != "drag"
    assert not page.errors

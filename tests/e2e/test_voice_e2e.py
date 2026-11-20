"""Voice mode (static/voice.js) in the Mac window: macOS dictation types the question, a pause sends it, the
answer is spoken sentence by sentence as it streams, then it listens again. Dictation and the native channel
are stood in for; speech goes through the page's speech queue."""
from __future__ import annotations

import os
import sys

import pytest


sys.path.insert(0, os.path.dirname(__file__))
from playwright.sync_api import expect  # noqa: E402

from test_e2e import TIMEOUT, api, browser, clear_vault, ctx, server  # noqa: E402,F401  (fixtures are used by name)

STUB = """localStorage.setItem('rb.tour.done','true'); window.__sent = [];
Object.defineProperty(window, 'webkit', { configurable: true, value: { messageHandlers: { rb: {
  postMessage: (m) => window.__sent.push(JSON.parse(m)) } } } });"""


def test_voice_mode_talks_and_listens_again(ctx, server):
    clear_vault(server)
    api(server, "POST", "/api/notes", {"type": "paper", "title": "Cross sections", "abstract": "Electron impact cross sections."})
    page = ctx.new_page()
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.add_init_script(STUB)
    page.goto(server["base"] + "/?app=mac")
    page.wait_for_function("window.__rb && window.__rbVoice && window.__rb.fg !== undefined")
    page.evaluate("() => { window.__said = []; const sp = window.__rbVoice._speech; const say = sp.say.bind(sp); sp.say = (t) => { window.__said.push(t); say(t); }; }")
    page.click("#btn-chat")
    page.click("#btn-chat-voice")
    expect(page.locator(".voice-bar")).to_be_visible()
    page.wait_for_function("() => window.__rbVoice.state === 'listening'")  # it checks the voice's setup first
    assert {"dictate": True} in page.evaluate("() => window.__sent")
    for part in ["What is", "What is a cross section?"]:  # macOS dictation typing
        page.evaluate("(t) => { const ta = document.getElementById('chat-input'); ta.value = t; ta.dispatchEvent(new Event('input')); }", part)
    page.wait_for_function("() => window.__rb.S.chat.some((m) => m.role === 'user' && m.content === 'What is a cross section?')", timeout=TIMEOUT)
    page.wait_for_function("() => !window.__rb.S.chatBusy", timeout=TIMEOUT)
    said = page.evaluate("() => window.__said")
    assert said and all("[[" not in s and "$" not in s for s in said)  # prose only: no citations, math read plainly
    page.wait_for_function("() => window.__rbVoice.state === 'listening'", timeout=60000)  # then it listens again
    page.click(".voice-bar .btn")  # End
    assert page.evaluate("() => window.__rbVoice.state") == "off"
    expect(page.locator(".voice-bar")).to_be_hidden()
    assert page.locator(".msg-read").count() >= 1  # Read aloud on the answer
    assert not errors


def test_speakable_skips_what_cant_be_heard(ctx, server):
    page = ctx.new_page()
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rbVoice")
    out = page.evaluate("""() => window.__rbVoice.speakable(
      "## Result\\nThe rate is $\\\\sigma^2$ [[paper-a]] p. 3, see [the paper](https://x.org).\\n\\n```python\\nprint(1)\\n```\\n\\n| a | b |\\n|---|---|\\n| 1 | 2 |\\n<remember>Likes plots.</remember>")""")
    assert "sigma squared" in out and "[[" not in out and "print" not in out and "| a" not in out
    assert "the paper" in out and "https" not in out and "Likes plots" not in out and "##" not in out


@pytest.mark.skipif(not os.environ.get("RB_VOICE_TEST_DIR"), reason="needs the voice download (RB_VOICE_TEST_DIR: a set-up voice folder)")
def test_hands_free_turns_with_a_microphone(tmp_path):
    """The microphone (a recorded question, played as Chromium's fake microphone), turn-taking, Whisper and the
    natural voice, end to end: it hears the question, answers out loud, then listens and hears the next one.
    Runs in its own process (it needs its own Chromium, with a fake microphone)."""
    import subprocess
    script = os.path.join(os.path.dirname(__file__), "handsfree_check.py")
    r = subprocess.run([sys.executable, script, str(tmp_path)], capture_output=True, text=True, timeout=240,
                       cwd=os.path.dirname(os.path.dirname(os.path.dirname(__file__))))
    assert r.returncode == 0, r.stdout[-2000:] + r.stderr[-2000:]

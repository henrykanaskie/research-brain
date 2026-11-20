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


def test_talking_over_it_and_its_own_echo(ctx, server):
    """While it talks, its own voice coming back through the microphone (here a fifth of what it plays) is
    learned and ignored; you talking clearly over it starts an interruption. Voice is tagged as beta."""
    page = ctx.new_page()
    page.add_init_script("localStorage.setItem('rb.tour.done','true');")
    page.goto(server["base"] + "/")
    page.wait_for_function("window.__rbVoice")
    out = page.evaluate("""() => {
      const mic = window.__rbVoice._mic, o = window.__rbVoice._out;
      o.level = () => 0.1;  // it's talking
      const starts = [];
      mic.onStart = (b) => starts.push(b); mic.onUtter = () => {}; mic.onNothing = () => {};
      mic.mode = 'speaking'; mic.rate = 48000;
      const frame = (amp) => { const b = new Float32Array(2048); for (let i = 0; i < b.length; i++) b[i] = amp * Math.sqrt(2) * Math.sin(i / 7); mic.frame(b); };
      for (let i = 0; i < 80; i++) frame(0.02);      // 3.4 s of only its echo
      const afterEcho = starts.length;
      for (let i = 0; i < 10; i++) frame(0.16);      // you, talking over it
      return { afterEcho, starts, echo: mic.echo };
    }""")
    assert out["afterEcho"] == 0, out
    assert out["starts"] == [True], out
    assert 0.1 < out["echo"] < 0.35, out
    page.click("#btn-chat")
    assert "beta" in (page.get_attribute("#btn-chat-voice", "aria-label") or "").lower()
    assert page.locator(".voice-bar .beta-tag").count() == 1


def test_spoken_questions_ask_for_conversation():
    from app import server as srv
    assert srv._extras(srv.ChatIn(question="q", voice=True)) == srv.VOICE_EXTRAS
    assert srv._extras(srv.ChatIn(question="q")) == srv.CHAT_EXTRAS
    assert srv._extras(srv.GeneralIn(question="q", voice=True)) == srv.VOICE_EXTRAS
    assert "markdown" in srv.VOICE_EXTRAS.lower() and "mermaid" not in srv.VOICE_EXTRAS

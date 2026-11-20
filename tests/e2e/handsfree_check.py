"""Run by test_voice_e2e.py in its own process: hands-free voice mode with Chromium's fake microphone.
Env: RB_VOICE_TEST_DIR (a set-up voice folder), RB_VOICE_TEST_TURN (a WAV: quiet, a spoken question, 20 s of quiet)."""
import json
import os
import sys
import urllib.request
from pathlib import Path

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from playwright.sync_api import sync_playwright  # noqa: E402
from rb_testserver import run_server  # noqa: E402

vault = Path(sys.argv[1]) / "vault"
vault.mkdir(parents=True, exist_ok=True)
with run_server(vault, env={"RB_EMBEDDER": "tfidf", "RB_VOICE_DIR": os.environ["RB_VOICE_TEST_DIR"]}, startup_timeout=120) as srv:
    urllib.request.urlopen(urllib.request.Request(srv.base + "/api/notes", data=json.dumps({"type": "paper", "title": "Iodine cross sections"}).encode(),
                                                  headers={"Content-Type": "application/json"})).read()
    with sync_playwright() as p:
        b = p.chromium.launch(args=["--use-fake-ui-for-media-stream", "--use-fake-device-for-media-stream",
                                    f"--use-file-for-fake-audio-capture={os.environ['RB_VOICE_TEST_TURN']}", "--autoplay-policy=no-user-gesture-required"])
        page = b.new_page()
        page.add_init_script("localStorage.setItem('rb.tour.done','true');")
        page.goto(srv.base)
        page.wait_for_function("() => window.__rbVoice && window.__rb.S")
        page.evaluate("() => { window.__said = []; const sp = window.__rbVoice._speech, say = sp.say.bind(sp); sp.say = (t) => { window.__said.push(t); say(t); }; }")
        page.click("#btn-chat")
        page.click("#btn-chat-voice")
        page.wait_for_function("() => window.__rb.S.chat.filter((m) => m.role === 'user').length >= 2", timeout=120000)
        asked = page.evaluate("() => window.__rb.S.chat.filter((m) => m.role === 'user').map((m) => m.content)")
        assert all("cross" in a.lower() and "iodine" in a.lower() for a in asked), asked
        assert page.evaluate("() => window.__said.length") > 0, "it didn't answer out loud"
        b.close()
print("ok")

"""The natural voice (app/voice.py): its status, the voice choice, and what the endpoints do before it's set up.
The model itself (330 MB) isn't downloaded in tests."""
from __future__ import annotations

import pytest

from test_cards import server  # noqa: F401  (fixture)


def test_voice_endpoints_before_it_is_set_up(server, tmp_path, monkeypatch):  # noqa: F811
    """Before setup the status says missing (default voice af_heart, no hearing), speaking and hearing say to set
    it up first, warming is harmless; a voice can be chosen (a bad name is refused), and hearing takes audio only."""
    srv, c, use = server
    monkeypatch.setenv("RB_VOICE_DIR", str(tmp_path / "voice"))
    v = c.get("/api/voice").json()
    assert v["neural"]["state"] == "missing" and v["neural"]["voice"] == "af_heart" and "af_heart" in v["neural"]["voices"]
    assert v["neural"]["hear"] is False
    r = c.post("/api/voice/tts", json={"text": "Hello"})
    assert r.status_code == 409 and "set up" in r.json()["detail"]
    assert c.post("/api/voice/warm").status_code == 200  # harmless before it's set up

    wav = b"RIFF" + b"\0" * 2000
    r = c.post("/api/voice/stt", content=wav, headers={"Content-Type": "audio/wav"})
    assert r.status_code == 409 and "set up" in r.json()["detail"]
    # only audio: a form or a plain-text post (what another site could send without asking) is refused
    assert c.post("/api/voice/stt", content=b"hello", headers={"Content-Type": "text/plain"}).status_code == 415

    # choosing a voice
    assert c.put("/api/voice/settings", json={"voice": "bm_george"}).json()["voice"] == "bm_george"
    assert c.get("/api/voice").json()["neural"]["voice"] == "bm_george"
    assert c.put("/api/voice/settings", json={"voice": "nonsense"}).status_code == 400


def test_voice_is_ready_only_with_whole_files_and_the_worker_is_standalone(tmp_path, monkeypatch):
    """Files of the wrong size mean not ready (speaking raises), and the worker script imports nothing of the app."""
    from app import voice
    monkeypatch.setenv("RB_VOICE_DIR", str(tmp_path))
    py = voice._python()
    py.parent.mkdir(parents=True)
    py.write_text("")
    for name, (_, size) in voice.FILES.items():
        (tmp_path / name).write_bytes(b"x" * 10)  # wrong size: not ready
    (tmp_path / ".ok").write_text("x")
    assert not voice.ready()
    with pytest.raises(RuntimeError):
        voice.speak("hi")

    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent / "app" / "voice_worker.py").read_text()
    assert "from app" not in src and "research_brain" not in src.split('"""', 2)[-1]

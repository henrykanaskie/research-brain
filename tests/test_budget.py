"""AI usage budgets (app/budget.py): how much each feature may send, the light model for small jobs,
the critique cache and the usage estimate."""
import importlib
import sys

import pytest

sys.path.insert(0, __import__("os").path.dirname(__file__))
from test_agents import FakeBackend, data_dir  # noqa: E402,F401  (data_dir: the fixture)


def test_levels_and_history_trim(data_dir):
    from app import budget
    assert budget.level() == "balanced"
    assert budget.get("paper_full_chars") == 24_000
    budget.save("light")
    assert budget.level() == "light" and budget.get("paper_full_chars") == 12_000
    hist = [{"role": "user" if i % 2 == 0 else "assistant", "content": "word " * 600} for i in range(10)]
    out = budget.trim_history(hist)
    assert len(out) == 4 and all(len(h["content"]) <= 700 + 4 for h in out) and out[-1]["content"].endswith("[…]")
    with pytest.raises(ValueError):
        budget.save("unlimited")
    budget.save("thorough")
    assert budget.get("critique_full_chars") == 180_000


def test_light_model_only_inside_small_jobs(data_dir):
    from app import agents, budget
    assert budget.small_model("claude") == ""
    with budget.small_job():
        assert agents.model_for("claude") == "haiku"
        assert agents.model_for("codex") == agents.model_for("codex")  # no known light model: unchanged
        assert budget.small_model("codex") == ""
    budget.save(light_model=False)
    with budget.small_job():
        assert budget.small_model("claude") == ""


def test_usage_summary_from_log():
    import datetime as dt
    from app import budget
    now = dt.datetime.now().astimezone()
    log = [{"at": now.isoformat(), "purpose": "chat", "chars": 4000},
           {"at": now.isoformat(), "purpose": "critique", "chars": 40000},
           {"at": (now - dt.timedelta(days=30)).isoformat(), "purpose": "chat", "chars": 999999}]
    u = budget.usage(log, days=7)
    assert u["tokens"] == 11000 and u["requests"] == 2 and len(u["by_day"]) == 7
    assert u["by_purpose"][0] == {"purpose": "critique", "tokens": 10000, "requests": 1}


@pytest.fixture()
def server(tmp_path, monkeypatch, data_dir):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    with TestClient(srv.app) as c:
        yield srv, c


PARA = ("Electron cross-field transport in the near plume exceeds the classical value by an order of magnitude, "
        "and the breathing mode modulates the ionization front at 10 to 30 kHz. ")


def test_paper_chat_and_critique_respect_the_level(server):
    srv, c = server
    from app import agents
    fb = FakeBackend("fake", script=["ok"] * 10)
    agents.register(fb)
    try:
        c.put("/api/agents/active", json={"id": "fake"})
        p = c.post("/api/notes", json={"type": "paper", "title": "Journal length paper",
                                       "summary": "Transport is anomalous near the exit."}).json()
        text = "\n".join(f"[[page {i}]]\n" + PARA * 20 for i in range(1, 13))  # ~45k characters
        srv.store.text_path(p["id"]).write_text(text, encoding="utf-8")
        q = {"question": "What drives transport?", "history": [], "paper": p["id"]}
        # balanced: too long to send whole, so passages
        assert c.post("/api/chat", json=q).status_code == 200
        assert "FULL TEXT of" not in fb.prompts[-1][0] and "PASSAGES FROM THE FOCUS PAPER" in fb.prompts[-1][0]
        # thorough: sent whole (the old behaviour)
        c.put("/api/ai-usage", json={"level": "thorough"})
        c.post("/api/chat", json=q)
        assert "FULL TEXT of" in fb.prompts[-1][0]
        # critique of a long paper at light: opening, passages near the summary, end
        c.put("/api/ai-usage", json={"level": "light"})
        r = c.post(f"/api/notes/{p['id']}/critique")
        assert r.status_code == 200 and "cached" not in r.json()
        sent = fb.prompts[-1][0]
        assert "THE PAPER IS LONG" in sent and len(sent) < 30_000
        n = len(fb.prompts)
        # the same summary of the same paper: the last critique, nothing sent
        r = c.post(f"/api/notes/{p['id']}/critique")
        assert r.json()["cached"] is True and len(fb.prompts) == n
        r = c.post(f"/api/notes/{p['id']}/critique/stream")
        assert '"cached": true' in r.text and len(fb.prompts) == n
        # ...unless asked again, or the summary changed
        c.post(f"/api/notes/{p['id']}/critique?refresh=true")
        assert len(fb.prompts) == n + 1
        c.put(f"/api/notes/{p['id']}", json={"summary": "Transport is anomalous; spokes matter."})
        c.post(f"/api/notes/{p['id']}/critique")
        assert len(fb.prompts) == n + 2
        u = c.get("/api/ai-usage").json()
        assert u["level"] == "light" and u["usage"]["requests"] >= 4 and u["usage"]["tokens"] > 0
        assert c.put("/api/ai-usage", json={"level": "max"}).status_code == 400
    finally:
        agents.unregister("fake")

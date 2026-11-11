"""Review (active recall / SM-2) and critique history.

Run:  .venv/bin/python -m pytest -q tests
"""
import datetime as dt
import importlib
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    day = {"d": dt.date(2026, 11, 9)}
    monkeypatch.setattr(server.review_mod, "_today", lambda: day["d"])
    return TestClient(server.app), server, day


def note(c, **kw):
    r = c.post("/api/notes", json=kw)
    assert r.status_code == 201, r.text
    return r.json()


# ---------------------------------------------------------------- scheduling math
def test_sm2_schedule():
    from app import review
    s = review.schedule(None, 4)
    assert s == {"reps": 1, "ease": 2.5, "interval": 1}
    s2 = review.schedule(s, 4)
    assert s2["reps"] == 2 and s2["interval"] == 6
    s3 = review.schedule(s2, 4)
    assert s3["interval"] == 15  # 6 * 2.5
    # Easy raises ease and gets a bonus; Hard grows slowly and lowers ease
    easy = review.schedule(s2, 5)
    hard = review.schedule(s2, 3)
    assert easy["ease"] == 2.6 and easy["interval"] == round(6 * 2.6 * 1.3)
    assert hard["ease"] == pytest.approx(2.36) and hard["interval"] == round(6 * 1.2)
    # lapse resets reps and interval, drops ease, never below 1.3
    lapse = review.schedule(s3, 1)
    assert lapse["reps"] == 0 and lapse["interval"] == 1 and lapse["ease"] < 2.5
    st = {"reps": 5, "ease": 1.31, "interval": 40}
    assert review.schedule(st, 0)["ease"] == 1.3
    # buttons always give distinct, increasing intervals once learned
    p = review.preview({"reps": 3, "ease": 2.5, "interval": 10})
    assert p["again"] < p["hard"] < p["good"] < p["easy"]
    assert review.preview(None) == {"again": 1, "hard": 1, "good": 1, "easy": 4}


# ---------------------------------------------------------------- items + queue rules
def test_items_and_queue_rules(env):
    c, server, day = env
    p = note(c, type="paper", title="Hall thruster oscillations", authors=["A. B"], year=2016,
             summary="Breathing mode is an ionization predator-prey cycle.")
    note(c, type="paper", title="No summary yet")
    note(c, type="fragment", parent=p["id"], quote="$\\omega \\approx \\sqrt{v_i v_n}/L$", locator="Eq. 3",
         body="Frequency set by ion and neutral speeds.")
    note(c, type="fragment", parent=p["id"], quote="unannotated passage", body="")
    q = c.get("/api/review/queue").json()
    kinds = sorted(i["kind"] for i in q["items"])
    assert kinds == ["fragment", "summary"] and q["total"] == 2
    summ = next(i for i in q["items"] if i["kind"] == "summary")
    assert summ["prompt"]["title"] == "Hall thruster oscillations" and summ["prompt"]["year"] == 2016
    assert summ["answer"].startswith("Breathing mode") and summ["is_new"]
    frag = next(i for i in q["items"] if i["kind"] == "fragment")
    assert frag["prompt"]["paper_title"] == "Hall thruster oscillations" and frag["prompt"]["locator"] == "Eq. 3"
    assert frag["answer"] == "Frequency set by ion and neutral speeds."
    assert summ["intervals"]["good"] == 1


def test_new_per_day_cap_and_order(env):
    c, server, day = env
    ids = [note(c, type="note", title=f"Idea {i}", summary=f"My idea number {i}.")["id"] for i in range(8)]
    q = c.get("/api/review/queue?limit=20").json()
    assert len(q["items"]) == 5 and q["new"] == 5
    assert [i["id"] for i in q["items"]] == ids[:5]  # oldest created first
    for it in q["items"][:3]:
        assert c.post(f"/api/review/{it['id']}/grade", json={"grade": 4}).status_code == 200
    q = c.get("/api/review/queue?limit=20").json()
    assert len(q["items"]) == 2 and q["due"] == 0  # only 2 new left today; graded ones are due tomorrow
    assert c.get("/api/review/stats").json()["reviewed_today"] == 3
    # limit applies
    assert len(c.get("/api/review/queue?limit=1").json()["items"]) == 1
    # next day: the 3 graded are due (first), and 5 new again
    day["d"] += dt.timedelta(days=1)
    q = c.get("/api/review/queue?limit=20").json()
    assert q["due"] == 3 and q["new"] == 5
    assert [i["id"] for i in q["items"][:3]] == ids[:3] and not q["items"][0]["is_new"]


def test_due_order_oldest_first(env):
    c, server, day = env
    a = note(c, type="note", title="A", summary="aaa")["id"]
    b = note(c, type="note", title="B", summary="bbb")["id"]
    c.post(f"/api/review/{a}/grade", json={"grade": "easy"})   # due in 4 days
    c.post(f"/api/review/{b}/grade", json={"grade": "good"})   # due in 1 day
    day["d"] += dt.timedelta(days=10)
    q = c.get("/api/review/queue").json()
    assert [i["id"] for i in q["items"]] == [b, a]  # b was due on day 1, a on day 4: oldest due first
    assert q["items"][0]["due"] == "2026-11-10" and q["items"][1]["due"] == "2026-11-13"


def test_grading_and_persistence(env, tmp_path):
    c, server, day = env
    a = note(c, type="note", title="A", summary="aaa")["id"]
    r = c.post(f"/api/review/{a}/grade", json={"grade": "good", "recalled_text": "something like aaa"})
    assert r.status_code == 200 and r.json()["due"] == "2026-11-10" and r.json()["interval"] == 1
    day["d"] = dt.date(2026, 11, 10)
    r = c.post(f"/api/review/{a}/grade", json={"grade": 4}).json()
    assert r["interval"] == 6 and r["due"] == "2026-11-16"
    data = json.loads((tmp_path / "vault/state/review.json").read_text())
    st = data["items"][a]
    assert st["kind"] == "summary" and st["reps"] == 2 and len(st["history"]) == 2
    assert st["history"][0]["grade"] == 4 and st["history"][0]["recalled"] == "something like aaa"
    # survives a fresh process
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server2 = importlib.import_module("app.server")
    server2.review_mod._today = lambda: dt.date(2026, 11, 16)
    from fastapi.testclient import TestClient
    c2 = TestClient(server2.app)
    q = c2.get("/api/review/queue").json()
    assert q["due"] == 1 and q["items"][0]["id"] == a and q["items"][0]["intervals"]["good"] == 15
    # bad input
    assert c2.post(f"/api/review/{a}/grade", json={"grade": 9}).status_code == 400
    assert c2.post(f"/api/review/{a}/grade", json={"grade": "meh"}).status_code == 400
    assert c2.post("/api/review/nope/grade", json={"grade": 4}).status_code == 404


def test_items_disappear_with_source(env, tmp_path):
    c, server, day = env
    p = note(c, type="paper", title="P", summary="mine")
    f = note(c, type="fragment", parent=p["id"], quote="q", body="my note")
    n = note(c, type="note", title="N", summary="idea")
    for i in (p["id"], f["id"], n["id"]):
        c.post(f"/api/review/{i}/grade", json={"grade": 4})
    assert c.get("/api/review/stats").json()["total"] == 3
    c.delete(f"/api/notes/{p['id']}")  # also trashes its fragment
    c.put(f"/api/notes/{n['id']}", json={"summary": "  "})  # emptied: no longer an item
    day["d"] += dt.timedelta(days=30)
    q = c.get("/api/review/queue").json()
    assert q["items"] == [] and q["total"] == 0
    data = json.loads((tmp_path / "vault/state/review.json").read_text())
    assert set(data["items"]) == {n["id"]}  # deleted sources pruned; emptied one keeps its history
    assert c.post(f"/api/review/{p['id']}/grade", json={"grade": 4}).status_code == 404


def test_stats_streak(env):
    c, server, day = env
    a = note(c, type="note", title="A", summary="aaa")["id"]
    s = c.get("/api/review/stats").json()
    assert s == {"due_today": 1, "due": 0, "new": 1, "reviewed_today": 0, "streak": 0, "total": 1}
    for d in (7, 8):
        day["d"] = dt.date(2026, 11, d)
        c.post(f"/api/review/{a}/grade", json={"grade": 1})
    day["d"] = dt.date(2026, 11, 9)
    assert c.get("/api/review/stats").json()["streak"] == 2  # today not done yet: streak still counts
    c.post(f"/api/review/{a}/grade", json={"grade": 1})
    s = c.get("/api/review/stats").json()
    assert s["streak"] == 3 and s["reviewed_today"] == 1
    day["d"] = dt.date(2026, 11, 11)
    assert c.get("/api/review/stats").json()["streak"] == 0


def test_check_recall(env):
    c, server, day = env
    a = note(c, type="note", private=False, title="A", summary="aaa")["id"]
    assert c.post(f"/api/review/{a}/check", json={"recalled_text": ""}).status_code == 400
    r = c.post(f"/api/review/{a}/check", json={"recalled_text": "aa"})
    assert r.status_code == 200
    assert 1 <= len(r.json()["text"].splitlines()) <= 3


# ---------------------------------------------------------------- critique history
def test_critique_history(env):
    c, server, day = env
    p = note(c, type="paper", title="P", summary="First take.")
    r1 = c.post(f"/api/notes/{p['id']}/critique").json()
    assert set(r1) >= {"text", "at"}
    c.put(f"/api/notes/{p['id']}", json={"summary": "Second, better take."})
    r2 = c.post(f"/api/notes/{p['id']}/critique").json()
    n = c.get(f"/api/notes/{p['id']}").json()
    assert len(n["critiques"]) == 2
    assert [x["summary_snapshot"] for x in n["critiques"]] == ["First take.", "Second, better take."]
    assert n["critique"]["at"] == r2["at"] and n["critique"]["summary_snapshot"] == "Second, better take."
    assert r2["summary_snapshot"] == "Second, better take."
    # critiques can't be set through the API
    c.put(f"/api/notes/{p['id']}", json={"critiques": []})
    assert len(c.get(f"/api/notes/{p['id']}").json()["critiques"]) == 2


def test_critique_migration(env, tmp_path):
    c, server, day = env
    p = note(c, type="paper", title="Old", summary="s")
    path = tmp_path / "vault/notes" / f"{p['id']}.md"
    raw = path.read_text()
    raw = raw.replace("---\n", "---\ncritique:\n  text: old critique\n  at: '2025-01-01T10:00:00+00:00'\n"
                                "  summary_snapshot: older summary\n", 1)
    path.write_text(raw)
    n = c.get(f"/api/notes/{p['id']}").json()
    assert n["critiques"] == [{"text": "old critique", "at": "2025-01-01T10:00:00+00:00", "summary_snapshot": "older summary"}]
    c.post(f"/api/notes/{p['id']}/critique")
    n = c.get(f"/api/notes/{p['id']}").json()
    assert len(n["critiques"]) == 2 and n["critiques"][0]["text"] == "old critique"
    assert "critiques:" in path.read_text()

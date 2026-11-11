"""Live updates (SSE from the vault watcher) and streaming Claude endpoints.

Runs a real uvicorn server (a free port, tests/rb_testserver.py) so Server-Sent Events stream for real; Claude is faked.
"""
import json
import sys
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))
from rb_testserver import run_server  # noqa: E402


@pytest.fixture(scope="module")
def srv(tmp_path_factory):
    vault = tmp_path_factory.mktemp("live") / "vault"
    with run_server(vault, env={"RB_FAKE_STREAM_DELAY": "0.05", "PYTHONPATH": str(ROOT)}) as srv_:
        yield srv_.base, vault


def _events(resp, until: float):
    """Parse SSE frames from a streaming response until `until` (monotonic)."""
    buf = ""
    for chunk in resp.iter_content(chunk_size=None, decode_unicode=True):
        buf += chunk
        while "\n\n" in buf:
            frame, buf = buf.split("\n\n", 1)
            ev, data = "message", []
            for line in frame.split("\n"):
                if line.startswith("event:"):
                    ev = line[6:].strip()
                elif line.startswith("data:"):
                    data.append(line[5:].lstrip())
                elif line.startswith(":"):
                    ev = "comment"
            if ev == "message" and not data:
                continue  # e.g. the initial "retry:" frame
            yield ev, ("\n".join(data) if data else None)
        if time.monotonic() > until:
            return


def test_external_edit_pushes_event_but_own_save_does_not(srv):
    base, vault = srv
    note = requests.post(base + "/api/notes", json={"type": "note", "title": "Watched note"}).json()
    with requests.get(base + "/api/events", stream=True, timeout=10,
                      headers={"Origin": base}) as r:  # the page's own origin must pass the guard
        assert r.status_code == 200 and r.headers["content-type"].startswith("text/event-stream")
        it = _events(r, time.monotonic() + 8)
        assert next(it)[0] == "hello"
        # the app's own save: no event should follow for it
        requests.put(f"{base}/api/notes/{note['id']}", json={"body": "saved from the app"})
        time.sleep(0.8)
        # an outside edit (as Obsidian would make)
        t0 = time.monotonic()
        p = vault / "notes" / f"{note['id']}.md"
        p.write_text(p.read_text(encoding="utf-8").replace("saved from the app", "edited in Obsidian"), encoding="utf-8")
        got = None
        for ev, data in it:
            if ev == "notes-changed":
                got = json.loads(data)
                break
        assert got is not None, "no notes-changed event"
        assert got["ids"] == [note["id"]], got  # only the outside edit, not the app's own save
        assert time.monotonic() - t0 < 2.0
    assert requests.get(f"{base}/api/notes/{note['id']}").json()["body"] == "edited in Obsidian"


def test_new_and_deleted_files_are_reported(srv):
    base, vault = srv
    with requests.get(base + "/api/events", stream=True, timeout=10) as r:
        it = _events(r, time.monotonic() + 8)
        next(it)
        (vault / "notes" / "dropped-in.md").write_text("---\ntitle: Dropped in\n---\nhi\n", encoding="utf-8")
        ids = set()
        for ev, data in it:
            if ev == "notes-changed":
                ids |= set(json.loads(data)["ids"])
                if "dropped-in" in ids:
                    break
        assert "dropped-in" in ids
        (vault / "notes" / "dropped-in.md").unlink()
        for ev, data in it:
            if ev == "notes-changed" and "dropped-in" in json.loads(data)["ids"]:
                break
        else:
            pytest.fail("deletion not reported")
    assert all(n["id"] != "dropped-in" for n in requests.get(base + "/api/graph").json()["nodes"])


def test_foreign_origin_rejected(srv):
    base, _ = srv
    r = requests.get(base + "/api/events", headers={"Origin": "https://evil.example"}, timeout=5)
    assert r.status_code == 403


def _stream(base, path, body=None):
    with requests.post(base + path, json=body, stream=True, timeout=30, headers={"Origin": base}) as r:
        assert r.status_code == 200, r.text
        return list(_events(r, time.monotonic() + 20))


def test_critique_stream_matches_stored_critique(srv):
    base, _ = srv
    n = requests.post(base + "/api/notes", json={"type": "paper", "title": "Stream me",
                                                  "summary": "A summary to critique."}).json()
    evs = _stream(base, f"/api/notes/{n['id']}/critique/stream")
    deltas = [json.loads(d)["text"] for e, d in evs if e == "delta"]
    done = [json.loads(d) for e, d in evs if e == "done"]
    assert len(deltas) >= 3 and len(done) == 1  # fake Claude streams in chunks
    assert set(done[0]) == {"text", "at", "summary_snapshot"}  # same shape as POST .../critique
    assert done[0]["text"] == "".join(deltas).strip()
    stored = requests.get(f"{base}/api/notes/{n['id']}").json()
    assert stored["critique"]["text"] == done[0]["text"]
    assert stored["critiques"][-1]["text"] == done[0]["text"]  # history goes through the same helper
    again = requests.post(f"{base}/api/notes/{n['id']}/critique").json()  # same summary: the last critique, not resent
    assert again["cached"] is True and again["text"] == done[0]["text"]
    plain = requests.post(f"{base}/api/notes/{n['id']}/critique?refresh=true").json()
    assert set(plain) == set(done[0])
    # errors before streaming are plain HTTP errors, like the non-streaming endpoint
    empty = requests.post(base + "/api/notes", json={"type": "paper", "title": "No summary"}).json()
    r = requests.post(f"{base}/api/notes/{empty['id']}/critique/stream")
    assert r.status_code == 400 and "summary" in r.json()["detail"]


def test_chat_stream(srv):
    base, _ = srv
    evs = _stream(base, "/api/chat/stream", {"question": "What is in my library?", "history": []})
    deltas = "".join(json.loads(d)["text"] for e, d in evs if e == "delta")
    done = next(json.loads(d) for e, d in evs if e == "done")
    plain = requests.post(base + "/api/chat", json={"question": "What is in my library?", "history": []}).json()
    assert set(done) == set(plain)  # answer, sources, passages (whatever the plain endpoint returns)
    assert done["answer"] == deltas.strip() and "fake Claude" in done["answer"]
    assert requests.post(base + "/api/chat/stream", json={"question": " "}).status_code == 400


def test_stream_line_parser():
    from app.claude_cli import parse_stream_line
    st = {}
    lines = [
        '{"type":"system","subtype":"init"}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"Hel"}}}',
        '{"type":"stream_event","event":{"type":"content_block_delta","delta":{"type":"text_delta","text":"lo $x"}}}',
        '{"type":"assistant","message":{"model":"m","content":[{"type":"text","text":"Hello $x"}]}}',
        'not json',
        '{"type":"result","subtype":"success","is_error":false,"result":"Hello $x$."}',
    ]
    out = "".join(parse_stream_line(l, st) for l in lines)
    assert out == "Hello $x$." and not st.get("error")  # result tops up what the deltas missed
    st = {}  # CLI without partial messages: assistant messages carry the text
    assert parse_stream_line('{"type":"assistant","message":{"content":[{"type":"text","text":"Whole"}]}}', st) == "Whole"
    st = {}
    parse_stream_line('{"type":"assistant","message":{"model":"<synthetic>","content":[{"type":"text","text":"Not logged in · Please run /login"}]}}', st)
    parse_stream_line('{"type":"result","subtype":"success","is_error":true,"result":"Not logged in · Please run /login"}', st)
    assert "login" in st["error"]


def test_server_stops_promptly_with_open_event_streams(tmp_path):
    """SIGTERM/SIGINT end the /api/events streams, so the server exits instead of waiting on them forever."""
    import signal
    import threading
    for sig in (signal.SIGTERM, signal.SIGINT):
        with run_server(tmp_path / f"vault-{sig}", graceful_timeout=None) as s:  # as run.sh starts it
            responses = [requests.get(s.base + "/api/events", stream=True, timeout=10, headers={"Origin": s.base})
                         for _ in range(2)]
            for r in responses:
                assert r.status_code == 200
                threading.Thread(target=lambda r=r: [None for _ in r.iter_content(64)], daemon=True).start()
            t0 = time.monotonic()
            s.proc.send_signal(sig)
            s.proc.wait(timeout=8)  # before: never exited on its own (uvicorn waits for open responses)
            assert time.monotonic() - t0 < 5
            for r in responses:
                r.close()


def test_event_stream_ends_when_bus_closes():
    import asyncio
    from app import live

    class Req:
        async def is_disconnected(self):
            return False

    async def go():
        bus = live.EventBus()
        gen = live.event_stream(bus, Req(), keepalive=30)
        assert "hello" in await gen.__anext__()
        asyncio.get_running_loop().call_later(0.05, bus.close)
        rest = [x async for x in gen]
        return rest, bus.count
    rest, count = asyncio.run(asyncio.wait_for(go(), 5))
    assert rest == [] and count == 0

"""Pluggable AI backends (app/agents): registry and saved choice, the app-driven Research loop, the local
model servers (Ollama, OpenAI-compatible) against fake HTTP servers, and the server routing every AI call
through the active backend. No network, no real CLIs.
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


@pytest.fixture()
def data_dir(tmp_path, monkeypatch):
    """A private settings file per test, and a clean registry/detection cache."""
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.delenv("RB_AGENT", raising=False)
    from app import agents
    agents.forget_detection()
    yield tmp_path / "data"
    agents.forget_detection()


from app.agents.base import Backend as _Backend, Detection as _Detection  # noqa: E402


class FakeBackend(_Backend):
    """A scripted backend: replies are taken from `script` in order (callables get the prompt)."""

    def __init__(self, id="fake", available=True, script=(), delay=0.0):
        self.id, self.name, self.kind, self.home, self.cost_note = id, f"Fake {id}", "cli", "https://example.invalid", "Free."
        self._available = available
        self.script = list(script)
        self.prompts: list[tuple[str, str]] = []
        self.delay = delay

    def detect(self):
        return _Detection(self._available, version="1.0", path="/fake", signed_in=True)

    def ask_stream(self, prompt, *, system="", timeout=180):
        import time
        self.prompts.append((prompt, system))
        if self.delay:
            time.sleep(self.delay)
        reply = self.script.pop(0) if self.script else '{"action": "answer", "text": "default answer"}'
        if callable(reply):
            reply = reply(prompt)
        for i in range(0, len(reply), 7):
            yield reply[i:i + 7]


@pytest.fixture()
def registry(data_dir):
    from app import agents
    added: list[str] = []

    def add(b):
        agents.register(b)
        added.append(b.id)
        return b
    yield add
    for i in added:
        agents.unregister(i)


# ---------------------------------------------------------------- registry and selection
def test_builtin_adapters_registered(data_dir):
    from app import agents
    ids = [b.id for b in agents.all_backends()]
    for need in ("claude", "ollama", "openai-local"):
        assert need in ids
    assert ids.index("claude") < ids.index("ollama") < ids.index("openai-local")
    for b in agents.all_backends():
        assert b.name and b.kind in ("cli", "local-server") and b.cost_note


def test_active_defaults_to_first_available_and_choice_persists(registry, data_dir, monkeypatch):
    from app import agents, paths
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    claude = agents.get("claude")
    monkeypatch.setattr(type(claude), "detect", lambda self: agents.Detection(False, note="not here"))
    for b in agents.all_backends():  # nothing real is available in this test
        if b.id not in ("claude",) and b.kind == "local-server":
            monkeypatch.setattr(type(b), "detect", lambda self: agents.Detection(False))
    for b in agents.all_backends():
        if b.kind == "cli" and b.id != "claude":
            monkeypatch.setattr(type(b), "detect", lambda self: agents.Detection(False))
    agents.forget_detection()
    assert agents.active().id == "claude"  # nothing available: Claude, whose errors explain installing it
    late = registry(FakeBackend("zz-late", available=True))
    agents.forget_detection()
    assert agents.active() is late  # first available, in ORDER then by id
    other = registry(FakeBackend("other", available=False))
    agents.set_active("other", model="m1")
    assert paths.load_settings()["agent"] == "other"
    assert agents.active() is other  # an explicit choice wins even if it isn't ready
    assert agents.model_for("other") == "m1"
    agents.set_active("other", model="")
    assert agents.model_for("other") == ""
    with pytest.raises(agents.AgentError):
        agents.set_active("nope")
    monkeypatch.setenv("RB_AGENT", "zz-late")
    assert agents.active() is late


def test_broken_adapter_module_is_skipped(tmp_path, monkeypatch, caplog):
    from app import agents
    bad = Path(agents.__file__).with_name("zz_broken_for_test.py")
    bad.write_text("raise RuntimeError('boom')\n")
    try:
        monkeypatch.setattr(agents, "_loaded", False)
        agents._failed.discard("zz_broken_for_test")
        with caplog.at_level("WARNING"):
            assert agents.get("claude") is not None
        assert "zz_broken_for_test" in caplog.text
    finally:
        bad.unlink()
        sys.modules.pop("app.agents.zz_broken_for_test", None)


def test_base_helpers_kill_and_timeout(tmp_path):
    from app.agents import base
    wd = base.private_workdir("t_")
    assert wd.is_dir() and not any(wd.iterdir()) and (os.name == "nt" or (wd.stat().st_mode & 0o077) == 0)
    code, out, err = base.run_process([sys.executable, "-c", "import sys; print(sys.stdin.read().upper())"], stdin="hi")
    assert code == 0 and out.strip() == "HI"
    with pytest.raises(base.AgentError) as e:
        base.run_process([sys.executable, "-c", "import time; time.sleep(30)"], timeout=1)
    assert "longer than" in str(e.value)
    env = base.clean_env(force={"X_RB": "1"}, keep_api_keys=False)
    assert "NODE_OPTIONS" not in env and env["X_RB"] == "1" and "OPENAI_API_KEY" not in env
    assert base.combine("ask", "sys").startswith("sys")
    import shutil
    shutil.rmtree(wd, ignore_errors=True)


# ---------------------------------------------------------------- the research loop
def _lib(tmp_path, notes, pages=None):
    from app.memory import Memory
    by_id = {n["id"]: {"type": "paper", "authors": [], "tags": [], "cites": [], "links": [], "parent": "",
                       "abstract": "", "summary": "", "body": "", **n} for n in notes}
    pages = pages or {}
    mem = Memory(tmp_path / "memory", note_exists=lambda i: i in by_id, page_count=lambda i: 9 if i in pages else None)

    def passages(q, k, per_note):
        return [{"id": i, "page": 2, "src": "text", "text": f"{i} says {q}", "score": 1.0} for i in by_id][:k]

    def fulltext(i):
        return "\n".join(f"[[page {p}]]\n{t}" for p, t in sorted((pages.get(i) or {}).items()))
    return SimpleNamespace(by_id=lambda: by_id, passages=passages, fts=lambda q, limit: [], fulltext=fulltext,
                           neighbours=lambda i: [], memory=mem, topics=lambda: None, pdf_of=lambda i: "")


def _run(backend, tmp_path, **kw):
    from app import agent as agent_mod
    from app.agents import research
    lib = _lib(tmp_path, [{"id": "kr-cr", "title": "Krypton CR model"}],
               {"kr-cr": {1: "intro", 2: "The 811 nm line.", 3: "T_e = 4.2 eV from the ratio."}})
    tools = agent_mod.Tools(lib)
    run = agent_mod.open_run("What T_e?")
    try:
        evs = [e for e in research.run(backend, "PRE-SEARCHED PASSAGES: none\n\nQUESTION: What T_e?", tools, run, **kw)
               if e["type"] != "tick"]
    finally:
        agent_mod.close_run(run)
    return evs, run, lib


def test_research_search_read_answer(tmp_path):
    fb = FakeBackend(script=[
        'Sure!\n```json\n{"action": "search", "queries": ["krypton temperature", "line ratio"]}\n```',
        '{"action": "read", "id": "kr-cr", "pages": [2, 3]} I will read these.',
        '{"action": "answer", "text": "T_e was 4.2 eV [[kr-cr]] p. 3.\\n\\n_Searched twice._"}'])
    evs, run, _ = _run(fb, tmp_path)
    kinds = [e["type"] for e in evs]
    assert kinds[0] == "init" and kinds[-1] == "end"
    uses = [e for e in evs if e["type"] == "tool_use"]
    assert [u["name"] for u in uses] == ["search", "read_pages"]
    assert uses[1]["input"] == {"id": "kr-cr", "start": 2, "end": 3}
    results = [e for e in evs if e["type"] == "tool_result"]
    assert not any(r["is_error"] for r in results) and "4.2 eV" in results[1]["text"]
    end = evs[-1]
    assert end["reason"] == "done" and end["tool_calls"] == 2 and "[[kr-cr]] p. 3" in end["text"]
    assert "".join(e["text"] for e in evs if e["type"] == "delta") == end["text"]
    # results are fed back to the model; the system prompt forbids tools and asks for JSON
    last_prompt, system = fb.prompts[-1]
    assert "T_e = 4.2 eV" in last_prompt and "YOUR LOOKUPS SO FAR" in last_prompt
    assert "no tools" in system and "JSON" in system
    assert any(p["page"] == 3 for p in run.passages)


def test_research_malformed_retry_then_answer(tmp_path):
    fb = FakeBackend(script=["I think I should search first.", '{"action":"answer","text":"Fine [[kr-cr]]."}'])
    evs, _, _ = _run(fb, tmp_path)
    assert evs[-1]["reason"] == "done" and evs[-1]["text"] == "Fine [[kr-cr]]."
    assert len(fb.prompts) == 2 and "wasn't one valid JSON action" in fb.prompts[1][0]


def test_research_malformed_twice_ends(tmp_path):
    fb = FakeBackend(script=["{oops", "{still not json"])
    evs, _, _ = _run(fb, tmp_path)
    assert evs[-1]["reason"] == "malformed" and evs[-1]["text"] == "" and len(fb.prompts) == 2
    fb = FakeBackend(script=["nope", "The library says the temperature was about 4.2 eV, see [[kr-cr]] p. 3."])
    evs, _, _ = _run(fb, tmp_path)
    assert evs[-1]["reason"] == "done" and "4.2 eV" in evs[-1]["text"]  # clearly prose: accepted as the answer


def test_research_step_cap(tmp_path):
    fb = FakeBackend(script=['{"action":"search","query":"a"}'] * 10)
    evs, _, _ = _run(fb, tmp_path, max_steps=3)
    assert evs[-1]["reason"] == "max_turns" and evs[-1]["tool_calls"] == 3 and len(fb.prompts) == 3
    assert "last turn" in fb.prompts[-1][0]


def test_research_time_cap(tmp_path):
    t = [0.0]

    def clock():
        return t[0]

    def slow(prompt):
        t[0] += 40  # each model call "takes" 40 s of a 100 s budget
        return '{"action":"search","query":"x"}'
    fb = FakeBackend(script=[slow] * 10)
    evs, _, _ = _run(fb, tmp_path, budget=100, clock=clock, max_steps=10)
    assert evs[-1]["reason"] == "budget" and evs[-1]["tool_calls"] == 3


def test_research_rejects_bad_actions_and_validates_remember(tmp_path):
    fb = FakeBackend(script=[
        '{"action":"shell","cmd":"rm -rf /"}',
        '{"action":"read","id":"kr-cr","pages":[1,2,3,4]}',
        '{"action":"search","queries":["a","b","c","d","e"]}',
        '{"action":"remember","text":"T_e from the 811/826 ratio is 4.2 eV.","sources":["no-such-note"]}',
        '{"action":"remember","text":"T_e from the 811/826 ratio is 4.2 eV.","sources":["kr-cr"]}',
        '{"action":"answer","text":"done [[kr-cr]]"}'])
    evs, _, lib = _run(fb, tmp_path, max_steps=8)
    res = [e for e in evs if e["type"] == "tool_result"]
    assert res[0]["is_error"] and "unknown action" in res[0]["text"]
    assert res[1]["is_error"] and "pages" in res[1]["text"]
    assert res[2]["is_error"] and "1-4" in res[2]["text"]
    assert res[3]["text"].startswith("Not saved")
    assert res[4]["text"].startswith("Saved finding")
    entries, _ = lib.memory.findings()
    assert len(entries) == 1 and entries[0]["sources"][0]["id"] == "kr-cr"
    assert evs[-1]["reason"] == "done"
    # at most MAX_REMEMBER findings per question
    from app.agents import research
    fb = FakeBackend(script=[f'{{"action":"remember","text":"Finding number {i} about krypton lines.","sources":["kr-cr"]}}'
                             for i in range(4)] + ['{"action":"answer","text":"ok"}'])
    evs, _, lib = _run(fb, tmp_path / "b", max_steps=8)
    saved = [e for e in evs if e["type"] == "tool_result" and e["text"].startswith("Saved")]
    assert len(saved) == research.MAX_REMEMBER


def test_extract_action_variants():
    from app.agents.research import extract_action
    assert extract_action('```json\n{"action":"search","query":"x"}\n```')["query"] == "x"
    assert extract_action('Here: {"note": 1} then {"action":"answer","text":"a {b} c"} bye')["text"] == "a {b} c"
    assert extract_action('{"action": "answer", "text": "line one\nline two"}')["text"] == "line one\nline two"
    assert extract_action("no json") is None and extract_action("") is None


def test_research_answers_keep_latex_written_with_single_backslashes():
    """Agents often write LaTeX in a JSON answer with one backslash: "\\alpha" is invalid JSON and "\\frac",
    "\\theta", "\\nabla" would silently decode as a form feed, a tab and a newline."""
    from app.agents.research import extract_action
    raw = (r'{"action":"answer","text":"$\alpha + \frac{a}{b} = \theta \nabla$, $\nu \times \tau \rho \beta$'
           r'\nNext line \u00e9 \\gamma \"q\" \text{x} \right \tNot math"}')
    text = extract_action(raw)["text"]
    for cmd in ("\\alpha", "\\frac{a}{b}", "\\theta", "\\nabla", "\\nu", "\\times", "\\tau", "\\rho", "\\beta",
                "\\gamma", "\\text{x}", "\\right"):
        assert cmd in text, cmd
    assert "\nNext line é" in text and '"q"' in text and "\tNot math" in text  # real escapes still work
    assert "\f" not in text and "\x08" not in text


# ---------------------------------------------------------------- local model servers
class _Fake(BaseHTTPRequestHandler):
    seen: list = []

    def log_message(self, *a):
        pass

    def _send(self, code, body, ctype="application/json"):
        data = body if isinstance(body, bytes) else json.dumps(body).encode()
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/api/tags":
            return self._send(200, {"models": [{"name": "llama3.1:8b"}, {"name": "qwen2.5:7b"}]})
        if self.path == "/api/version":
            return self._send(200, {"version": "0.9.0"})
        if self.path == "/v1/models":
            return self._send(200, {"object": "list", "data": [{"id": "local-model"}]})
        self._send(404, {"error": "nope"})

    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)))
        type(self).seen.append((self.path, body, dict(self.headers)))
        if self.path == "/api/chat":
            lines = [{"message": {"role": "assistant", "content": c}, "done": False} for c in ("Hel", "lo ", "there")]
            lines.append({"message": {"role": "assistant", "content": ""}, "done": True})
            if "TOOLS PLEASE" in body["messages"][-1]["content"]:
                lines = [{"message": {"role": "assistant", "content": "", "tool_calls": [{"function": {"name": "x"}}]}, "done": True}]
            return self._send(200, ("\n".join(json.dumps(x) for x in lines) + "\n").encode(), "application/x-ndjson")
        if self.path == "/v1/chat/completions":
            chunks = [{"choices": [{"delta": {"content": c}}]} for c in ("Hi ", "from ", "LM")]
            if "TOOLS PLEASE" in body["messages"][-1]["content"]:
                chunks = [{"choices": [{"delta": {"tool_calls": [{"id": "1"}]}, "finish_reason": "tool_calls"}]}]
            sse = "".join(f"data: {json.dumps(c)}\n\n" for c in chunks) + "data: [DONE]\n\n"
            return self._send(200, sse.encode(), "text/event-stream")
        self._send(404, {"error": "nope"})


@pytest.fixture()
def fake_http():
    _Fake.seen = []
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), _Fake)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{httpd.server_address[1]}"
    httpd.shutdown()


def test_ollama_streams_without_tools(data_dir, fake_http, monkeypatch):
    from app import agents
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # must be ignored for local servers
    b = agents.get("ollama")
    agents.set_active("ollama", url=fake_http.replace("127.0.0.1", "localhost"))
    det = b.detect()
    assert det.available and det.version == "0.9.0" and "llama3.1:8b" in det.note
    assert b.models() == ["llama3.1:8b", "qwen2.5:7b"]
    assert list(b.ask_stream("hello?", system="be brief")) == ["Hel", "lo ", "there"]
    path, body, _ = _Fake.seen[-1]
    assert path == "/api/chat" and body["model"] == "llama3.1:8b" and body["stream"] is True
    assert body["messages"][0] == {"role": "system", "content": "be brief"}
    assert "tools" not in body and "functions" not in body
    agents.set_active("ollama", model="qwen2.5:7b")
    assert b.ask("x") == "Hello there" and _Fake.seen[-1][1]["model"] == "qwen2.5:7b"
    with pytest.raises(agents.ToolGuardError):
        list(b.ask_stream("TOOLS PLEASE"))


def test_openai_local_streams_without_tools(data_dir, fake_http):
    from app import agents
    b = agents.get("openai-local")
    agents.set_active("openai-local", url=fake_http)
    assert b.base_url() == fake_http + "/v1"
    assert b.detect().available and b.models() == ["local-model"]
    assert "".join(b.ask_stream("q", system="s")) == "Hi from LM"
    path, body, headers = _Fake.seen[-1]
    assert path == "/v1/chat/completions" and body["model"] == "local-model" and body["stream"]
    assert "tools" not in body and "functions" not in body and "tool_choice" not in body
    assert not any(k.lower() == "authorization" for k in headers)
    with pytest.raises(agents.ToolGuardError):
        list(b.ask_stream("TOOLS PLEASE"))


@pytest.mark.parametrize("url", ["http://192.168.1.5:11434", "http://example.com", "http://127.0.0.1.nip.io:1234",
                                 "file:///etc/passwd", "http://user:pw@127.0.0.1:1234", "http://0.0.0.0:11434",
                                 "http://[::2]:1234", "ftp://127.0.0.1"])
def test_local_servers_refuse_non_loopback(data_dir, url):
    from app import agents
    for bid in ("ollama", "openai-local"):
        with pytest.raises(agents.AgentError):
            agents.set_active(bid, url=url)
    from app.agents.localhttp import check_loopback
    assert check_loopback("http://[::1]:1234/v1/") == "http://[::1]:1234/v1"


def test_local_server_down_is_reported(data_dir):
    from app import agents
    agents.set_active("ollama", url="http://127.0.0.1:1")
    det = agents.get("ollama").detect()
    assert not det.available and det.note
    with pytest.raises(agents.AgentError):
        agents.get("ollama").ask("x")


# ---------------------------------------------------------------- the server routes through the active backend
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


def _sse_events(text):
    out = []
    for frame in text.split("\n\n"):
        ev, data = None, []
        for line in frame.split("\n"):
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].lstrip())
        if ev:
            out.append((ev, json.loads("\n".join(data))))
    return out


def test_server_uses_active_backend(server):
    srv, c = server
    from app import agents
    fb = FakeBackend("fake", script=["A plain chat answer about plasma.", "Streamed answer.",
                                     "- Nothing important missing."] * 3)
    agents.register(fb)
    try:
        r = c.put("/api/agents/active", json={"id": "fake"})
        assert r.status_code == 200 and r.json()["active"] == "fake"
        st = c.get("/api/status").json()
        assert st["agent"]["name"] == "Fake fake" and st["claude"]["available"] and "Fake fake" in st["claude"]["detail"]
        lst = c.get("/api/agents").json()
        assert lst["active"] == "fake" and lst["chosen"] == "fake"
        assert {a["id"] for a in lst["agents"]} >= {"fake", "claude", "ollama", "openai-local"}
        assert all({"name", "kind", "home", "cost_note", "detection"} <= set(a) for a in lst["agents"])
        c.post("/api/notes", json={"type": "note", "private": False, "title": "Plasma notes",
                                     "body": "Debye length shields charges."})
        r = c.post("/api/chat", json={"question": "What shields charges?", "history": []})
        assert r.status_code == 200 and r.json()["answer"] == "A plain chat answer about plasma."
        prompt, system = fb.prompts[-1]
        assert "Debye length" in prompt and "TASK:" in prompt and system == srv.SYSTEM
        r = c.post("/api/chat/stream", json={"question": "What shields charges?", "history": []})
        evs = _sse_events(r.text)
        assert evs[-1][0] == "done" and evs[-1][1]["answer"] == "Streamed answer."
        assert "".join(d["text"] for e, d in evs if e == "delta") == "Streamed answer."
        assert c.put("/api/agents/active", json={"id": "nope"}).status_code == 404
        assert c.put("/api/agents/active", json={"id": "ollama", "url": "http://10.0.0.1:11434"}).status_code == 400
    finally:
        agents.unregister("fake")


def test_server_research_universal_loop(server):
    srv, c = server
    from app import agents
    nid = c.post("/api/notes", json={"type": "note", "title": "Sheath notes",
                                     "body": "Ions enter the sheath at the Bohm speed."}).json()["id"]
    fb = FakeBackend("fake", script=[
        '{"action":"search","queries":["Bohm speed"]}',
        '{"action":"read","id":"%s"}' % nid,
        '{"action":"answer","text":"Ions enter at the Bohm speed [[%s]]."}' % nid])
    agents.register(fb)
    try:
        agents.set_active("fake")
        r = c.post("/api/research/stream", json={"question": "How fast do ions enter the sheath?"})
        evs = _sse_events(r.text)
        kinds = [e for e, _ in evs]
        assert kinds[-1] == "done" and "error" not in kinds, evs[-3:]
        labels = [d["label"] for e, d in evs if e == "step" and d["status"] == "running"]
        assert labels[0].startswith("Searching your library") and labels[1].startswith("Searching: Bohm speed")
        assert labels[2] == f"Opening [[{nid}]]"
        done = evs[-1][1]
        assert done["ended"] == "done" and done["mode"] == "research" and f"[[{nid}]]" in done["answer"]
        assert done["sources"][0]["id"] == nid and done["sources"][0]["cited"]
        assert done["timing"]["tool_calls"] == 2
        # step cap: the fallback (no-tools) answer is written by the same backend
        fb.script = ['{"action":"search","query":"x"}'] * 20 + ["Best effort answer [[%s]]." % nid]
        from app.agents import research
        old = research.MAX_STEPS
        research.MAX_STEPS = 2
        try:
            evs = _sse_events(c.post("/api/research/stream", json={"question": "Bohm?"}).text)
        finally:
            research.MAX_STEPS = old
        done = evs[-1][1]
        assert done["ended"] == "max_turns"
        assert any(d["label"].startswith("Step limit reached") for e, d in evs if e == "step")
        assert fb.prompts[-1][1] == srv.agent_mod.FALLBACK_SYSTEM
    finally:
        agents.unregister("fake")


def test_server_research_guard_error(server):
    _, c = server
    from app import agents
    from app.agents.base import ToolGuardError

    class Guarded(FakeBackend):
        def ask_stream(self, prompt, *, system="", timeout=180):
            raise ToolGuardError("Stopped: it tried to call a tool.")
            yield ""
    g = Guarded("guarded")
    agents.register(g)
    try:
        agents.set_active("guarded")
        c.post("/api/notes", json={"type": "note", "title": "N", "body": "text"})
        evs = _sse_events(c.post("/api/research/stream", json={"question": "q?"}).text)
        assert evs[-1][0] == "error" and evs[-1][1]["guard"] is True
        r = c.post("/api/chat", json={"question": "q?", "history": []})
        assert r.status_code == 502 and "tool" in r.json()["detail"]
    finally:
        agents.unregister("guarded")


def test_status_without_any_agent(server, monkeypatch):
    _, c = server
    from app import agents
    for b in agents.all_backends():
        monkeypatch.setattr(type(b), "detect", lambda self: agents.Detection(False, note="missing"))
    agents.forget_detection()
    st = c.get("/api/status").json()
    assert st["claude"]["available"] is False and "No AI agent found" in st["claude"]["detail"]
    assert st["agent"]["id"] == "claude"


def test_status_text_in_app_js():
    js = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    assert "'Claude ready'" not in js and "st-model" in js  # name (+ model) only; the dot shows ready
    s = (ROOT / "static" / "settings.js").read_text(encoding="utf-8")
    assert "id: 'agent.choose'" in s and "It never needs an API key." in s


def test_chat_about_one_paper(server):
    """Ask about this paper: the agent gets that paper (whole text when it fits) and your shareable notes on it,
    not the rest of the library; private notes on it stay out; a private paper is refused; related is opt-in."""
    srv, c = server
    from app import agents
    fb = FakeBackend("fake", script=["About the focus paper."] * 6)
    agents.register(fb)
    try:
        c.put("/api/agents/active", json={"id": "fake"})
        focus = c.post("/api/notes", json={"type": "paper", "title": "Sheath edge criterion in Hall thrusters",
                                             "abstract": "We revisit the Bohm criterion at the sheath edge."}).json()
        srv.store.text_path(focus["id"]).write_text(
            "[[page 1]]\nThe Bohm criterion requires ions to enter the sheath at the sound speed.\n"
            "[[page 2]]\nSecondary electron emission weakens the sheath potential drop.", encoding="utf-8")
        c.post("/api/notes", json={"type": "fragment", "parent": focus["id"], "quote": "sound speed",
                                   "body": "My shareable highlight about ion entry."})
        c.post("/api/notes", json={"type": "note", "parent": focus["id"], "title": "Private hunch", "private": True,
                                   "body": "Unpublished idea about emissive walls changing the criterion."})
        other = c.post("/api/notes", json={"type": "paper", "title": "Bohm criterion in magnetized sheaths",
                                             "abstract": "Bohm criterion sheath edge magnetized plasma sound speed ions."}).json()
        srv.store.text_path(other["id"]).write_text("[[page 1]]\nMagnetized presheath and the Bohm criterion.", encoding="utf-8")
        q = {"question": "What does the Bohm criterion require here?", "history": []}
        r = c.post("/api/chat", json={**q, "paper": focus["id"]})
        assert r.status_code == 200 and r.json()["paper"] == focus["id"]
        prompt, _ = fb.prompts[-1]
        assert "FOCUS PAPER" in prompt and "Secondary electron emission" in prompt and "[[page 2]]" in prompt
        assert "My shareable highlight" in prompt
        assert "Unpublished idea" not in prompt and "Private hunch" not in prompt
        assert "Magnetized presheath" not in prompt  # the rest of the library stays out by default
        assert [s["id"] for s in r.json()["sources"]] == [focus["id"]]
        r = c.post("/api/chat", json={**q, "paper": focus["id"], "include_related": True})
        assert r.status_code == 200
        assert "RELATED PAPERS" in fb.prompts[-1][0] and "Magnetized presheath" in fb.prompts[-1][0]
        r = c.post("/api/chat/stream", json={**q, "paper": focus["id"]})
        assert _sse_events(r.text)[-1][0] == "done"
        # from the PDF viewer: the page being read and a selected passage go along with the question
        r = c.post("/api/chat", json={**q, "paper": focus["id"], "page": 2,
                                      "selection": "Secondary electron emission weakens the sheath potential drop."})
        assert r.status_code == 200
        prompt = fb.prompts[-1][0]
        assert "THE STUDENT SELECTED THIS PASSAGE on p. 2" in prompt and "> Secondary electron emission weakens" in prompt
        r = c.post("/api/chat", json={**q, "paper": focus["id"], "page": 2})
        assert "reading page 2" in fb.prompts[-1][0]
        r = c.post("/api/chat", json={**q, "paper": focus["id"], "selection": "x" * 9000})
        assert "x" * 4000 in fb.prompts[-1][0] and "x" * 4001 not in fb.prompts[-1][0]  # selections are capped
        c.post(f"/api/notes/{focus['id']}/privacy", json={"private": True})
        n = len(fb.prompts)
        r = c.post("/api/chat", json={**q, "paper": focus["id"]})
        assert r.status_code == 403 and "private" in r.json()["detail"] and len(fb.prompts) == n
        assert c.post("/api/chat", json={**q, "paper": "nope-404"}).status_code == 404
    finally:
        agents.unregister("fake")


def test_chat_about_a_long_paper_includes_the_page_being_read(server, monkeypatch):
    """A paper too long to send whole goes as its best passages, plus the full page the student is on."""
    srv, c = server
    from app import agents
    real = srv.budget.get
    monkeypatch.setattr(srv.budget, "get", lambda name: 200 if name == "paper_full_chars" else real(name))
    fb = FakeBackend("fake", script=["ok"] * 3)
    agents.register(fb)
    try:
        c.put("/api/agents/active", json={"id": "fake"})
        p = c.post("/api/notes", json={"type": "paper", "title": "Long krypton paper"}).json()
        pages = [f"[[page {i}]]\n" + (f"Filler text about topic {i}. " * 20) for i in range(1, 6)]
        pages[3] = "[[page 4]]\nThe zebra-marker sentence lives only on page four. " + "More words. " * 30
        srv.store.text_path(p["id"]).write_text("\n".join(pages), encoding="utf-8")
        r = c.post("/api/chat", json={"question": "What is on this page?", "history": [], "paper": p["id"], "page": 4})
        assert r.status_code == 200
        prompt = fb.prompts[-1][0]
        assert f"THE PAGE THEY ARE READING ([[{p['id']}]] p. 4)" in prompt and "zebra-marker" in prompt
    finally:
        agents.unregister("fake")

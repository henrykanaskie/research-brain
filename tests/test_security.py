"""Security regression tests (see SECURITY.md): DNS rebinding and cross-site requests, SSRF in URL import,
untrusted PDFs and note files, path traversal, the Claude CLI lockdown, agent tool input, XSS in the
renderer, file permissions."""
from __future__ import annotations

import asyncio
import base64
import hashlib
import http.server
import importlib
import io
import json
import os
import re
import shutil
import socket
import stat
import subprocess
import sys
import textwrap
import threading
import time
from pathlib import Path

import pytest
import requests

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
from rb_testserver import run_server  # noqa: E402


def _pdf_bytes(text="Langmuir probe sheath theory") -> bytes:
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf)
    c.drawString(72, 700, text)
    c.showPage()
    c.save()
    return buf.getvalue()


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    yield TestClient(server.app), server
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]


@pytest.fixture(scope="module")
def live(tmp_path_factory):
    vault = tmp_path_factory.mktemp("sec") / "vault"
    env = {"RB_WATCH": "0", "RB_MEMORY_WATCH": "0", "RB_CITES_ON_IMPORT": "0",
           "RB_MAX_JSON_MB": "0.05", "RB_MAX_UPLOAD_MB": "1"}
    with run_server(vault, env=env) as srv:
        yield srv


def raw_request(port: int, head: str, body: bytes = b"") -> tuple[int, dict, bytes]:
    """One HTTP/1.1 request with exactly these headers (requests would fix up Host)."""
    with socket.create_connection(("127.0.0.1", port), timeout=10) as s:
        s.sendall(head.replace("\n", "\r\n").encode() + b"\r\n" + body)
        data = b""
        while True:
            chunk = s.recv(65536)
            if not chunk:
                break
            data += chunk
    head_b, _, rest = data.partition(b"\r\n\r\n")
    lines = head_b.decode("latin-1").split("\r\n")
    status = int(lines[0].split()[1])
    headers = {k.lower(): v.strip() for k, v in (l.split(":", 1) for l in lines[1:] if ":" in l)}
    return status, headers, rest


# ---------------------------------------------------------------- DNS rebinding: the Host allowlist
@pytest.mark.parametrize("host,ok", [
    ("127.0.0.1:{port}", True), ("localhost:{port}", True), ("[::1]:{port}", True), ("LOCALHOST.:{port}", True),
    ("evil.example:{port}", False), ("evil.example", False), ("127.0.0.1.evil.example:{port}", False),
    ("localhost@evil.example", False), ("", False), ("192.168.1.5:{port}", False),
])
def test_host_header_allowlist(live, host, ok):
    for path in ("/api/status", "/api/graph", "/", "/static/app.js"):
        h = f"Host: {host.format(port=live.port)}\n" if host else ""
        status, headers, _ = raw_request(live.port, f"GET {path} HTTP/1.1\n{h}Connection: close\n")
        if ok:
            assert status == 200, (host, path)
        else:
            assert status in (400, 421), (host, path, status)  # 400: uvicorn itself rejects a missing Host


def test_rb_host_is_allowed(tmp_path):
    with run_server(tmp_path / "vault", env={"RB_HOST": "100.101.102.103", "RB_WATCH": "0"}) as srv:
        st, _, _ = raw_request(srv.port, f"GET /api/status HTTP/1.1\nHost: 100.101.102.103:{srv.port}\nConnection: close\n")
        # the Host is accepted; a request naming another host than loopback must come from a paired device
        # (tests/test_pairing.py), so it gets 401 rather than 421
        assert st == 401
        st, _, _ = raw_request(srv.port, f"GET /api/status HTTP/1.1\nHost: 100.101.102.104:{srv.port}\nConnection: close\n")
        assert st == 421


# ---------------------------------------------------------------- cross-site requests
def test_origin_must_be_this_app(live):
    b, port = live.base, live.port
    ok_origins = [b, f"http://localhost:{port}", f"http://[::1]:{port}"]
    bad_origins = ["https://evil.example", "http://localhost:3000", f"http://127.0.0.1:{port + 1}", "null",
                   f"http://evil.example:{port}", "chrome-extension://abcdefghijklmnopabcdefghijklmnop",
                   f"http://127.0.0.1:{port}.evil.example", "file://"]
    for o in ok_origins:
        r = requests.post(b + "/api/notes", json={"title": "from app"}, headers={"Origin": o}, timeout=10)
        assert r.status_code == 201, o
    for o in bad_origins:
        for method, path in (("post", "/api/notes"), ("get", "/api/graph"), ("options", "/api/notes")):
            r = getattr(requests, method)(b + path, json={"title": "x"} if method == "post" else None,
                                          headers={"Origin": o, "Access-Control-Request-Method": "POST"}, timeout=10)
            assert r.status_code == 403, (o, method, path)
            assert "access-control-allow-origin" not in {k.lower() for k in r.headers}


def test_simple_cross_site_requests_cannot_mutate(live):
    """What an HTML form or a no-cors fetch can send without a CORS preflight is refused, Origin or not."""
    b = live.base
    before = len(requests.get(b + "/api/graph", timeout=10).json()["nodes"])
    bodies = [("text/plain", b'{"title": "csrf"}'), ("application/x-www-form-urlencoded", b"title=csrf"),
              ("multipart/form-data; boundary=x", b'--x\r\nContent-Disposition: form-data; name="title"\r\n\r\ncsrf\r\n--x--\r\n'),
              ("", b'{"title": "csrf"}')]
    for ctype, body in bodies:
        headers = {"Content-Type": ctype} if ctype else {}
        r = requests.post(b + "/api/notes", data=body, headers=headers, timeout=10)
        assert r.status_code == 415, ctype
        r = requests.put(b + "/api/notes/x", data=body, headers=headers, timeout=10)
        assert r.status_code == 415, ctype
    assert len(requests.get(b + "/api/graph", timeout=10).json()["nodes"]) == before


def test_fetch_metadata_blocks_cross_site_reads(live):
    b = live.base
    for site in ("cross-site", "same-site"):
        for path in ("/api/graph", "/api/search?q=a", "/pdf/x", "/api/memory"):
            r = requests.get(b + path, headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "no-cors",
                                                "Sec-Fetch-Dest": "script"}, timeout=10)
            assert r.status_code == 403, (site, path)
        # clicking a link to the app from another site still opens it
        r = requests.get(b + "/", headers={"Sec-Fetch-Site": site, "Sec-Fetch-Mode": "navigate",
                                           "Sec-Fetch-Dest": "document"}, timeout=10)
        assert r.status_code == 200
    # the app's own requests, and clients without fetch metadata (curl, iOS Shortcuts, the MCP bridge)
    assert requests.get(b + "/api/graph", headers={"Sec-Fetch-Site": "same-origin"}, timeout=10).status_code == 200
    assert requests.get(b + "/api/graph", timeout=10).status_code == 200


def test_security_headers_and_csp(live):
    r = requests.get(live.base + "/", timeout=10)
    h = {k.lower(): v for k, v in r.headers.items()}
    assert h["x-content-type-options"] == "nosniff"
    assert h["cross-origin-resource-policy"] == "same-origin"
    assert h["referrer-policy"] == "no-referrer"
    assert h["x-frame-options"] == "SAMEORIGIN"
    csp = {d.split()[0]: d.split()[1:] for d in h["content-security-policy"].split(";") if d.strip()}
    assert "'unsafe-inline'" not in csp["script-src"] and "'unsafe-eval'" not in csp["script-src"]
    assert csp["object-src"] == ["'none'"] and csp["connect-src"] == ["'self'"]
    assert not [s for d in ("img-src", "connect-src", "script-src", "font-src") for s in csp[d] if s.startswith("http")]
    # every inline script of the app's pages is allowed by its hash (and nothing else inline)
    for page in ("index.html", "viewer.html"):
        src = (ROOT / "static" / page).read_text(encoding="utf-8")
        for m in re.finditer(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", src):
            digest = base64.b64encode(hashlib.sha256(m.group(1).encode()).digest()).decode()
            assert f"'sha256-{digest}'" in csp["script-src"], page
    j = requests.get(live.base + "/api/status", timeout=10)
    assert j.headers["X-Content-Type-Options"] == "nosniff" and "Content-Security-Policy" not in j.headers


def test_body_size_limits(live):
    b = live.base
    big = json.dumps({"title": "x", "body": "a" * 80_000})  # RB_MAX_JSON_MB=0.05 on this server
    r = requests.post(b + "/api/notes", data=big, headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code == 413

    def chunks():
        for _ in range(20):
            yield b"a" * 8000
    r = requests.post(b + "/api/notes", data=chunks(), headers={"Content-Type": "application/json"}, timeout=10)
    assert r.status_code in (400, 413)  # counted as it streams in
    # uploads have their own (here 1 MB) limit, checked before the body is read
    r = requests.post(b + "/api/import/pdf", files={"file": ("a.pdf", b"%PDF-1.4" + b"0" * (2 << 20), "application/pdf")},
                      timeout=20)
    assert r.status_code == 413
    r = requests.post(b + "/api/import/pdf", files={"file": ("a.pdf", b"not a pdf at all", "application/pdf")}, timeout=10)
    assert r.status_code == 400


def test_port_is_learned_from_the_socket_not_the_host_header(app_env):
    """The agent's MCP bridge calls back to _server_port(); a forged Host port must not redirect it."""
    import httpx
    _, server = app_env
    server._bound_port.clear()

    async def go():
        transport = httpx.ASGITransport(app=server.app, client=("127.0.0.1", 50123))
        async with httpx.AsyncClient(transport=transport, base_url="http://127.0.0.1:4242") as c:
            r = await c.get("/api/status", headers={"Host": "localhost:9999"})
            assert r.status_code == 200
    asyncio.run(go())
    assert server._bound_port == [4242]


# ---------------------------------------------------------------- path traversal
def test_path_traversal_is_refused(live):
    b = live.base
    secret = Path(live.log_path).parent / "secret.txt"
    secret.write_text("TOP SECRET")
    probes = ["/api/notes/..%2F..%2Fsecret", "/api/notes/..%2Fnotes%2Fx", "/pdf/..%2F..%2Fsecret",
              "/pdf/%2E%2E%2Fsecret", "/api/topics/..%2F..%2Fsecret", "/api/topics/..%5C..%5Csecret",
              "/api/memory/findings/..%2F..%2Fsecret", "/static/..%2F..%2Fsecret.txt", "/static/../app/server.py",
              "/static/%2E%2E/app/server.py", "/api/notes/a%00b", "/api/notes/ABC"]
    for p in probes:
        r = requests.get(b + p, timeout=10)
        assert r.status_code in (400, 404, 405, 422), p
        assert "TOP SECRET" not in r.text and "def api_" not in r.text, p
    r = requests.put(b + "/api/memory/findings/..%2Fx", json={"text": "hi"}, timeout=10)
    assert r.status_code in (404, 422)
    r = requests.post(b + "/api/export", json={"ids": ["../../secret"]}, timeout=10)
    assert r.status_code == 404


# ---------------------------------------------------------------- untrusted note files (YAML)
def test_yaml_bomb_note_is_refused_quickly(app_env):
    c, server = app_env
    bomb = "---\ntitle: bomb\na: &a [x, x, x, x, x, x, x, x, x]\n" + "".join(
        f"{chr(98 + i)}: &{chr(98 + i)} [{', '.join(['*' + chr(97 + i)] * 9)}]\n" for i in range(9)) + "---\nbody\n"
    (Path(server.VAULT) / "Notes" / "bomb.md").write_text(bomb)
    (Path(server.VAULT) / "Notes" / "loop.md").write_text("---\ntitle: loop\nx: &a [*a]\n---\n")
    huge = "---\ntitle: huge\nabstract: " + "a" * (1 << 21) + "\n---\n"
    (Path(server.VAULT) / "Notes" / "huge.md").write_text(huge)
    t = time.monotonic()
    for nid in ("bomb", "loop", "huge"):
        r = c.get(f"/api/notes/{nid}")
        assert r.status_code == 200 and r.json().get("_yaml_error"), nid
        assert c.put(f"/api/notes/{nid}", json={"summary": "x"}).status_code == 409  # never rewritten
    assert c.get("/api/graph").status_code == 200
    assert time.monotonic() - t < 10


def test_plain_aliases_from_older_versions_still_load(app_env):
    c, server = app_env
    (Path(server.VAULT) / "Notes" / "old.md").write_text(
        "---\ntitle: Old\ncritique: &id001\n  text: fine\n  at: '2025-01-01'\ncritiques:\n- *id001\n---\n")
    n = c.get("/api/notes/old").json()
    assert not n.get("_yaml_error") and n["critiques"][0]["text"] == "fine"
    c.put("/api/notes/old", json={"summary": "edited"})
    assert "*id" not in server.store.note_path("old").read_text(encoding="utf-8")  # written without aliases


# ---------------------------------------------------------------- untrusted PDFs
def test_pdf_parser_runs_in_a_limited_child(tmp_path, monkeypatch):
    from app import pdftext
    good = tmp_path / "good.pdf"
    good.write_bytes(_pdf_bytes())
    text, _ = pdftext.extract(good)
    assert "Langmuir" in text
    # a parser that never finishes is killed at the time limit
    hang = tmp_path / "hang.py"
    hang.write_text("import time\ntime.sleep(60)\n")
    monkeypatch.setattr(pdftext, "__file__", str(hang))
    monkeypatch.setattr(pdftext, "TIMEOUT", 1.0)
    t = time.monotonic()
    with pytest.raises(pdftext.PdfError):
        pdftext.extract(good)
    assert time.monotonic() - t < 10
    if sys.platform.startswith("linux"):  # a decompression bomb runs out of (limited) memory, not ours
        bomb = tmp_path / "bomb.py"
        bomb.write_text("x = bytearray(3 << 30)\n")
        monkeypatch.setattr(pdftext, "__file__", str(bomb))
        monkeypatch.setattr(pdftext, "TIMEOUT", 30.0)
        monkeypatch.setattr(pdftext, "MAX_MEM_MB", 512)
        with pytest.raises(pdftext.PdfError):
            pdftext.extract(good)


def test_broken_pdf_upload_is_a_clean_400(app_env):
    c, _ = app_env
    r = c.post("/api/import/pdf", files={"file": ("x.pdf", b"%PDF-1.7\n" + os.urandom(4000), "application/pdf")})
    assert r.status_code in (201, 400)  # parsed (pdfium is lenient) or refused; never a server error


# ---------------------------------------------------------------- SSRF: URL import
class _Resp:
    def __init__(self, status=200, body=b"%PDF-1.4\n%%EOF\n", headers=None):
        self.status_code, self.body, self.headers = status, body, dict(headers or {})

    def iter_content(self, n):
        for i in range(0, len(self.body), n):
            yield self.body[i:i + n]

    def close(self):
        pass


def test_urlfetch_resolves_once_and_connects_to_the_checked_address(monkeypatch):
    from app import urlfetch
    answers = iter([["93.184.216.34"], ["127.0.0.1"], ["127.0.0.1"]])  # a rebinding DNS server
    lookups, opened = [], []

    def resolve(host):
        lookups.append(host)
        return next(answers)

    def _open(url, addr=None):
        opened.append((url, addr))
        return _Resp()
    monkeypatch.setattr(urlfetch, "resolve", resolve)
    monkeypatch.setattr(urlfetch, "_open", _open)
    path, final = urlfetch.download_pdf("https://rebind.example/a.pdf")
    shutil.rmtree(path.parent)
    assert lookups == ["rebind.example"] and opened == [("https://rebind.example/a.pdf", "93.184.216.34")]

    # each redirect hop is resolved and checked again, and the second answer here is private
    answers = iter([["93.184.216.34"], ["10.0.0.7"]])
    monkeypatch.setattr(urlfetch, "_open", lambda url, addr=None: _Resp(302, headers={"Location": "https://next.example/b"}))
    with pytest.raises(urlfetch.FetchError) as e:
        urlfetch.download_pdf("https://first.example/a.pdf")
    assert e.value.status == 403


@pytest.mark.parametrize("headers", [{"Content-Type": "text/html"}, {"Content-Type": "image/png"},
                                     {"Content-Encoding": "gzip"}, {"content-encoding": "br"}])
def test_urlfetch_refuses_wrong_content_types_and_compression(monkeypatch, headers):
    from app import urlfetch
    monkeypatch.setattr(urlfetch, "resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(urlfetch, "_open", lambda url, addr=None: _Resp(headers=headers))
    with pytest.raises(urlfetch.FetchError) as e:
        urlfetch.download_pdf("https://example.com/a.pdf")
    assert e.value.status == 415


def test_urlfetch_total_time_limit(monkeypatch):
    from app import urlfetch

    class Slow(_Resp):
        def iter_content(self, n):
            for _ in range(100):
                time.sleep(0.05)
                yield b"%PDF" + b"0" * 100
    monkeypatch.setattr(urlfetch, "resolve", lambda h: ["93.184.216.34"])
    monkeypatch.setattr(urlfetch, "_open", lambda url, addr=None: Slow())
    monkeypatch.setattr(urlfetch, "TOTAL_TIMEOUT", 0.3)
    with pytest.raises(urlfetch.FetchError) as e:
        urlfetch.download_pdf("https://example.com/slow.pdf")
    assert e.value.status == 504


def test_urlfetch_real_open_pins_ip_and_keeps_host_header(monkeypatch):
    """The real _open against a local server: it connects to the given address, sends the URL's host name
    as Host, no proxy, identity encoding; and the peer check refuses non-public peers."""
    from app import urlfetch
    seen = {}

    class H(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            seen.update(host=self.headers.get("Host"), enc=self.headers.get("Accept-Encoding"), path=self.path)
            body = b"%PDF-1.4 ok"
            self.send_response(200)
            self.send_header("Content-Type", "application/pdf")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *a):
            pass
    httpd = http.server.ThreadingHTTPServer(("127.0.0.1", 0), H)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    port = httpd.server_address[1]
    monkeypatch.setenv("HTTP_PROXY", "http://127.0.0.1:9")  # would break the request if it were used
    try:
        with pytest.raises(urlfetch.FetchError) as e:  # loopback peer: refused after connecting
            urlfetch._open(f"http://papers.example:{port}/x.pdf?a=1", "127.0.0.1")
        assert e.value.status == 403 and not seen
        monkeypatch.setattr(urlfetch, "_public", lambda a: True)
        r = urlfetch._open(f"http://papers.example:{port}/x.pdf?a=1", "127.0.0.1")
        assert r.status_code == 200 and b"".join(r.iter_content(1024)) == b"%PDF-1.4 ok"
        r.close()
        assert seen == {"host": f"papers.example:{port}", "enc": "identity", "path": "/x.pdf?a=1"}
    finally:
        httpd.shutdown()


# ---------------------------------------------------------------- the Claude CLI lockdown
FAKE_CLI = textwrap.dedent('''\
    #!{py}
    import json, os, sys
    if "--version" in sys.argv:
        print("9.9.9 (fake)"); sys.exit(0)
    json.dump({{"argv": sys.argv, "cwd": os.getcwd(), "env": dict(os.environ)}}, open(os.environ["FAKE_OUT"], "w"))
    print(json.dumps({{"type": "system", "subtype": "init", "tools": [], "mcp_servers": []}}), flush=True)
    print(json.dumps({{"type": "result", "subtype": "success", "result": "hello"}}), flush=True)
''')


def test_claude_cli_gets_no_settings_hooks_or_claude_md(tmp_path, monkeypatch):
    from app import claude_cli
    exe = tmp_path / "claude"
    exe.write_text(FAKE_CLI.format(py=sys.executable))
    exe.chmod(0o755)
    out = tmp_path / "out.json"
    monkeypatch.setenv("RB_CLAUDE_BIN", str(exe))
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    monkeypatch.setenv("FAKE_OUT", str(out))
    monkeypatch.setenv("NODE_OPTIONS", "--require /tmp/evil.js")
    monkeypatch.setenv("RB_AGENT_DEV_TOKEN", "secret-dev-token")
    monkeypatch.setattr(claude_cli, "_stream_strict_ok", None)
    assert claude_cli.ask("q", "ctx") == "hello"
    got = json.loads(out.read_text(encoding="utf-8"))
    argv = got["argv"]
    assert argv[argv.index("--setting-sources") + 1] == ""  # no user/project settings, hooks or CLAUDE.md
    assert argv[argv.index("--tools") + 1] == ""
    for flag in ("--disable-slash-commands", "--strict-mcp-config", "--no-chrome", "--no-session-persistence"):
        assert flag in argv
    env = got["env"]
    assert "NODE_OPTIONS" not in env and "RB_AGENT_DEV_TOKEN" not in env and "ANTHROPIC_API_KEY" not in env
    assert env["ENABLE_CLAUDEAI_MCP_SERVERS"] == "false" and env["DISABLE_TELEMETRY"] == "1"
    # the working directory is private and no folder above it is writable by other users,
    # so nobody can plant a CLAUDE.md the CLI would pick up from a parent folder (e.g. /tmp/CLAUDE.md)
    cwd = Path(got["cwd"])
    assert (os.name == "nt" or stat.S_IMODE(cwd.stat().st_mode) == 0o700) and not list(cwd.iterdir())
    if claude_cli._private_base() is not None:
        for d in cwd.resolve().parents:
            assert not (d.stat().st_mode & 0o002), d


def test_private_workdir_refuses_shared_parents(tmp_path, monkeypatch):
    from app import claude_cli
    shared = tmp_path / "shared"
    shared.mkdir()
    shared.chmod(0o777)
    monkeypatch.setenv("RB_CLAUDE_WORKDIR", str(shared / "rb"))
    assert claude_cli._private_base() is None
    monkeypatch.setenv("RB_CLAUDE_WORKDIR", str(tmp_path / "mine" / "rb"))
    (tmp_path / "mine").mkdir(mode=0o700)
    tmp_path.chmod(0o700)
    base = claude_cli._private_base()
    if all(not (d.stat().st_mode & 0o022) for d in tmp_path.resolve().parents):
        assert base is not None and stat.S_IMODE(base.stat().st_mode) == 0o700


# ---------------------------------------------------------------- agent tool input
def test_agent_tool_arguments_are_validated():
    from app import agent
    assert agent.validate_args("search", {"queries": ["a", "b"], "k": "3"}) == {"queries": ["a", "b"], "k": 3}
    bad = [("search", {"queries": ["x"] * 9}), ("search", {"queries": ["x" * 5000]}), ("search", {"cmd": "rm"}),
           ("read_pages", {"id": "a", "start": "1e99999"}), ("read_pages", {"id": "a", "start": True}),
           ("read_note", {"id": {"$gt": ""}}), ("read_note", {"id": "a" * 1000}), ("recent", {"run": 1}),
           ("remember", {"text": "a" * 5000, "sources": ["x"]}), ("remember", {"text": "ok", "sources": ["x"] * 50}),
           ("memory_search", "not a dict")]
    for name, args in bad:
        with pytest.raises(ValueError):
            agent.validate_args(name, args)


def test_agent_tool_endpoint_rejects_bad_input(tmp_path):
    env = {"RB_AGENT_DEV_TOKEN": "dev-token", "RB_WATCH": "0", "RB_MEMORY_WATCH": "0"}
    with run_server(tmp_path / "vault", env=env) as srv:
        requests.post(srv.base + "/api/notes", json={"title": "A note"}, timeout=10)
        h = {"X-RB-Agent-Token": "dev-token"}
        r = requests.post(srv.base + "/api/agent/tools/search", json={"args": {"queries": ["x" * 9999]}}, headers=h, timeout=10)
        assert r.json()["is_error"] and "Tool error" in r.json()["text"]
        r = requests.post(srv.base + "/api/agent/tools/recent", json={"args": {"n": 3}}, headers=h, timeout=10)
        assert not r.json()["is_error"]
        r = requests.post(srv.base + "/api/agent/tools/recent", json={"args": {}}, timeout=10)
        assert r.status_code == 403  # no token


# ---------------------------------------------------------------- XSS in the markdown renderer
NODE = shutil.which("node")
RENDER_JS = """
const [,, marked, katex, le] = process.argv;
global.window = global; global.addEventListener = () => {};
global.navigator = { platform: 'Linux' };
global.document = { addEventListener() {}, getElementById() { return null; },
  createElement() { return { style: {}, setAttribute() {} }; }, head: { appendChild() {} } };
global.marked = require(marked); global.katex = require(katex);
require(le);
const cases = JSON.parse(require('fs').readFileSync(0, 'utf8'));
process.stdout.write(JSON.stringify(cases.map((c) => [window.LatexEditor.render(c, {}),
                                                      window.LatexEditor.render(c, { inline: true })])));
"""
XSS_CASES = [
    "[a](javascript:alert(1))", "[a](<java\tscript:alert(1)>)", "[a](<java\nscript:alert(1)>)",
    "[a](JaVaScRiPt:alert(1))", "[a](&#106;avascript:alert(1))", "[a]( vbscript:msgbox(1))",
    "[a](data:text/html,<script>alert(1)</script>)", "![x](javascript:alert(1))", "<javascript:alert(1)>",
    "<img src=x onerror=alert(1)>", "<script>alert(1)</script>", "<a href='javascript:alert(1)'>x</a>",
    "<svg onload=alert(1)>", "$\\href{javascript:alert(1)}{x}$", "$$\\htmlClass{x}{y}\\url{javascript:x}$$",
    "[x](https://ok.example \"t\\\" onmouseover=alert(1) x=\\\"\")", "LEPH0XL<img src=x onerror=alert(1)>",
    "`<img src=x onerror=alert(1)>`", "```\n<script>alert(1)</script>\n```",
]


def _bad_html(out: str) -> list[str]:
    bad = []
    for m in re.finditer(r"<([a-zA-Z][\w-]*)([^>]*)>", out):
        tag, attrs = m.group(1).lower(), m.group(2)
        if tag in ("script", "iframe", "object", "embed", "svg", "style", "base", "form", "meta", "link"):
            bad.append(m.group(0))
        for am in re.finditer(r"([^\s=/>]+)(?:\s*=\s*(\"[^\"]*\"|'[^']*'|[^\s>]+))?", attrs):
            name, value = am.group(1).lower(), (am.group(2) or "").strip("\"'")
            if name.startswith("on"):
                bad.append(m.group(0))
            if name not in ("href", "src"):
                continue
            v = re.sub(r"[\x00-\x20]", "", value.replace("&#9;", "").replace("&#10;", "")).lower()
            if re.match(r"^[a-z][a-z0-9+.-]*:", v) and not re.match(r"^(https?|mailto):", v):
                bad.append(m.group(0))
    return bad


@pytest.mark.skipif(not NODE, reason="node is not installed")
@pytest.mark.parametrize("le", ["static/latex-editor.js", "extension/vendor/latex-editor.js"])
def test_renderer_neutralises_xss(tmp_path, le):
    script = tmp_path / "render.js"
    script.write_text(RENDER_JS)
    out = subprocess.run([NODE, str(script), str(ROOT / "static/vendor/marked.min.js"),
                          str(ROOT / "static/vendor/katex/katex.min.js"), str(ROOT / le)],
                         input=json.dumps(XSS_CASES), capture_output=True, text=True, timeout=60)
    assert out.returncode == 0, out.stderr
    for case, pair in zip(XSS_CASES, json.loads(out.stdout)):
        for html in pair:
            assert not _bad_html(html), (case, html)
    ok = json.loads(subprocess.run([NODE, str(script), str(ROOT / "static/vendor/marked.min.js"),
                                    str(ROOT / "static/vendor/katex/katex.min.js"), str(ROOT / le)],
                                   input=json.dumps(["[ok](https://a.example/x)", "[rel](#sec-2)"]),
                                   capture_output=True, text=True, timeout=60).stdout)
    assert 'href="https://a.example/x"' in ok[0][0] and 'href="#sec-2"' in ok[1][0]


@pytest.mark.skipif(not NODE, reason="node is not installed")
def test_app_js_safe_url():
    src = (ROOT / "static" / "app.js").read_text(encoding="utf-8")
    fn = re.search(r"function safeUrl\(href\) \{[\s\S]*?\n  \}", src).group(0)
    js = fn + "\nconst cases = " + json.dumps(["javascript:x", "java\tscript:x", "\x01javascript:x", " data:x",
                                                 "vbscript:x", "https://a.b", "mailto:a@b", "#x", "/viewer/a",
                                                 "notes/a.html"]) + ";\nprocess.stdout.write(JSON.stringify(cases.map(safeUrl)));"
    out = json.loads(subprocess.run([NODE, "-e", js], capture_output=True, text=True, timeout=30).stdout)
    assert out[:5] == [None] * 5 and all(out[5:])


def test_exported_site_escapes_note_content(app_env):
    c, server = app_env
    evil = "</script><script>alert(1)</script><img src=x onerror=alert(1)>"
    n = c.post("/api/notes", json={"title": evil, "body": evil, "summary": evil, "url": "javascript:alert(1)",
                                   "tags": [evil], "private": False}).json()
    r = c.post("/api/export", json={"ids": [n["id"]]})
    assert r.status_code == 201, r.text
    out = Path(r.json()["path"]) if "path" in r.json() else None
    root = out or next((Path(server.VAULT) / "exports").iterdir())
    for page in root.rglob("*.html"):
        html = page.read_text(encoding="utf-8")
        assert "<script>alert" not in html and "<img src=x" not in html and 'href="javascript:' not in html, page


# ---------------------------------------------------------------- file permissions
@pytest.mark.skipif(os.name != "posix", reason="POSIX permissions")
def test_vault_files_are_private(app_env):
    c, server = app_env
    n = c.post("/api/notes", json={"title": "Private"}).json()
    vault = Path(server.VAULT)
    assert stat.S_IMODE(vault.stat().st_mode) == 0o700
    assert stat.S_IMODE(server.store.note_path(n["id"]).stat().st_mode) & 0o077 == 0

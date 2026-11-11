"""Device pairing for phones and tablets (app/pairing.py, app/mobile_api.py, enforced in app/security.py).

Requests from this computer (loopback peer + loopback Host, or the server's own address) need nothing.
Everything else needs a paired device's token, or gets 401; codes are single use, expire, are rate limited;
revoking locks the device out; paired devices can't reach the computer-only endpoints.
"""
from __future__ import annotations

import importlib
import json
import sys
import time
import urllib.request

import pytest

from rb_testserver import run_server

PHONE = ("100.101.102.7", 51000)
NAME = "my-mac.tail1234.ts.net"


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_FAKE_CLAUDE", "1")
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    monkeypatch.setenv("RB_ALLOWED_HOSTS", NAME)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    server = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    laptop = TestClient(server.app)  # in-process client: counts as this computer
    phone = TestClient(server.app, base_url=f"http://{NAME}", client=PHONE)
    other = TestClient(server.app, base_url=f"http://{NAME}", client=("100.101.102.9", 51000))
    yield laptop, phone, other, server
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]


def pair(laptop, phone, name="Test phone"):
    code = laptop.post("/api/pair/start").json()["code"]
    r = phone.post("/api/pair/claim", json={"code": code, "name": name})
    assert r.status_code == 201, r.text
    return r


def test_unpaired_device_gets_401(env):
    laptop, phone, *_ = env
    assert laptop.get("/api/graph").status_code == 200        # this computer: no pairing needed
    r = phone.get("/api/graph")
    assert r.status_code == 401
    assert "Pair this device" in r.json()["detail"]
    page = phone.get("/", headers={"Sec-Fetch-Dest": "document"})
    assert page.status_code == 401
    assert page.headers["content-type"].startswith("text/html")
    assert "Pair this device from your computer" in page.text
    assert phone.get("/static/app.js").status_code == 401     # not even the app code
    assert phone.get("/pdf/x").status_code == 401
    # what the pairing page needs is open
    assert phone.get("/pair").status_code == 200
    assert phone.get("/static/pair.js").status_code == 200
    assert phone.get("/static/icons/icon-192.png").status_code == 200
    assert phone.get("/manifest.webmanifest").status_code == 200


def test_pairing_code_works_once(env):
    laptop, phone, other, _ = env
    start = laptop.post("/api/pair/start").json()
    assert start["expires_in"] == 600
    assert any(u["pair_url"].startswith(f"https://{NAME}/pair?code=") for u in start["urls"])
    r = phone.post("/api/pair/claim", json={"code": start["code"], "name": "Pixel"})
    assert r.status_code == 201
    cookie = r.headers["set-cookie"]
    assert "rb_device=" in cookie and "HttpOnly" in cookie and "SameSite=strict" in cookie.replace("Strict", "strict")
    assert "Secure" not in cookie  # http here; https (tailscale serve) gets Secure
    assert phone.get("/api/graph").status_code == 200
    assert phone.get("/api/device").json()["device"]["name"] == "Pixel"
    # the same code again: refused
    r2 = other.post("/api/pair/claim", json={"code": start["code"]})
    assert r2.status_code in (400, 410)
    assert other.get("/api/graph").status_code == 401
    # devices are listed (no hashes) and the token is stored hashed, not in the vault
    devs = laptop.get("/api/devices").json()["devices"]
    assert [d["name"] for d in devs] == ["Pixel"]
    assert "hash" not in devs[0]
    from app import paths
    raw = json.dumps(paths.load_settings())
    token = phone.cookies.get("rb_device")
    assert token and token not in raw


def test_pairing_code_expires(env, monkeypatch):
    laptop, phone, *_ = env
    from app import pairing
    code = laptop.post("/api/pair/start").json()["code"]
    real = time.monotonic
    monkeypatch.setattr(pairing.time, "monotonic", lambda: real() + pairing.CODE_TTL + 5)
    r = phone.post("/api/pair/claim", json={"code": code})
    assert r.status_code in (400, 410)
    assert phone.get("/api/graph").status_code == 401


def test_revoke_locks_device_out(env):
    laptop, phone, *_ = env
    pair(laptop, phone)
    assert phone.get("/api/graph").status_code == 200
    did = laptop.get("/api/devices").json()["devices"][0]["id"]
    assert laptop.delete(f"/api/devices/{did}").status_code == 200
    assert phone.get("/api/graph").status_code == 401
    assert laptop.get("/api/devices").json()["devices"] == []


def test_paired_device_cannot_manage_computer(env):
    laptop, phone, *_ = env
    pair(laptop, phone)
    assert phone.post("/api/pair/start").status_code == 403
    assert phone.get("/api/devices").status_code == 403
    assert phone.post("/api/cite/auto-exports", json={"path": "/tmp/x.bib"}).status_code == 403
    assert phone.put("/api/agents/active", json={"id": "claude"}).status_code == 403
    assert phone.get("/api/vault/export").status_code == 403
    assert phone.get("/api/settings").status_code == 200  # reading is fine
    # the phone can unpair itself
    assert phone.post("/api/device/forget").status_code == 200
    assert laptop.get("/api/devices").json()["devices"] == []


def test_claims_are_rate_limited(env):
    laptop, phone, *_ = env
    from app import pairing
    for _ in range(pairing.FAIL_PER_CLIENT):
        assert phone.post("/api/pair/claim", json={"code": "wrong"}).status_code == 400
    good = laptop.post("/api/pair/start").json()["code"]
    assert phone.post("/api/pair/claim", json={"code": good}).status_code == 429


def test_photo_capture_from_phone(env):
    laptop, phone, *_ = env
    pair(laptop, phone)
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64
    r = phone.post("/api/capture/photo?title=Whiteboard%20equation", content=png, headers={"Content-Type": "image/png"})
    assert r.status_code == 201, r.text
    d = r.json()
    note = phone.get(f"/api/notes/{d['id']}").json()
    assert d["attachment"] in note["body"] and note["title"] == "Whiteboard equation"
    assert phone.get(d["attachment"]).content == png
    assert phone.post("/api/capture/photo", content=b"<svg/>", headers={"Content-Type": "image/png"}).status_code == 415
    assert phone.post("/api/capture/photo", content=png, headers={"Content-Type": "text/plain"}).status_code == 415
    assert laptop.get("/attachments/..%2Fnotes").status_code == 404


def test_is_local_rules():
    from app.pairing import is_local
    loop = {"client": ("127.0.0.1", 1), "server": ("127.0.0.1", 8765)}
    assert is_local(loop, {"host": "127.0.0.1:8765"})
    assert is_local(loop, {"host": "localhost:8765"})
    # tailscale serve: loopback peer, but the ts.net Host and proxy headers -> not this computer
    assert not is_local(loop, {"host": NAME})
    assert not is_local(loop, {"host": "127.0.0.1:8765", "x-forwarded-for": "100.1.2.3"})
    assert not is_local(loop, {"host": "127.0.0.1:8765", "tailscale-user-login": "me@example.com"})
    # a browser on the laptop opening its own Tailscale address (RB_HOST=100.x)
    own = {"client": ("100.64.0.1", 2), "server": ("100.64.0.1", 8765)}
    assert is_local(own, {"host": "100.64.0.1:8765"})
    assert not is_local({"client": ("100.64.0.2", 2), "server": ("100.64.0.1", 8765)}, {"host": "100.64.0.1:8765"})
    assert not is_local({"client": ("0.0.0.0", 2), "server": ("0.0.0.0", 8765)}, {"host": "x"})


def test_live_server_loopback_and_proxied(tmp_path):
    """A real socket: loopback works unpaired; the same socket with a proxy's Host/headers needs pairing."""
    vault = tmp_path / "vault"
    with run_server(vault, env={"RB_ALLOWED_HOSTS": NAME, "RB_WATCH": "0", "RB_MEMORY_WATCH": "0"}) as srv:
        with urllib.request.urlopen(srv.base + "/api/graph", timeout=10) as r:
            assert r.status == 200
        req = urllib.request.Request(srv.base + "/api/graph", headers={"Host": NAME, "X-Forwarded-For": "100.9.9.9"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 401
        req = urllib.request.Request(srv.base + "/api/pair/start", method="POST", headers={"Host": NAME, "X-Forwarded-For": "100.9.9.9"})
        with pytest.raises(urllib.error.HTTPError) as e:
            urllib.request.urlopen(req, timeout=10)
        assert e.value.code == 401


def test_shortcut_token(env):
    laptop, phone, other, _ = env
    r = laptop.post("/api/devices/token", json={"name": "iOS Shortcut"})
    assert r.status_code == 201
    tok = r.json()["token"]
    body = {"quote": "Bohm criterion", "url": "", "page_title": "Phone inbox", "note": ""}
    assert other.post("/api/clip", json=body).status_code == 401
    ok = other.post("/api/clip", json=body, headers={"Authorization": f"Bearer {tok}"})
    assert ok.status_code == 201, ok.text
    assert other.post("/api/clip", json=body, headers={"Authorization": "Bearer nope"}).status_code == 401
    assert phone.post("/api/devices/token", json={}, headers={"Authorization": f"Bearer {tok}"}).status_code == 403


def test_qr_encoder_decodes(tmp_path):
    """static/vendor/qr.js (the offline QR encoder) produces codes a standard decoder reads."""
    import shutil
    import subprocess
    cv2 = pytest.importorskip("cv2")
    np = pytest.importorskip("numpy")
    node = shutil.which("node")
    if not node:
        pytest.skip("node is not installed")
    from pathlib import Path
    qr = Path(__file__).resolve().parents[1] / "static" / "vendor" / "qr.js"
    texts = ["https://my-mac.tail1234.ts.net/pair?code=Ab3_dEf-ghIJklMNopQRst", "http://100.101.102.103:8765/pair?code=x", "x" * 200]
    js = ("const Q=require(process.argv[1]);console.log(JSON.stringify(process.argv.slice(2).map(t=>{const q=Q.encode(t);"
          "const r=[];for(let y=0;y<q.size;y++){let s='';for(let x=0;x<q.size;x++)s+=q.get(x,y)?'1':'0';r.push(s)}return r})))")
    out = json.loads(subprocess.run([node, "-e", js, str(qr), *texts], capture_output=True, text=True, check=True).stdout)
    for text, rows in zip(texts, out):
        n, q, s = len(rows), 4, 6
        img = np.full(((n + 2 * q) * s, (n + 2 * q) * s), 255, np.uint8)
        for y, row in enumerate(rows):
            for x, c in enumerate(row):
                if c == "1":
                    img[(y + q) * s:(y + q + 1) * s, (x + q) * s:(x + q + 1) * s] = 0
        got, _, _ = cv2.QRCodeDetector().detectAndDecode(img)
        assert got == text

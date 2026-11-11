"""Device pairing: who may use research brain from another device (a phone or tablet).

research brain has no password because it normally listens on 127.0.0.1 only, so only programs on this
computer can reach it. With RB_HOST / RB_ALLOWED_HOSTS (docs/mobile-capture.md) other devices can reach it,
and those must be paired first:

1. On the computer, Library settings > Phones and tablets > "Pair a phone" asks for a pairing code
   (POST /api/pair/start, computer only). The code is 128 random bits, single use, valid for 10 minutes,
   and only its SHA-256 is kept (in memory). The QR code on screen holds <address>/pair?code=<code>.
2. The phone opens that page and taps "Pair this phone" (POST /api/pair/claim, the one API path an unpaired
   device may call). The server burns the code and answers with a device token: 256 random bits in an
   HttpOnly, SameSite=Strict cookie (Secure when the page is https). Only the token's SHA-256 is stored, in
   the per-user settings.json (never in the library, so a library zip carries no tokens).
3. Every later request from a device that isn't this computer must carry a valid token, or it gets 401
   (a short "Pair this device from your computer" page for page loads). Library settings lists devices with
   when each was last seen; Revoke deletes the token's hash, so the device is locked out at once.

"This computer" means: the TCP peer is a loopback address and the Host header names loopback, or the peer
address equals the address the server accepted the connection on (the same machine, e.g. a browser on the
laptop opening its own Tailscale IP); and in both cases no proxy header (X-Forwarded-For, Forwarded,
X-Real-IP, Tailscale-User-*) is present. `tailscale serve` connects from 127.0.0.1 but adds those headers
and uses the ts.net name as Host, so phones that come in through it still need pairing.

Paired devices can do what the app does on a phone ("Lite"), but not change this computer: the paths in
COMPUTER_ONLY (auto-updating .bib files that write to disk, library import/export and profiles, the AI agent
choice, Zotero, pairing and devices themselves) answer 403 "Available on your computer" to them.

Claim attempts are rate limited: at most 10 failed claims per client address and 30 in total per
10 minutes; past that every claim gets 429 until the window passes (so codes can't be guessed in bulk,
on top of their 128 bits).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import re
import secrets
import threading
import time
from datetime import datetime, timezone

from . import paths

COOKIE = "rb_device"
CODE_TTL = 600              # seconds a pairing code stays valid
MAX_DEVICES = 20
FAIL_WINDOW = 600
FAIL_PER_CLIENT = 10
FAIL_TOTAL = 30
SEEN_EVERY = 300            # write last_seen to settings.json at most this often per device
SETTINGS_KEY = "paired_devices"
PROXY_HEADERS = ("x-forwarded-for", "forwarded", "x-real-ip", "x-forwarded-host", "tailscale-user-login",
                 "tailscale-user-name")

# What an unpaired device may load: the pairing page and claim, plus the icons and fonts that page shows.
UNPAIRED_OK = re.compile(r"^/(pair|api/pair/claim|manifest\.webmanifest|static/icons/[a-z0-9.-]+|static/brain\.svg|static/pair\.js"
                         r"|static/vendor/fonts/[A-Za-z0-9._-]+)$")
# What a paired device may not do: things that change this computer rather than the library.
COMPUTER_ONLY = [
    (re.compile(r"^/api/(pair/start|pair/codes.*|devices.*)$"), None),
    (re.compile(r"^/api/cite/auto-exports.*$"), None),
    (re.compile(r"^/api/vault/(export|import)$"), None),
    (re.compile(r"^/api/library/move-legacy$"), None),
    (re.compile(r"^/api/profiles.*$"), "write"),
    (re.compile(r"^/api/settings$"), "write"),
    (re.compile(r"^/api/agents/active$"), "write"),
    (re.compile(r"^/api/privacy/settings$"), "write"),
    (re.compile(r"^/api/zotero/.*$"), None),
    (re.compile(r"^/api/export$"), None),
    (re.compile(r"^/api/backfill/.*$"), None),
]

_lock = threading.Lock()
_codes: dict[str, float] = {}          # sha256(code) -> expiry (monotonic)
_fails: list[tuple[float, str]] = []    # (time, client) of failed claims
_seen: dict[str, float] = {}            # device id -> last time written to settings


def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _h(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


# ---------------------------------------------------------------- who is asking
def _ip(s: str):
    try:
        return ipaddress.ip_address((s or "").split("%")[0])
    except ValueError:
        return None


def is_local(scope, headers: dict) -> bool:
    """True when the request comes from this computer itself, not through a proxy."""
    client = (scope.get("client") or ("", 0))[0]
    if client == "testclient":  # Starlette's in-process TestClient (never a real socket)
        return True
    if any(h in headers for h in PROXY_HEADERS):
        return False
    peer = _ip(client)
    if peer is None:
        return False
    from .security import split_authority  # same parsing as the Host allowlist ("LOCALHOST.:8765" -> "localhost")
    auth = split_authority(headers.get("host") or "")
    host = auth[0] if auth else ""
    if peer.is_loopback:
        return host in ("127.0.0.1", "localhost", "::1") or host.startswith("127.")
    server = _ip((scope.get("server") or ("", 0))[0] or "")
    return server is not None and not server.is_unspecified and peer == server


def _cookie(headers: dict) -> str:
    raw = headers.get("cookie", "")
    for part in raw.split(";"):
        k, _, v = part.strip().partition("=")
        if k == COOKIE:
            return v.strip()
    return ""


def _devices() -> list[dict]:
    d = paths.load_settings().get(SETTINGS_KEY)
    return [x for x in d if isinstance(x, dict) and x.get("hash")] if isinstance(d, list) else []


def device_for(headers: dict) -> dict | None:
    """The paired device whose token this request carries (cookie, or "Authorization: Bearer" for the iOS
    Shortcut), or None."""
    tok = _cookie(headers)
    if not tok:
        auth = headers.get("authorization", "")
        if auth[:7].lower() == "bearer ":
            tok = auth[7:].strip()
    if not tok or len(tok) > 200:
        return None
    th = _h(tok)
    for d in _devices():
        if hmac.compare_digest(d["hash"], th):
            _touch(d["id"])
            return d
    return None


def _touch(did: str) -> None:
    now = time.monotonic()
    with _lock:
        if now - _seen.get(did, -1e9) < SEEN_EVERY:
            return
        _seen[did] = now
        devs = _devices()
        for d in devs:
            if d.get("id") == did:
                d["last_seen"] = _now_iso()
        try:
            paths.save_settings({SETTINGS_KEY: devs})
        except OSError:
            pass


def computer_only(path: str, method: str) -> bool:
    write = method.upper() not in ("GET", "HEAD", "OPTIONS")
    return any(rx.match(path) and (when is None or write) for rx, when in COMPUTER_ONLY)


# ---------------------------------------------------------------- codes and devices
def new_code() -> tuple[str, int]:
    code = secrets.token_urlsafe(16)
    with _lock:
        now = time.monotonic()
        for k in [k for k, exp in _codes.items() if exp < now]:
            del _codes[k]
        _codes[_h(code)] = now + CODE_TTL
    return code, CODE_TTL


def cancel_codes() -> None:
    with _lock:
        _codes.clear()


class PairError(Exception):
    def __init__(self, status: int, msg: str):
        super().__init__(msg)
        self.status = status


def _rate_limited(client: str) -> bool:
    now = time.monotonic()
    global _fails
    _fails = [(t, c) for t, c in _fails if now - t < FAIL_WINDOW]
    return len(_fails) >= FAIL_TOTAL or sum(1 for _, c in _fails if c == client) >= FAIL_PER_CLIENT


def claim(code: str, name: str, user_agent: str, client: str) -> tuple[dict, str]:
    """Burn a pairing code and register a device. -> (device without hash, token)."""
    with _lock:
        if _rate_limited(client):
            raise PairError(429, "Too many pairing attempts. Wait 10 minutes, then pair again from your computer.")
        k = _h(code or "")
        exp = _codes.pop(k, None)
        if exp is None or exp < time.monotonic():
            _fails.append((time.monotonic(), client))
            raise PairError(400 if exp is None else 410,
                            "This pairing code has expired or was already used. Make a new one on your computer: "
                            "Library settings, Phones and tablets, Pair a phone.")
        token = secrets.token_urlsafe(32)
        dev = {"id": secrets.token_hex(6), "name": _clean_name(name, user_agent), "hash": _h(token),
               "created": _now_iso(), "last_seen": _now_iso(), "ua": (user_agent or "")[:160]}
        devs = _devices()
        devs.append(dev)
        devs = devs[-MAX_DEVICES:]
        paths.save_settings({SETTINGS_KEY: devs})
        _seen[dev["id"]] = time.monotonic()
    return public(dev), token


def issue_token(name: str) -> tuple[dict, str]:
    """A device token made on the computer for a client without a browser (the iOS Shortcut)."""
    with _lock:
        token = secrets.token_urlsafe(32)
        dev = {"id": secrets.token_hex(6), "name": _clean_name(name, "") if name else "iOS Shortcut", "hash": _h(token),
               "created": _now_iso(), "last_seen": None, "ua": "token"}
        devs = (_devices() + [dev])[-MAX_DEVICES:]
        paths.save_settings({SETTINGS_KEY: devs})
    return public(dev), token


def _clean_name(name: str, ua: str) -> str:
    name = re.sub(r"[\x00-\x1f\x7f]", "", (name or "")).strip()[:60]
    if name:
        return name
    ua = ua or ""
    for pat, label in ((r"iPhone", "iPhone"), (r"iPad", "iPad"), (r"Android.*Mobile", "Android phone"),
                       (r"Android", "Android tablet"), (r"Macintosh", "Mac"), (r"Windows", "Windows PC")):
        if re.search(pat, ua):
            return label
    return "Device"


def public(d: dict) -> dict:
    return {k: d.get(k) for k in ("id", "name", "created", "last_seen", "ua")}


def list_devices() -> list[dict]:
    return [public(d) for d in _devices()]


def revoke(did: str) -> bool:
    with _lock:
        devs = _devices()
        keep = [d for d in devs if d.get("id") != did]
        if len(keep) == len(devs):
            return False
        paths.save_settings({SETTINGS_KEY: keep})
        _seen.pop(did, None)
    return True


def rename(did: str, name: str) -> dict | None:
    with _lock:
        devs = _devices()
        for d in devs:
            if d.get("id") == did:
                d["name"] = _clean_name(name, d.get("ua", ""))
                paths.save_settings({SETTINGS_KEY: devs})
                return public(d)
    return None


def reset_for_tests() -> None:
    with _lock:
        _codes.clear()
        _fails.clear()
        _seen.clear()


# ---------------------------------------------------------------- the pages an unpaired device sees
_PAGE = """<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<meta name="theme-color" content="#EEEFEB"><title>{title}</title>
<link rel="icon" type="image/svg+xml" href="/static/brain.svg">
<link rel="stylesheet" href="/static/vendor/fonts/fonts.css">
<style>
:root {{ --ground:#EEEFEB; --sheet:#F7F7F4; --ink:#15181B; --ink-2:#61676C; --rule:#D5D7D1; --red:#BD3A2C; color-scheme: light; }}
@media (prefers-color-scheme: dark) {{ :root {{ --ground:#121518; --sheet:#171B1F; --ink:#E5E6E1; --ink-2:#969CA0;
  --rule:#2A2F34; --red:#EC6A58; color-scheme: dark; }} }}
* {{ box-sizing: border-box; }}
body {{ margin:0; min-height:100vh; background:var(--ground); color:var(--ink); font:16px/1.55 "Archivo",-apple-system,sans-serif;
  display:flex; align-items:center; justify-content:center;
  padding: max(24px, env(safe-area-inset-top)) max(20px, env(safe-area-inset-right)) max(24px, env(safe-area-inset-bottom)) max(20px, env(safe-area-inset-left)); }}
main {{ width:min(440px,100%); }}
img {{ width:44px; height:38px; display:block; margin-bottom:28px; }}
h1 {{ font:italic 500 34px/1.1 "Bodoni Moda","Didot",Georgia,serif; margin:0 0 14px; letter-spacing:-.01em; }}
p {{ color:var(--ink-2); margin:0 0 14px; }}
.mark {{ border-left:2px solid var(--red); padding-left:14px; }}
label {{ display:block; font-size:14px; color:var(--ink-2); margin:22px 0 6px; }}
input {{ width:100%; height:48px; font:17px "Archivo",sans-serif; color:var(--ink); background:var(--sheet);
  border:1px solid var(--rule); border-radius:2px; padding:0 12px; }}
button {{ margin-top:16px; width:100%; height:50px; font:500 17px "Archivo",sans-serif; color:var(--ground); background:var(--ink);
  border:0; border-radius:2px; cursor:pointer; }}
button:disabled {{ opacity:.5; }}
#msg {{ min-height:24px; margin-top:14px; font-size:15px; }}
#msg.err {{ color:var(--red); }}
</style></head><body><main>
<img src="/static/brain.svg" alt="">
{body}
</main>{script}</body></html>"""

PAIR_BODY = """<h1>Pair this phone</h1>
<p class="mark">Your computer made this code a moment ago. Pairing lets this browser open your library until you revoke it
in Library settings on the computer.</p>
<label for="name">Name for this device</label>
<input id="name" maxlength="60" autocomplete="off" placeholder="My phone">
<button id="go" type="button">Pair this phone</button>
<div id="msg" role="status"></div>"""

# Inline scripts are allowed by hash (app/security.py hashes every inline <script> in static/*.html), so the
# pairing script is a static file instead: /static/pair.js (served to unpaired devices too).
PAIR_SCRIPT = '<script src="/static/pair.js"></script>'

UNPAIRED_BODY = """<h1>Pair this device from your computer</h1>
<p class="mark">research brain runs on your computer and only opens on devices you have paired.</p>
<p>On the computer, open Library settings, then Phones and tablets, and choose Pair a phone. Scan the code with
this device's camera.</p>"""


def page(title: str, body: str, script: str = "") -> bytes:
    return _PAGE.format(title=title, body=body, script=script).encode("utf-8")


def unpaired_page() -> bytes:
    return page("Pair this device", UNPAIRED_BODY)


def pair_page() -> bytes:
    return page("Pair this phone", PAIR_BODY, PAIR_SCRIPT)

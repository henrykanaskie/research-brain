"""Request-level defences for the local server (an ASGI middleware), plus process hardening.

research_brain has no login: it trusts whoever can talk to 127.0.0.1:<port>. That makes the browser the
main way in for an attacker, so every request passes these checks before any endpoint runs:

1. Host allowlist (DNS rebinding). A page on evil.example can point its DNS name at 127.0.0.1; the
   browser then treats our server as evil.example (same origin, no CORS needed). The Host header still
   says evil.example, so we refuse any Host that isn't 127.0.0.1, localhost, [::1] or RB_HOST
   (plus RB_ALLOWED_HOSTS, comma separated). Applies to every path, static files included.
2. Origin. A request that carries an Origin must come from this app's own origin (scheme://<allowed
   host>:<our port>) or from the research_brain Chrome extension (its pinned id; RB_EXTENSION_IDS adds
   more). Other localhost ports are other apps and are refused.
3. Fetch metadata. Without an Origin, a browser request whose Sec-Fetch-Site is cross-site or same-site
   is refused unless it is a top-level navigation (someone clicking a link to the app). That covers
   <img>/<script>/no-cors GETs from other sites and other localhost ports, which carry no Origin.
4. Body type. POST/PUT/PATCH/DELETE with a body must be application/json, or multipart/form-data on the
   two PDF upload endpoints; RAW_BODY_PATHS fixes the one type a raw-body endpoint takes (application/pdf,
   application/zip: neither can be sent cross-origin without a CORS preflight). text/plain and urlencoded bodies (what an HTML form or a "simple" no-cors
   fetch can send without a CORS preflight) are refused.
5. Body size. JSON bodies at most RB_MAX_JSON_MB (default 16), uploads at most RB_MAX_UPLOAD_MB
   (default 300). Content-Length is checked up front; chunked bodies are counted as they arrive.
6. Response headers. nosniff, no referrer, same-origin resource policy (other sites can't embed our
   PDFs or JSON), no framing by other sites, and a Content-Security-Policy on HTML pages: scripts only
   from this app (inline scripts by hash), no plugins, no connections or images from the internet.

Clients without a browser (the iOS Shortcut, curl, the agent's MCP bridge) send no Origin and no
Sec-Fetch-* headers, so they pass 2 and 3; they still need a valid Host and a JSON body.

harden_process() sets a private umask (new files 0600, folders 0700) and makes the vault folder 0700.
"""
from __future__ import annotations

import base64
import hashlib
import json
import os
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
LOOPBACK = ("127.0.0.1", "localhost", "::1")
SAFE_METHODS = ("GET", "HEAD", "OPTIONS")
UPLOAD_PATHS = re.compile(r"^/api/(import/pdf|notes/[^/]+/pdf)$")
# Raw-body endpoints (app/storage_api.py): the body type is fixed per path, and neither type can be sent
# cross-origin without a CORS preflight. path -> (allowed content types, size limit env, default MB)
RAW_BODY_PATHS = {
    "/api/import/pdf-bytes": (("application/pdf",), "RB_MAX_UPLOAD_MB", 300),
    "/api/vault/import": (("application/zip", "application/x-zip-compressed"), "RB_MAX_IMPORT_MB", 50 * 1024),
    "/api/capture/photo": (("image/jpeg", "image/png", "image/webp"), "RB_MAX_PHOTO_MB", 25),  # app/mobile_api.py
}
MB = 1 << 20

# The pinned id of the research_brain extension: derived from the public "key" in extension/manifest.json
# (Chrome computes the same id from it, wherever the unpacked folder lives). Fallback if the file is missing.
DEFAULT_EXTENSION_ID = "jjgkmeojpgkgonllpcgmoldlmbckaaia"


def _env_list(name: str) -> list[str]:
    return [x.strip().lower() for x in os.environ.get(name, "").split(",") if x.strip()]


def extension_id_from_key(key_b64: str) -> str:
    """Chrome's extension id for a manifest "key": sha256 of the DER public key, first 32 hex digits as a-p."""
    digest = hashlib.sha256(base64.b64decode(key_b64)).hexdigest()[:32]
    return "".join(chr(ord("a") + int(c, 16)) for c in digest)


def _manifest_extension_id() -> str:
    for p in (ROOT / "extension" / "manifest.json", Path(__file__).resolve().parent / "extension" / "manifest.json"):
        try:
            key = json.loads(p.read_text(encoding="utf-8")).get("key")
            if key:
                return extension_id_from_key(key)
        except (OSError, ValueError):
            continue
    return DEFAULT_EXTENSION_ID


def _norm_host(h: str) -> str:
    h = (h or "").strip().lower().rstrip(".")
    if h.startswith("[") and h.endswith("]"):
        h = h[1:-1]
    return h


def split_authority(value: str) -> tuple[str, int | None] | None:
    """'127.0.0.1:8765' -> ('127.0.0.1', 8765); '[::1]:80' -> ('::1', 80); 'localhost' -> ('localhost', None).
    None when malformed (userinfo, junk ports, spaces)."""
    v = (value or "").strip()
    if not v or any(c in v for c in " /\\@?#") or len(v) > 300:
        return None
    if v.startswith("["):
        end = v.find("]")
        if end == -1:
            return None
        host, rest = v[1:end], v[end + 1:]
        if rest and not rest.startswith(":"):
            return None
        port_s = rest[1:] if rest else ""
    elif v.count(":") == 1:
        host, port_s = v.split(":")
    elif ":" in v:  # a bare IPv6 address isn't a valid Host header
        return None
    else:
        host, port_s = v, ""
    port = None
    if port_s:
        if not port_s.isdigit() or len(port_s) > 5:
            return None
        port = int(port_s)
    return _norm_host(host), port


class Policy:
    """Which hosts, ports and extension ids are this app. Read from the environment once."""

    def __init__(self):
        rb_host = _norm_host(os.environ.get("RB_HOST", ""))
        hosts = set(LOOPBACK)
        if rb_host and rb_host not in ("0.0.0.0", "::", "*"):
            hosts.add(rb_host)
        hosts.update(_norm_host(h) for h in _env_list("RB_ALLOWED_HOSTS"))
        self.hosts = frozenset(h for h in hosts if h)
        self.extension_ids = frozenset({_manifest_extension_id(), *_env_list("RB_EXTENSION_IDS")})
        try:
            self.port = int(os.environ.get("RB_PORT", "8765"))
        except ValueError:
            self.port = 8765
        self.max_json = int(float(os.environ.get("RB_MAX_JSON_MB", "16")) * MB)
        self.max_upload = int(float(os.environ.get("RB_MAX_UPLOAD_MB", "300")) * MB)

    def limit_for(self, path: str) -> int:
        raw = RAW_BODY_PATHS.get(path)
        if raw:
            return int(float(os.environ.get(raw[1], str(raw[2]))) * MB)
        return self.max_upload if UPLOAD_PATHS.match(path) else self.max_json

    def host_ok(self, host: str, testclient: bool = False) -> bool:
        return host in self.hosts or (testclient and host == "testserver")

    def origin_ok(self, origin: str, server_port: int | None, testclient: bool = False) -> bool:
        m = re.fullmatch(r"chrome-extension://([a-p]{32})", origin or "")
        if m:
            return m.group(1) in self.extension_ids
        m = re.fullmatch(r"(https?)://([^/]+)", origin or "")
        if not m:
            return False  # includes "null" (sandboxed frames, file:// pages, data: URLs)
        auth = split_authority(m.group(2))
        if not auth or not self.host_ok(auth[0], testclient):
            return False
        port = auth[1] if auth[1] is not None else (443 if m.group(1) == "https" else 80)
        if testclient:
            return True
        # `tailscale serve` (docs/mobile-capture.md) puts https on 443 in front of the app for a non-loopback name
        if m.group(1) == "https" and port == 443 and auth[0] not in LOOPBACK:
            return True
        return port in {p for p in (server_port, self.port) if p}


def _json(status: int, detail: str, extra: list | None = None):
    body = json.dumps({"detail": detail}).encode()
    headers = [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode()),
               *(extra or [])]
    return status, headers, body


# ---------------------------------------------------------------- CSP for the app's HTML pages
_INLINE_SCRIPT_RE = re.compile(r"<script(?![^>]*\bsrc=)[^>]*>([\s\S]*?)</script>", re.I)
_csp_cache: dict = {}


def _static_dir() -> Path:
    try:
        from .paths import static_dir
        return static_dir()
    except Exception:
        return ROOT / "static"


def inline_script_hashes(static: Path | None = None) -> list[str]:
    """sha256 sources for every inline <script> in the app's HTML pages (recomputed when a page changes)."""
    static = static or _static_dir()
    files = sorted(static.glob("*.html"))
    key = tuple((str(p), p.stat().st_mtime_ns) for p in files if p.exists())
    if _csp_cache.get("key") == key:
        return _csp_cache["hashes"]
    hashes = []
    for p in files:
        try:
            src = p.read_text(encoding="utf-8")
        except OSError:
            continue
        for m in _INLINE_SCRIPT_RE.finditer(src):
            if m.group(0).lower().startswith('<script type="application/json"'):
                continue
            h = base64.b64encode(hashlib.sha256(m.group(1).encode("utf-8")).digest()).decode()
            hashes.append(f"'sha256-{h}'")
    _csp_cache.update(key=key, hashes=sorted(set(hashes)))
    return _csp_cache["hashes"]


def csp() -> str:
    scripts = " ".join(["'self'", "'wasm-unsafe-eval'", *inline_script_hashes()])
    return ("default-src 'self'; "
            f"script-src {scripts}; "
            "style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data: blob:; "
            "font-src 'self' data:; "
            "connect-src 'self'; "
            "worker-src 'self' blob:; "
            "frame-src 'self' blob:; "
            "object-src 'none'; base-uri 'none'; form-action 'self'; frame-ancestors 'self'")


COMMON_HEADERS = [
    (b"x-content-type-options", b"nosniff"),
    (b"referrer-policy", b"no-referrer"),
    (b"cross-origin-resource-policy", b"same-origin"),
    (b"x-frame-options", b"SAMEORIGIN"),
]


class _TooLarge(Exception):
    pass


class SecurityMiddleware:
    """Pure ASGI, so it also covers streaming responses and the static mount."""

    def __init__(self, app, policy: Policy | None = None):
        self.app = app
        self.policy = policy or Policy()

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        refusal = self.check(scope)
        if refusal:
            if scope["type"] == "websocket":
                return await send({"type": "websocket.close", "code": 1008})
            if len(refusal) == 3 and refusal[2] == "html":  # an unpaired device opening a page (app/pairing.py)
                from . import pairing
                body = pairing.unpaired_page()
                headers = [(b"content-type", b"text/html; charset=utf-8"), (b"content-length", str(len(body)).encode()),
                           (b"cache-control", b"no-store"), (b"content-security-policy", csp().encode())]
                await send({"type": "http.response.start", "status": refusal[0], "headers": headers + COMMON_HEADERS})
                await send({"type": "http.response.body", "body": body})
                return
            status, headers, body = _json(*refusal[:2])
            await send({"type": "http.response.start", "status": status, "headers": headers + COMMON_HEADERS})
            await send({"type": "http.response.body", "body": body})
            return
        if scope["type"] == "websocket":
            return await self.app(scope, receive, send)

        limit = self.policy.limit_for(scope.get("path", ""))
        seen = 0
        started = False

        async def counted_receive():
            nonlocal seen
            msg = await receive()
            if msg.get("type") == "http.request":
                seen += len(msg.get("body") or b"")
                if seen > limit:
                    raise _TooLarge()
            return msg

        async def secured_send(msg):
            nonlocal started
            if msg["type"] == "http.response.start":
                started = True
                headers = list(msg.get("headers") or [])
                names = {k.lower() for k, _ in headers}
                headers += [h for h in COMMON_HEADERS if h[0] not in names]
                ctype = next((v for k, v in headers if k.lower() == b"content-type"), b"")
                if ctype.startswith(b"text/html") and b"content-security-policy" not in names:
                    headers.append((b"content-security-policy", csp().encode()))
                msg = {**msg, "headers": headers}
            await send(msg)

        try:
            await self.app(scope, counted_receive, secured_send)
        except _TooLarge:
            if not started:
                status, headers, body = _json(413, f"Request body is larger than {limit // MB} MB")
                await send({"type": "http.response.start", "status": status, "headers": headers + COMMON_HEADERS})
                await send({"type": "http.response.body", "body": body})

    def check(self, scope) -> tuple | None:
        """(status, reason[, "html"|"json"]) to refuse the request, or None."""
        headers = {}
        for k, v in scope.get("headers") or []:
            headers.setdefault(k.decode("latin-1").lower(), v.decode("latin-1"))
        client = scope.get("client") or ("", 0)
        testclient = client[0] == "testclient"  # Starlette's in-process TestClient, never a real socket
        server = scope.get("server") or (None, None)
        auth = split_authority(headers.get("host", ""))
        if not auth or not self.policy.host_ok(auth[0], testclient):
            return 421, "This server only answers to 127.0.0.1 or localhost (wrong Host header)"
        origin = headers.get("origin")
        method = scope.get("method", "GET").upper()
        path = scope.get("path", "")
        # Device pairing (app/pairing.py): anything that isn't this computer needs a paired device's token.
        from . import pairing
        if not pairing.is_local(scope, headers):
            if not pairing.UNPAIRED_OK.match(path):
                if pairing.device_for(headers) is None:
                    page = (method in ("GET", "HEAD") and not path.startswith(("/api/", "/pdf/", "/attachments/"))
                            and headers.get("sec-fetch-dest", "document") in ("document", ""))
                    return (401, "Pair this device from your computer: Library settings, Phones and tablets.",
                            "html" if page else "json")
                if pairing.computer_only(path, method):
                    return 403, "Available on your computer: this changes the computer, not your library."
        if origin is not None:
            if not self.policy.origin_ok(origin, server[1], testclient):
                return 403, f"Origin {origin[:100]} not allowed"
        else:
            site = headers.get("sec-fetch-site", "")
            if site in ("cross-site", "same-site"):
                navigation = (method in ("GET", "HEAD") and headers.get("sec-fetch-mode") == "navigate"
                              and headers.get("sec-fetch-dest", "document") in ("document", ""))
                if not navigation:
                    return 403, "Cross-site requests are not allowed"
        if method not in SAFE_METHODS:
            clen = headers.get("content-length", "")
            has_body = (clen.strip() not in ("", "0")) or "chunked" in headers.get("transfer-encoding", "").lower()
            if has_body:
                ctype = headers.get("content-type", "").split(";")[0].strip().lower()
                upload = UPLOAD_PATHS.match(path)
                raw = RAW_BODY_PATHS.get(path)
                if raw:
                    if ctype not in raw[0]:
                        return 415, f"Send the body as {raw[0][0]}"
                elif not (ctype == "application/json" or ctype.endswith("+json")
                          or (upload and ctype == "multipart/form-data")):
                    return 415, ("Send JSON (Content-Type: application/json)" if not upload
                                 else "Upload the PDF as multipart/form-data")
                limit = self.policy.limit_for(path)
                if clen.strip().isdigit() and int(clen) > limit:
                    return 413, f"Request body is larger than {limit // MB} MB"
        return None


def install(app) -> None:
    """Add the middleware as the outermost layer (call after every other add_middleware)."""
    app.add_middleware(SecurityMiddleware)


def harden_process(vault: Path | None = None) -> None:
    """Private by default: files this process creates are 0600, folders 0700; the vault folder is 0700."""
    if os.name != "posix" or os.environ.get("RB_KEEP_UMASK") == "1":
        return
    os.umask(0o077)
    if vault is not None:
        try:
            v = Path(vault)
            if v.is_dir() and v.stat().st_uid == os.getuid():
                v.chmod(0o700)
        except OSError:
            pass

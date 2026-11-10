"""Download a PDF from a web URL for POST /api/import/url (the browser extension's "Annotate this PDF").

Runs on the user's own machine, but a local server that fetches arbitrary URLs is an SSRF risk
(a page could try to make it fetch http://192.168.1.1/ or a cloud metadata address), so:

- only http and https, no credentials in the URL, default or explicit web ports only;
- every hop (the first URL and each redirect) has its host resolved ONCE; the hop is refused when any
  address is not public (loopback, private, link-local, CGNAT, multicast, reserved, unspecified,
  IPv4-mapped versions of those); the connection then goes to one of exactly those checked addresses
  (pinned), with the URL's host name kept for the Host header, TLS SNI and certificate checks. So a
  DNS answer that changes between the check and the connect (DNS rebinding) can't redirect us;
- the connected socket's peer address is checked again after connecting;
- no proxies (a proxy would resolve the name itself, behind our check);
- at most MAX_REDIRECTS redirects, followed by hand so each target is checked;
- a connect timeout, a per-read timeout and a total time limit (TOTAL_TIMEOUT);
- `Accept-Encoding: identity` (nothing is decompressed, so there is no decompression bomb), and a
  compressed or non-PDF-looking Content-Type (text/*, image/*, ...) is refused;
- the body is streamed and cut off at max_bytes (Content-Length is checked first when present);
- the first bytes must contain the PDF magic "%PDF".

Tests replace `resolve` and `_open`, so no network is needed.
"""
from __future__ import annotations

import http.client
import ipaddress
import os
import socket
import ssl
import tempfile
import time
from pathlib import Path
from urllib.parse import urljoin, urlsplit

MAX_REDIRECTS = 3
MAX_MB = int(os.environ.get("RB_URL_IMPORT_MAX_MB", "100"))
TIMEOUT = (10, 60)  # connect, read (seconds)
TOTAL_TIMEOUT = float(os.environ.get("RB_URL_IMPORT_TIMEOUT", "180"))  # whole download
UA = "research_brain/1.0 (local PDF import)"


class FetchError(Exception):
    def __init__(self, message: str, status: int = 400):
        super().__init__(message)
        self.status = status


def resolve(host: str) -> list[str]:
    """All addresses `host` resolves to (replaced in tests)."""
    try:
        infos = socket.getaddrinfo(host, None, proto=socket.IPPROTO_TCP)
    except socket.gaierror as e:
        raise FetchError(f"Couldn't resolve {host}: {e}", 502)
    return sorted({i[4][0] for i in infos})


def _public(addr: str) -> bool:
    try:
        ip = ipaddress.ip_address(addr.split("%", 1)[0])
    except ValueError:
        return False
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return ip.is_global and not ip.is_multicast


def check_url(url: str) -> list[str]:
    """Raise FetchError unless `url` is an http(s) URL whose host resolves only to public addresses.
    Returns those addresses: connect to one of them, never resolve the name again."""
    try:
        u = urlsplit(url)
    except ValueError:
        raise FetchError("That isn't a valid URL")
    if u.scheme not in ("http", "https"):
        raise FetchError("Only http and https links can be imported")
    if not u.hostname:
        raise FetchError("That URL has no host")
    if u.username or u.password:
        raise FetchError("URLs with a username or password can't be imported")
    try:
        port = u.port
    except ValueError:
        raise FetchError("That URL has an invalid port")
    if port not in (None, 80, 443, 8080, 8443):
        raise FetchError("Only standard web ports (80, 443, 8080, 8443) are allowed")
    host = u.hostname
    try:  # a literal IP needs no lookup
        addrs = [str(ipaddress.ip_address(host))]
    except ValueError:
        if host == "localhost" or host.endswith((".localhost", ".local", ".internal", ".lan", ".home.arpa")):
            raise FetchError("Local and private network addresses can't be imported", 403)
        addrs = resolve(host)
    if not addrs or not all(_public(a) for a in addrs):
        raise FetchError("Local and private network addresses can't be imported", 403)
    return list(addrs)


def _bracket(addr: str) -> str:
    return f"[{addr}]" if ":" in addr else addr


def _tls_context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except Exception:
        return ssl.create_default_context()


class _PinnedHTTP(http.client.HTTPConnection):
    def __init__(self, addr: str, port: int):
        super().__init__(_bracket(addr), port, timeout=TIMEOUT[1])
        self._addr = addr

    def connect(self):
        self.sock = _connect(self._addr, self.port)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, addr: str, port: int, server_hostname: str):
        super().__init__(_bracket(addr), port, timeout=TIMEOUT[1], context=_tls_context())
        self._addr, self._sni = addr, server_hostname

    def connect(self):
        raw = _connect(self._addr, self.port)
        self.sock = self._context.wrap_socket(raw, server_hostname=self._sni)  # SNI + cert check for the name


def _connect(addr: str, port: int) -> socket.socket:
    sock = socket.create_connection((addr, port), timeout=TIMEOUT[0])
    peer = sock.getpeername()[0]
    if not _public(peer) or ipaddress.ip_address(peer.split("%", 1)[0]) != ipaddress.ip_address(addr.split("%", 1)[0]):
        sock.close()
        raise FetchError("Local and private network addresses can't be imported", 403)
    sock.settimeout(TIMEOUT[1])
    return sock


class _Response:
    """The few bits of a requests.Response that download_pdf uses."""

    def __init__(self, conn, resp):
        self._conn, self._resp = conn, resp
        self.status_code = resp.status
        self.headers = {k: v for k, v in resp.getheaders()}

    def iter_content(self, n: int):
        while True:
            chunk = self._resp.read(n)
            if not chunk:
                return
            yield chunk

    def close(self):
        try:
            self._resp.close()
        finally:
            self._conn.close()


def _open(url: str, addr: str | None = None):
    """One GET of `url`, connecting to the already-checked address `addr` (replaced in tests).
    No redirects are followed and no proxy is used."""
    u = urlsplit(url)
    host = u.hostname or ""
    if addr is None:  # only reached if a caller skipped check_url
        addr = check_url(url)[0]
    port = u.port or (443 if u.scheme == "https" else 80)
    conn = _PinnedHTTPS(addr, port, host) if u.scheme == "https" else _PinnedHTTP(addr, port)
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host_hdr = _bracket(host) + (f":{u.port}" if u.port else "")
    try:
        conn.putrequest("GET", path, skip_host=True, skip_accept_encoding=True)
        for k, v in (("Host", host_hdr), ("User-Agent", UA), ("Accept", "application/pdf,*/*;q=0.5"),
                     ("Accept-Encoding", "identity"), ("Connection", "close")):
            conn.putheader(k, v)
        conn.endheaders()
        return _Response(conn, conn.getresponse())
    except BaseException:
        conn.close()
        raise


def _header(r, name: str) -> str:
    for k, v in (getattr(r, "headers", None) or {}).items():
        if k.lower() == name:
            return str(v)
    return ""


_BAD_TYPES = ("text/", "image/", "audio/", "video/", "font/", "multipart/")


def _pick(addrs: list[str]) -> list[str]:
    """IPv4 first (more often reachable), then IPv6; all of them were checked."""
    return sorted(addrs, key=lambda a: (":" in a, a))


def download_pdf(url: str, max_bytes: int | None = None) -> tuple[Path, str]:
    """Download `url` to a temp file. Returns (path, final_url). Caller removes path.parent."""
    max_bytes = max_bytes if max_bytes is not None else MAX_MB << 20
    deadline = time.monotonic() + TOTAL_TIMEOUT
    cur = url
    for hop in range(MAX_REDIRECTS + 1):
        addrs = check_url(cur)
        r, last = None, None
        for addr in _pick(addrs):
            try:
                r = _open(cur, addr)
                break
            except FetchError:
                raise
            except Exception as e:  # ConnectionError, timeout, TLS failure: try the next checked address
                last = e
        if r is None:
            raise FetchError(f"Couldn't download the PDF: {last}", 502)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                loc = _header(r, "location")
                if not loc:
                    raise FetchError("The server sent a redirect without a location", 502)
                if hop == MAX_REDIRECTS:
                    raise FetchError(f"Too many redirects (more than {MAX_REDIRECTS})", 502)
                cur = urljoin(cur, loc)
                continue
            if r.status_code != 200:
                raise FetchError(f"The site answered HTTP {r.status_code}", 502)
            ctype = _header(r, "content-type").split(";")[0].strip().lower()
            if ctype.startswith(_BAD_TYPES):
                raise FetchError(f"That link isn't a PDF (the site says {ctype[:60]})", 415)
            enc = _header(r, "content-encoding").strip().lower()
            if enc and enc != "identity":
                raise FetchError("The site sent a compressed response; only plain PDFs can be imported", 415)
            clen = _header(r, "content-length")
            if clen and clen.isdigit() and int(clen) > max_bytes:
                raise FetchError(f"The PDF is larger than {max_bytes >> 20} MB", 413)
            tmpdir = Path(tempfile.mkdtemp(prefix="rb_urlpdf_"))
            dest = tmpdir / "download.pdf"
            size, head = 0, b""
            try:
                with dest.open("wb") as f:
                    for chunk in r.iter_content(1 << 16):
                        if time.monotonic() > deadline:
                            raise FetchError(f"The download took longer than {int(TOTAL_TIMEOUT)} s", 504)
                        if not chunk:
                            continue
                        size += len(chunk)
                        if size > max_bytes:
                            raise FetchError(f"The PDF is larger than {max_bytes >> 20} MB", 413)
                        if len(head) < 1024:
                            head += chunk[:1024 - len(head)]
                            if len(head) >= 1024 and b"%PDF" not in head:
                                raise FetchError("That link isn't a PDF", 415)
                        f.write(chunk)
                if b"%PDF" not in head:
                    raise FetchError("That link isn't a PDF", 415)
            except BaseException as e:
                import shutil
                shutil.rmtree(tmpdir, ignore_errors=True)
                if isinstance(e, (socket.timeout, TimeoutError)):
                    raise FetchError("The site stopped sending data", 504)
                if isinstance(e, (OSError, http.client.HTTPException)) and not isinstance(e, FetchError):
                    raise FetchError(f"Couldn't download the PDF: {e}", 502)
                raise
            return dest, cur
        finally:
            try:
                r.close()
            except Exception:
                pass
    raise FetchError(f"Too many redirects (more than {MAX_REDIRECTS})", 502)

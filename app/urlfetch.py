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

fetch() is the same guard for small metadata API requests (app/metadata.py): https only, a fixed list of
allowed host names checked on every hop, pinned public addresses, a byte and time cap. fetch_local() is
the one way to reach a helper on this computer (Zotero's translation-server): 127.0.0.1 and a port only.

Tests replace `resolve`, `_open`, `_request` and `_request_local`, so no network is needed.
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
    def __init__(self, addr: str, port: int, timeout: tuple = TIMEOUT):
        super().__init__(_bracket(addr), port, timeout=timeout[1])
        self._addr, self._timeouts = addr, timeout

    def connect(self):
        self.sock = _connect(self._addr, self.port, self._timeouts)


class _PinnedHTTPS(http.client.HTTPSConnection):
    def __init__(self, addr: str, port: int, server_hostname: str, timeout: tuple = TIMEOUT):
        super().__init__(_bracket(addr), port, timeout=timeout[1], context=_tls_context())
        self._addr, self._sni, self._timeouts = addr, server_hostname, timeout

    def connect(self):
        raw = _connect(self._addr, self.port, self._timeouts)
        self.sock = self._context.wrap_socket(raw, server_hostname=self._sni)  # SNI + cert check for the name


def _connect(addr: str, port: int, timeout: tuple = TIMEOUT) -> socket.socket:
    sock = socket.create_connection((addr, port), timeout=timeout[0])
    peer = sock.getpeername()[0]
    if not _public(peer) or ipaddress.ip_address(peer.split("%", 1)[0]) != ipaddress.ip_address(addr.split("%", 1)[0]):
        sock.close()
        raise FetchError("Local and private network addresses can't be imported", 403)
    sock.settimeout(timeout[1])
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


# ---------------------------------------------------------------- small API requests (app/metadata.py)
# The same guard for JSON/XML metadata lookups: the request may only go to `allowed_hosts` (exact host
# names; every redirect hop is checked against the list too), the host must resolve to public addresses
# only (pinned connection, as above), nothing is decompressed, the body is capped at `max_bytes` and the
# whole exchange at `total_timeout` seconds. Non-2xx answers are returned, not raised, so a caller can
# tell "not found" (404) from "unreachable" (FetchError).
API_TIMEOUT = (6, 15)


class Fetched:
    def __init__(self, status: int, headers: dict, body: bytes, url: str):
        self.status, self.headers, self.body, self.url = status, headers, body, url

    def header(self, name: str) -> str:
        return self.headers.get(name.lower(), "")


def _request(url: str, addr: str, method: str, headers: dict, body: bytes | None, timeout: tuple):
    """One request to the already-checked address `addr` (replaced in tests). Returns _Response."""
    u = urlsplit(url)
    host = u.hostname or ""
    port = u.port or (443 if u.scheme == "https" else 80)
    conn = (_PinnedHTTPS(addr, port, host, timeout) if u.scheme == "https" else _PinnedHTTP(addr, port, timeout))
    path = (u.path or "/") + (f"?{u.query}" if u.query else "")
    host_hdr = _bracket(host) + (f":{u.port}" if u.port else "")
    try:
        conn.putrequest(method, path, skip_host=True, skip_accept_encoding=True)
        hdrs = {"Host": host_hdr, "User-Agent": UA, "Accept": "*/*", **headers,
                "Accept-Encoding": "identity", "Connection": "close"}
        if body is not None:
            hdrs["Content-Length"] = str(len(body))
        for k, v in hdrs.items():
            conn.putheader(k, v)
        conn.endheaders(body)
        return _Response(conn, conn.getresponse())
    except BaseException:
        conn.close()
        raise


def _read_capped(r, max_bytes: int, deadline: float) -> bytes:
    enc = _header(r, "content-encoding").strip().lower()
    if enc and enc != "identity":
        raise FetchError("The server sent a compressed response", 415)
    clen = _header(r, "content-length")
    if clen and clen.isdigit() and int(clen) > max_bytes:
        raise FetchError(f"The response is larger than {max_bytes} bytes", 413)
    out, size = [], 0
    for chunk in r.iter_content(1 << 15):
        if time.monotonic() > deadline:
            raise FetchError("The request took too long", 504)
        size += len(chunk)
        if size > max_bytes:
            raise FetchError(f"The response is larger than {max_bytes} bytes", 413)
        out.append(chunk)
    return b"".join(out)


def fetch(url: str, *, allowed_hosts, accept: str = "application/json", headers: dict | None = None,
          method: str = "GET", data: bytes | None = None, max_bytes: int = 4 << 20,
          total_timeout: float = 25.0, timeout: tuple = API_TIMEOUT, max_redirects: int = MAX_REDIRECTS) -> Fetched:
    """A small GET (or POST) to a fixed set of public API hosts. Raises FetchError when the URL, a redirect
    target or an address isn't allowed, or the server can't be reached; returns the answer otherwise."""
    allowed = {h.lower() for h in allowed_hosts}
    deadline = time.monotonic() + total_timeout
    cur, meth, body = url, method, data
    hdrs = {"Accept": accept, **(headers or {})}
    for hop in range(max_redirects + 1):
        host = (urlsplit(cur).hostname or "").lower()
        if host not in allowed:
            raise FetchError(f"{host or 'That address'} isn't one of the hosts this lookup may contact", 403)
        if urlsplit(cur).scheme != "https":
            raise FetchError("Metadata lookups use https only", 403)
        addrs = check_url(cur)
        r, last = None, None
        for addr in _pick(addrs):
            if time.monotonic() > deadline:
                break
            try:
                r = _request(cur, addr, meth, hdrs, body, timeout)
                break
            except FetchError:
                raise
            except Exception as e:
                last = e
        if r is None:
            raise FetchError(f"Couldn't reach {host}: {last or 'timed out'}", 502)
        try:
            if r.status_code in (301, 302, 303, 307, 308):
                loc = _header(r, "location")
                if not loc:
                    raise FetchError("The server sent a redirect without a location", 502)
                if hop == max_redirects:
                    raise FetchError(f"Too many redirects (more than {max_redirects})", 502)
                cur = urljoin(cur, loc)
                if r.status_code == 303 or (r.status_code in (301, 302) and meth == "POST"):
                    meth, body = "GET", None
                continue
            try:
                payload = _read_capped(r, max_bytes, deadline)
            except (socket.timeout, TimeoutError):
                raise FetchError(f"{host} stopped sending data", 504)
            except (OSError, http.client.HTTPException) as e:
                if isinstance(e, FetchError):
                    raise
                raise FetchError(f"Couldn't read the answer from {host}: {e}", 502)
            return Fetched(r.status_code, {k.lower(): str(v) for k, v in (r.headers or {}).items()}, payload, cur)
        finally:
            try:
                r.close()
            except Exception:
                pass
    raise FetchError(f"Too many redirects (more than {max_redirects})", 502)


LOOPBACK_HOSTS = ("127.0.0.1", "localhost", "::1", "[::1]")


def _request_local(port: int, path: str, method: str, headers: dict, body: bytes | None, timeout: tuple):
    """One request to 127.0.0.1:<port> (replaced in tests)."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=timeout[1])
    try:
        conn.request(method, path, body=body, headers={"User-Agent": UA, "Accept-Encoding": "identity",
                                                       "Connection": "close", **headers})
        return _Response(conn, conn.getresponse())
    except BaseException:
        conn.close()
        raise


def fetch_local(port: int, path: str, *, method: str = "GET", data: bytes | None = None, headers: dict | None = None,
                max_bytes: int = 4 << 20, total_timeout: float = 20.0, timeout: tuple = (0.5, 15)) -> Fetched:
    """A request to a helper service on THIS computer (Zotero's translation-server): always 127.0.0.1, a
    port the user configured, no redirects followed. Nothing else is reachable through it."""
    if not isinstance(port, int) or not (1 <= port <= 65535):
        raise FetchError("Invalid local port", 400)
    if not path.startswith("/") or "//" in path or "@" in path:
        raise FetchError("Invalid local path", 400)
    deadline = time.monotonic() + total_timeout
    try:
        r = _request_local(port, path, method, headers or {}, data, timeout)
    except FetchError:
        raise
    except Exception as e:
        raise FetchError(f"Nothing answered on 127.0.0.1:{port} ({e.__class__.__name__})", 502)
    try:
        payload = _read_capped(r, max_bytes, deadline)
        return Fetched(r.status_code, {k.lower(): str(v) for k, v in (r.headers or {}).items()}, payload,
                       f"http://127.0.0.1:{port}{path}")
    except (socket.timeout, TimeoutError):
        raise FetchError(f"127.0.0.1:{port} stopped sending data", 504)
    finally:
        try:
            r.close()
        except Exception:
            pass

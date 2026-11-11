"""Phones and tablets: device pairing endpoints, the installable web app (manifest, service worker, share
target) and photo capture. The rules live in app/pairing.py; app/security.py enforces them on every request.

    GET    /api/device               {local, device: {id, name} | null}: is this this computer, or which paired device
    POST   /api/pair/start           a one-time pairing code and the URLs a phone can open (computer only)
    DELETE /api/pair/codes           cancel unused codes (computer only)
    POST   /api/pair/claim           {code, name} -> sets the device cookie (the one API an unpaired device may call)
    POST   /api/device/forget        a paired device unpairs itself
    GET    /api/devices              paired devices (computer only)
    POST   /api/devices/token        {name} -> a token for the iOS Shortcut, shown once (computer only)
    PATCH  /api/devices/{id}         {name} (computer only)
    DELETE /api/devices/{id}         revoke (computer only)
    POST   /api/capture/photo?title=  raw image body -> a new note showing the photo (attachment)
    GET    /attachments/{name}       an attached photo
    GET    /pair, /share, /manifest.webmanifest, /sw.js
"""
from __future__ import annotations

import os
import re
import secrets
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, Response
from pydantic import BaseModel

from . import pairing, security

router = APIRouter()


class _Ctx:
    store = None
    static: Path | None = None
    port: int = 8765
    publish = None


ctx = _Ctx()

IMAGE_MAGIC = ((b"\xff\xd8\xff", "jpg"), (b"\x89PNG\r\n\x1a\n", "png"), (b"RIFF", "webp"))
ATTACH_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,80}\.(jpg|png|webp)$")
MAX_PHOTO = int(float(os.environ.get("RB_MAX_PHOTO_MB", "25")) * (1 << 20))


def install(app, *, store, static: Path, port: int, publish=None) -> None:
    ctx.store, ctx.static, ctx.port, ctx.publish = store, Path(static), port, publish
    app.include_router(router)


def _headers(request: Request) -> dict:
    return {k.lower(): v for k, v in request.headers.items()}


def _local(request: Request) -> bool:
    return pairing.is_local(request.scope, _headers(request))


def _need_local(request: Request) -> None:
    if not _local(request):
        raise HTTPException(403, "Available on your computer.")


# ---------------------------------------------------------------- who am I
@router.get("/api/device")
def api_device(request: Request):
    if _local(request):
        return {"local": True, "device": None}
    d = pairing.device_for(_headers(request))
    return {"local": False, "device": {"id": d["id"], "name": d["name"]} if d else None}


# ---------------------------------------------------------------- pairing (computer side)
def _pair_urls(request: Request) -> list[dict]:
    """Addresses a phone could open, from RB_HOST, RB_ALLOWED_HOSTS and the page's own Host."""
    pol = security.Policy()
    seen, out = set(), []

    def add(url, label):
        if url not in seen:
            seen.add(url)
            out.append({"url": url, "label": label})
    names = sorted(h for h in pol.hosts if h not in security.LOOPBACK)
    for h in names:
        host = f"[{h}]" if ":" in h else h
        if h.endswith(".ts.net"):
            add(f"https://{host}", "Tailscale, https (tailscale serve)")
        add(f"http://{host}:{ctx.port}", "Tailscale" if h.startswith("100.") or h.endswith(".ts.net") else "Network")
    return out


@router.post("/api/pair/start")
def api_pair_start(request: Request):
    _need_local(request)
    code, ttl = pairing.new_code()
    urls = _pair_urls(request)
    return {"code": code, "expires_in": ttl,
            "urls": [{**u, "pair_url": f"{u['url']}/pair?code={code}"} for u in urls]}


@router.delete("/api/pair/codes")
def api_pair_cancel(request: Request):
    _need_local(request)
    pairing.cancel_codes()
    return {"ok": True}


@router.get("/api/devices")
def api_devices(request: Request):
    _need_local(request)
    return {"devices": pairing.list_devices()}


class DeviceName(BaseModel):
    name: str = ""


@router.post("/api/devices/token", status_code=201)
def api_device_token(body: DeviceName, request: Request):
    """A token for the iOS Shortcut (sent as "Authorization: Bearer <token>"), shown once (computer only)."""
    _need_local(request)
    dev, token = pairing.issue_token(body.name)
    return {"device": dev, "token": token}


@router.patch("/api/devices/{did}")
def api_device_rename(did: str, body: DeviceName, request: Request):
    _need_local(request)
    d = pairing.rename(did, body.name)
    if not d:
        raise HTTPException(404, "No such device")
    return d


@router.delete("/api/devices/{did}")
def api_device_revoke(did: str, request: Request):
    _need_local(request)
    if not pairing.revoke(did):
        raise HTTPException(404, "No such device")
    return {"ok": True}


# ---------------------------------------------------------------- pairing (phone side)
class ClaimIn(BaseModel):
    code: str
    name: str = ""


def _secure(request: Request) -> bool:
    return request.url.scheme == "https" or request.headers.get("x-forwarded-proto", "") == "https"


@router.post("/api/pair/claim")
def api_pair_claim(body: ClaimIn, request: Request):
    client = (request.client.host if request.client else "") or "?"
    try:
        dev, token = pairing.claim(body.code[:100], body.name, request.headers.get("user-agent", ""), client)
    except pairing.PairError as e:
        raise HTTPException(e.status, str(e))
    r = JSONResponse({"device": dev}, status_code=201)
    r.set_cookie(pairing.COOKIE, token, max_age=400 * 86400, httponly=True, samesite="strict",
                 secure=_secure(request), path="/")
    return r


@router.post("/api/device/forget")
def api_device_forget(request: Request):
    d = None if _local(request) else pairing.device_for(_headers(request))
    if d:
        pairing.revoke(d["id"])
    r = JSONResponse({"ok": True})
    r.delete_cookie(pairing.COOKIE, path="/")
    return r


@router.get("/pair")
def pair_page():
    return Response(pairing.pair_page(), media_type="text/html; charset=utf-8", headers={"Cache-Control": "no-store"})


# ---------------------------------------------------------------- installable web app
@router.get("/share")
def share_target():
    """The Web Share Target: the phone's share sheet opens /share?title=&text=&url=; the app reads the query."""
    return FileResponse(ctx.static / "index.html", headers={"Cache-Control": "no-store"})


@router.get("/manifest.webmanifest")
def manifest():
    return FileResponse(ctx.static / "manifest.webmanifest", media_type="application/manifest+json")


@router.get("/sw.js")
def service_worker():
    # served from the root so it may control "/", never cached by the HTTP cache so updates arrive
    return FileResponse(ctx.static / "sw.js", media_type="text/javascript",
                        headers={"Cache-Control": "no-cache", "Service-Worker-Allowed": "/"})


# ---------------------------------------------------------------- photo capture
def _attach_dir() -> Path:
    d = Path(ctx.store.root) / "attachments"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", (s or "").lower()).strip("-")[:40] or "photo"


@router.post("/api/capture/photo", status_code=201)
async def api_capture_photo(request: Request, title: str = "", note: str = ""):
    data = await request.body()
    if not data:
        raise HTTPException(400, "No photo in the request")
    if len(data) > MAX_PHOTO:
        raise HTTPException(413, "The photo is too large")
    ext = next((e for magic, e in IMAGE_MAGIC if data.startswith(magic)), None)
    if ext == "webp" and data[8:12] != b"WEBP":
        ext = None
    if not ext:
        raise HTTPException(415, "Send a JPEG, PNG or WebP photo")
    title = re.sub(r"[\x00-\x1f\x7f]", " ", title or "").strip()[:200] or "Photo"
    name = f"{_slug(title)}-{secrets.token_hex(4)}.{ext}"
    path = _attach_dir() / name
    path.write_bytes(data)
    body = f"![{title.replace(']', ')')}](/attachments/{name})"
    extra = re.sub(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]", "", note or "").strip()[:4000]
    if extra:
        body = extra + "\n\n" + body
    n = ctx.store.create({"type": "note", "title": title, "body": body, "tags": ["photo"]})
    if ctx.publish:
        try:
            ctx.publish({"type": "notes-changed", "ids": [n["id"]]})
        except Exception:
            pass
    return {"id": n["id"], "title": n["title"], "attachment": f"/attachments/{name}"}


@router.get("/attachments/{name}")
def get_attachment(name: str):
    if not ATTACH_RE.match(name):
        raise HTTPException(404, "Not found")
    p = _attach_dir() / name
    if not p.is_file():
        raise HTTPException(404, "Not found")
    return FileResponse(p, headers={"Cache-Control": "private, max-age=3600"})

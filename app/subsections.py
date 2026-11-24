"""Subsections: named groups of papers inside a region, kept only in the app.

They live in <library>/state/subsections.json, never as files or folders in your documents, and they don't bind
papers to anything: a subsection is a name and a set of papers.

    [{"id": "s-1a2b3c4d", "name": "Line ratios", "members": ["note-id", ...], "at": 1764000000.0}]

On the map (app/server.py `_subclusters`) a subsection belongs to the region that holds most of its papers, and its
papers there are laid out together inside the region, in place of the automatic sub-groups. A paper of it that the
map puts in another region is simply not drawn in it. A paper in two subsections of one region is drawn in the
smaller one (the more specific). The names show only while that region is focused (static/app.js).

Your AI agent can make and change them from the chat (app/region_edit.py, the "subsections" changes).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from pathlib import Path

from . import fsio

FILE = "subsections.json"
MAX_NAME = 80
MAX_SUBS = 2000
_lock = threading.RLock()


class SubsectionError(ValueError):
    pass


def path(store) -> Path:
    d = Path(store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / FILE


def clean_name(name) -> str:
    return " ".join(str(name or "").split())[:MAX_NAME]


def load(store) -> list[dict]:
    try:
        raw = json.loads(path(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out, seen = [], set()
    for r in raw if isinstance(raw, list) else []:
        if not isinstance(r, dict):
            continue
        sid, name = str(r.get("id") or ""), clean_name(r.get("name"))
        if not sid or not name or sid in seen or not isinstance(r.get("members"), list):
            continue
        seen.add(sid)
        mem = list(dict.fromkeys(str(m) for m in r["members"] if m))
        out.append({"id": sid, "name": name, "members": mem, "at": r.get("at") or 0})
    return out


def save(store, items: list[dict]) -> None:
    p = path(store)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items[:MAX_SUBS], ensure_ascii=False, indent=1), encoding="utf-8")
    fsio.replace(tmp, p)


def signature(store) -> int:
    """Changes when any subsection changes (the map's regions are cached on it)."""
    try:
        p = path(store)
        return p.stat().st_mtime_ns if p.exists() else 0
    except OSError:
        return 0


def _valid(store, ids) -> list[str]:
    ok = []
    for x in ids or []:
        nid = str(x).strip().strip("[]")
        try:
            n = store.get(nid)
        except (KeyError, ValueError):
            continue
        if n.get("type") != "fragment" and nid not in ok:
            ok.append(nid)
    return ok


def _find(items: list[dict], sid: str) -> dict:
    for r in items:
        if r["id"] == sid:
            return r
    raise KeyError(sid)


def new_id() -> str:
    return "s-" + uuid.uuid4().hex[:8]


def create(store, name: str, ids=None) -> dict:
    name = clean_name(name)
    if not name:
        raise SubsectionError("Give the subsection a name")
    with _lock:
        items = load(store)
        r = {"id": new_id(), "name": name, "members": _valid(store, ids), "at": time.time()}
        items.append(r)
        save(store, items)
    return r


def rename(store, sid: str, name: str) -> dict:
    name = clean_name(name)
    if not name:
        raise SubsectionError("Give the subsection a name")
    with _lock:
        items = load(store)
        r = _find(items, sid)
        r["name"] = name
        save(store, items)
    return r


def delete(store, sid: str) -> None:
    with _lock:
        items = load(store)
        _find(items, sid)
        save(store, [r for r in items if r["id"] != sid])


def add(store, sid: str, ids) -> dict:
    with _lock:
        items = load(store)
        r = _find(items, sid)
        r["members"] = list(dict.fromkeys(r["members"] + _valid(store, ids)))
        save(store, items)
    return r


def remove(store, sid: str, ids) -> dict:
    gone = {str(i) for i in ids or []}
    with _lock:
        items = load(store)
        r = _find(items, sid)
        r["members"] = [m for m in r["members"] if m not in gone]
        save(store, items)
    return r


def homes(regions: list[list[str]], subs: list[dict]) -> dict[int, list[tuple[dict, list[str]]]]:
    """Each region (index into `regions`, lists of paper ids) -> its subsections, with the papers of each that are in
    that region. A subsection belongs to the region holding most of its papers (the bigger region on a tie)."""
    region_of = {m: cid for cid, mem in enumerate(regions) for m in mem}
    out: dict[int, list[tuple[dict, list[str]]]] = {}
    for s in subs:
        votes: dict[int, int] = {}
        for m in s["members"]:
            if m in region_of:
                votes[region_of[m]] = votes.get(region_of[m], 0) + 1
        if not votes:
            continue
        cid = max(sorted(votes), key=lambda k: (votes[k], len(regions[k])))
        out.setdefault(cid, []).append((s, [m for m in s["members"] if region_of.get(m) == cid]))
    return out

"""Which region a new paper belongs in.

A paper you add goes into the region (a topic) it clearly belongs to; when it could go in one of a few, you're asked
which; when it's like none of them, it's left where the map puts it. Only papers added since this was first used are
looked at, once each (state/placed.json), so your library is never sorted again behind your back.

How close a paper is to a region: the mean similarity to the region's three papers most like it. That number means
different things with different embeddings, so it's measured against the region itself: 1 is as close as the
region's own papers are to each other (their median, each left out in turn), 0 as close as papers outside it are.
"""
from __future__ import annotations

import datetime as dt
import json
import threading
import time
from pathlib import Path

import numpy as np

from . import fsio

AUTO = 1.0      # as close as a typical member ...
MARGIN = 0.5    # ... and clearly closer than to any other region: it's put there
ASK = 0.4       # this close to one or more: you're asked
TOP = 3
CENTRE = False  # taking the library's average out: worse in a small library (a paper about two regions' subjects
                # looked like neither), and no better in a bigger one
WAIT_S = 600    # a paper with only a title is looked at once its abstract or text is in, or after this long
_lock = threading.Lock()


def _file(store) -> Path:
    d = Path(store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "placed.json"


def _load(store) -> dict | None:
    try:
        raw = json.loads(_file(store).read_text(encoding="utf-8"))
        return raw if isinstance(raw, dict) and isinstance(raw.get("seen"), list) else None
    except (OSError, ValueError):
        return None


def _save(store, data: dict) -> None:
    p = _file(store)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    fsio.replace(tmp, p)


def _close(M: np.ndarray, rows: list[int], v: np.ndarray) -> float:
    s = M[rows] @ v
    k = min(TOP, len(s))
    return float(np.sort(s)[-k:].mean())


def scores(idx: dict, topics: list[dict], nid: str) -> list[dict]:
    """[{slug, name, score, z}] for each region of 2 or more papers, closest first (z: see the module's doc)."""
    m, ids = idx.get("m"), idx.get("ids") or []
    if m is None or nid not in ids:
        return []
    pos = {i: k for k, i in enumerate(ids)}
    by_id = idx.get("by_id") or {}
    M = m - m.mean(axis=0, keepdims=True) if CENTRE else m
    M = M / np.maximum(np.linalg.norm(M, axis=1, keepdims=True), 1e-9)
    papers = [i for i in ids if i != nid and (by_id.get(i) or {}).get("type") in ("paper", "note")]
    v = M[pos[nid]]
    out = []
    for t in topics:
        mem = [x for x in t.get("members") or [] if x in pos and x != nid]
        if len(mem) < 2:
            continue
        rows = [pos[x] for x in mem]
        own = float(np.median([_close(M, [r for r in rows if r != pos[x]], M[pos[x]]) for x in mem]))
        inside = set(mem)
        outs = [i for i in papers if i not in inside][:400]
        # (never below 0, nothing in common: in a small library the papers outside it can be only another region's,
        # so unlike it that a paper about something else entirely would seem close)
        away = max(0.0, float(np.median([_close(M, rows, M[pos[i]]) for i in outs])) if outs else 0.0)
        s = _close(M, rows, v)
        z = (s - away) / max(own - away, 1e-3)
        out.append({"slug": t["slug"], "name": t["name"], "score": round(s, 4), "z": round(z, 3)})
    out.sort(key=lambda r: -r["z"])
    return out


def decide(ranked: list[dict]) -> tuple[dict | None, list[dict]]:
    """(the region to put it in, or None; the regions to offer when it isn't clear)"""
    if not ranked or ranked[0]["z"] < ASK:
        return None, []
    second = ranked[1]["z"] if len(ranked) > 1 else -1.0
    if ranked[0]["z"] >= AUTO and ranked[0]["z"] - second >= MARGIN:
        return ranked[0], []
    return None, [r for r in ranked[:3] if r["z"] >= ASK]


def _ready(n: dict) -> bool:
    if n.get("abstract") or n.get("has_text"):
        return True
    try:
        made = dt.datetime.fromisoformat(str(n.get("created") or "")).timestamp()
    except ValueError:
        return True
    return time.time() - made > WAIT_S


def place_new(store, idx: dict, topics: list[dict], add) -> dict:
    """Look at the papers added since the last look (each once). add(slug, [ids]) files them.
    Returns {"placed": [{id, title, slug, name}], "ask": [{id, title, options: [{slug, name}]}]}."""
    with _lock:
        by_id = idx.get("by_id") or {}
        papers = {i for i, n in by_id.items() if n.get("type") == "paper"}
        data = _load(store)
        if data is None:  # the first time: everything already here counts as looked at
            _save(store, {"seen": sorted(papers), "since": time.time()})
            return {"placed": [], "ask": []}
        seen = set(data["seen"])
        new = [i for i in sorted(papers - seen) if _ready(by_id[i])]
        if not new:
            return {"placed": [], "ask": []}
        member = {m for t in topics for m in t.get("members") or []}
        placed, ask = [], []
        for nid in new:
            if nid in member:  # it came with a region already (a folder of papers, Find papers for a region)
                continue
            go, options = decide(scores(idx, topics, nid))
            title = (by_id[nid].get("title") or "")[:160]
            if go:
                add(go["slug"], [nid])
                placed.append({"id": nid, "title": title, "slug": go["slug"], "name": go["name"]})
            elif options:
                ask.append({"id": nid, "title": title, "options": [{"slug": o["slug"], "name": o["name"]} for o in options]})
        data["seen"] = sorted(seen | set(new))
        _save(store, data)
        return {"placed": placed, "ask": ask}

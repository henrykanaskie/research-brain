"""Region names on the graph.

Regions (app/server.py `_clusters`) are found again every time the library changes, so their ids aren't
stable. A region is recognised by its members instead: the set of papers and notes in it.

A region's name, in order:
  1. a name you gave it (kept in <library>/state/region_names.json, matched by members). Yours always wins,
     and it follows the region as papers join or leave it: a region holding at least half of the papers you
     named (or half of its papers being them) keeps the name, and the stored members move with it;
  2. the name of the topic with the same papers (Jaccard overlap >= MATCH), so a region and its topic read
     the same;
  3. the research area your AI agent named it (state/region_ai_names.json; app/server.py names regions in the
     background from paper titles, never private ones), e.g. "Collisional-Radiative Models";
  4. its automatic name (its most distinctive phrase, from the titles on this computer).

Renaming a region that has a matching topic renames the topic too, unless you ask to keep them different.
Either way the region keeps the name as its own, so nothing automatic can replace it.
"""
from __future__ import annotations

from . import fsio

import json
import threading
import time
from pathlib import Path

MATCH = 0.5  # Jaccard overlap for "the same group of papers"
MAX_NAME = 80
_lock = threading.Lock()


def _file(store, name: str = "region_names.json") -> Path:
    d = Path(store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / name


AI_FILE = "region_ai_names.json"


def _load(store, name: str = "region_names.json") -> list[dict]:
    try:
        raw = json.loads(_file(store, name).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, dict) and isinstance(r.get("members"), list) and str(r.get("name") or "").strip():
            out.append({"members": [str(m) for m in r["members"]], "name": str(r["name"])[:MAX_NAME],
                        "at": r.get("at") or 0})
    return out


def _save(store, items: list[dict], name: str = "region_names.json") -> None:
    p = _file(store, name)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    fsio.replace(tmp, p)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / max(1, len(a | b))


def _held(a: set, b: set) -> float:
    """How much of the smaller group the other holds (0 unless they share two papers, or one of a pair)."""
    shared = len(a & b)
    if not shared or shared < min(2, len(a), len(b)):
        return 0.0
    return shared / max(1, min(len(a), len(b)))


def signature(store) -> tuple:
    """Changes when a region name or any topic changes (the graph payload is cached on it)."""
    from . import topics as T
    own = []
    for name in ("region_names.json", AI_FILE):
        try:
            f = _file(store, name)
            own.append(f.stat().st_mtime_ns if f.exists() else 0)
        except OSError:
            own.append(0)
    own = tuple(own)
    try:
        tops = [(p.name, p.stat().st_mtime_ns) for p in T.topics_dir(store).glob("*.md")]
    except OSError:
        tops = []
    return (own, tuple(sorted(tops)))


def _topics(store) -> list[dict]:
    from . import topics as T
    out = []
    for slug in T._all_slugs(store):
        try:
            t = T._read(store, slug)
        except (KeyError, OSError, UnicodeDecodeError):
            continue
        out.append({"slug": slug, "name": t["name"], "members": set(t["members"])})
    return out


def _best(groups: dict[int, set], cands: list[set], measure=_jaccard) -> dict[int, int]:
    """Pair regions with candidates one to one, best overlap first, at least MATCH."""
    pairs = sorted(((measure(m, c), cid, k) for cid, m in groups.items() for k, c in enumerate(cands)),
                   reverse=True)
    used_r, used_c, out = set(), set(), {}
    for s, cid, k in pairs:
        if s < MATCH:
            break
        if cid in used_r or k in used_c:
            continue
        used_r.add(cid)
        used_c.add(k)
        out[cid] = k
    return out


def apply(store, clusters: list[dict], members: dict[int, list[str]]) -> list[dict]:
    """New cluster dicts with their names: label (what to show), auto_label, name_source
    ("custom" | "topic" | "ai" | "auto"), and topic {slug, name} when a topic has the same papers."""
    groups = {c["id"]: set(members.get(c["id"], [])) for c in clusters}
    with _lock:
        custom = _load(store)
        by_custom = _best(groups, [set(r["members"]) for r in custom], _held)
        # your names follow their regions: the stored members become the region's current ones
        moved = False
        for cid, k in by_custom.items():
            now = sorted(groups[cid])
            if now and sorted(custom[k]["members"]) != now:
                custom[k]["members"], moved = now, True
        if moved:
            _save(store, custom)
    topics = _topics(store)
    by_topic = _best(groups, [t["members"] for t in topics])
    ai = _load(store, AI_FILE)
    by_ai = _best(groups, [set(r["members"]) for r in ai])
    out = []
    for c in clusters:
        d = dict(c)
        d["auto_label"] = c.get("label") or ""
        t = topics[by_topic[c["id"]]] if c["id"] in by_topic else None
        d["topic"] = {"slug": t["slug"], "name": t["name"]} if t else None
        if c["id"] in by_custom:
            d["label"], d["name_source"] = custom[by_custom[c["id"]]]["name"], "custom"
        elif t:
            d["label"], d["name_source"] = t["name"], "topic"
        elif c["id"] in by_ai:
            d["label"], d["name_source"] = ai[by_ai[c["id"]]]["name"], "ai"
        else:
            d["name_source"] = "auto"
        out.append(d)
    return out


def save_ai_names(store, named: list[tuple[list[str], str]]) -> None:
    """Keep the research-area names the AI agent gave regions (never touching yours)."""
    with _lock:
        items = _load(store, AI_FILE)
        for mem, name in named:
            name = " ".join((name or "").split())[:MAX_NAME]
            if not name or not mem:
                continue
            ms = set(mem)
            items = [r for r in items if _jaccard(set(r["members"]), ms) < MATCH]
            items.append({"members": sorted(ms), "name": name, "at": time.time()})
        _save(store, items[-300:], AI_FILE)


class RegionError(ValueError):
    pass


def rename(store, region_members: list[str], name: str, topic_slug: str | None, rename_topic: bool) -> dict:
    """Name a region. With rename_topic and a matching topic, the topic takes the name and the region simply
    follows it (no separate name is kept). Otherwise the region keeps this name of its own."""
    from . import topics as T
    name = " ".join((name or "").split())[:MAX_NAME]
    if not name:
        raise RegionError("Give the region a name")
    mem = set(region_members)
    if not mem:
        raise RegionError("That region is empty")
    with _lock:
        # the region keeps the name as its own either way, so no topic, AI or automatic name can replace it
        items = [r for r in _load(store) if _held(set(r["members"]), mem) < MATCH]
        if rename_topic and topic_slug:
            try:
                T.update_topic(store, topic_slug, name=name)
            except KeyError:
                raise RegionError("That topic no longer exists")
            except T.TopicError as e:
                raise RegionError(str(e))
        items.append({"members": sorted(mem), "name": name, "at": time.time()})
        _save(store, items)
    return {"name": name, "renamed_topic": bool(rename_topic and topic_slug)}


def reset(store, region_members: list[str]) -> None:
    """Drop a region's own name: it shows its topic's name again, or its automatic one."""
    mem = set(region_members)
    with _lock:
        items = _load(store)
        keep = [r for r in items if _held(set(r["members"]), mem) < MATCH]
        if len(keep) != len(items):
            _save(store, keep)

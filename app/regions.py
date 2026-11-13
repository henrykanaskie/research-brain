"""Region names on the graph.

Regions (app/server.py `_clusters`) are found again every time the library changes, so their ids aren't
stable. A region is recognised by its members instead: the set of papers and notes in it.

A region's name, in order:
  1. a name you gave it (kept in <library>/state/region_names.json, matched by members);
  2. the name of the topic with the same papers (Jaccard overlap >= MATCH), so a region and its topic read
     the same;
  3. its automatic name (its most distinctive terms).

Renaming a region that has a matching topic renames the topic too, unless you ask to keep them different;
then the region keeps its own name while the topic keeps its own.
"""
from __future__ import annotations

import json
import threading
import time
from pathlib import Path

MATCH = 0.5  # Jaccard overlap for "the same group of papers"
MAX_NAME = 80
_lock = threading.Lock()


def _file(store) -> Path:
    d = Path(store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d / "region_names.json"


def _load(store) -> list[dict]:
    try:
        raw = json.loads(_file(store).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    out = []
    for r in raw if isinstance(raw, list) else []:
        if isinstance(r, dict) and isinstance(r.get("members"), list) and str(r.get("name") or "").strip():
            out.append({"members": [str(m) for m in r["members"]], "name": str(r["name"])[:MAX_NAME],
                        "at": r.get("at") or 0})
    return out


def _save(store, items: list[dict]) -> None:
    p = _file(store)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(items, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)


def _jaccard(a: set, b: set) -> float:
    return len(a & b) / max(1, len(a | b))


def signature(store) -> tuple:
    """Changes when a region name or any topic changes (the graph payload is cached on it)."""
    from . import topics as T
    try:
        f = _file(store)
        own = f.stat().st_mtime_ns if f.exists() else 0
    except OSError:
        own = 0
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


def _best(groups: dict[int, set], cands: list[set]) -> dict[int, int]:
    """Pair regions with candidates one to one, best overlap first, at least MATCH."""
    pairs = sorted(((_jaccard(m, c), cid, k) for cid, m in groups.items() for k, c in enumerate(cands)),
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
    ("custom" | "topic" | "auto"), and topic {slug, name} when a topic has the same papers."""
    groups = {c["id"]: set(members.get(c["id"], [])) for c in clusters}
    custom = _load(store)
    topics = _topics(store)
    by_custom = _best(groups, [set(r["members"]) for r in custom])
    by_topic = _best(groups, [t["members"] for t in topics])
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
        else:
            d["name_source"] = "auto"
        out.append(d)
    return out


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
        items = [r for r in _load(store) if _jaccard(set(r["members"]), mem) < MATCH]
        if rename_topic and topic_slug:
            try:
                T.update_topic(store, topic_slug, name=name)
            except KeyError:
                raise RegionError("That topic no longer exists")
            except T.TopicError as e:
                raise RegionError(str(e))
        else:
            items.append({"members": sorted(mem), "name": name, "at": time.time()})
        _save(store, items)
    return {"name": name, "renamed_topic": bool(rename_topic and topic_slug)}


def reset(store, region_members: list[str]) -> None:
    """Drop a region's own name: it shows its topic's name again, or its automatic one."""
    mem = set(region_members)
    with _lock:
        items = _load(store)
        keep = [r for r in items if _jaccard(set(r["members"]), mem) < MATCH]
        if len(keep) != len(items):
            _save(store, keep)

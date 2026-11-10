"""Topics (user-made, many-to-many groups of notes) and per-topic reading queues.

Storage: one Markdown file per topic, vault/topics/<slug>.md, so topics are portable, readable in
Obsidian, and included in the vault snapshot:

    ---
    name: Hall thruster erosion
    description: Wall erosion and magnetic shielding
    created: 2026-11-09T10:00:00-08:00
    dismissed:                     # recommendations the user said no to (fed to S2 as negatives)
    - {s2_id: abc123, title: Some paper, at: '2026-11-09T10:05:00-08:00'}
    snoozed: {abc123: '2026-11-23T10:05:00-08:00', 'note:my-paper-2020': '...'}   # "Not now", 14 days
    ---
    - [[magnetic-shielding-of-a-hall-thruster-2014]]
    - [[erosion-measurements-2019]]

The body is the ordered member list (one `- [[note-id]]` per line). Membership, dismissals and
snoozes are the user's decisions and live only here (never in .index, which is derived and safe
to delete). Derived caches live in .index: refs-cache.json (Semantic Scholar references per paper,
with a TTL) and topic-recs.json (each topic's last computed recommendations).

Functions other modules (the Research agent, the library map) can call without the HTTP layer:
    list_topics(store)                 -> [{slug, name, description, created, count, counts}]
    topic_detail(store, slug)          -> one topic with members grouped by status
    topics_for(store, note_id)         -> [{slug, name}] topics a note belongs to
    papers_by_status(store, status, topic=None) -> [{id, title, year, status, saved_because}]
    local_queue(store, idx, slug)      -> the topic's to-read papers, ranked
    queue(store, idx, embedder, slug)  -> the topic's reading queue (to-read papers + recommendations)
"""
from __future__ import annotations

import datetime as dt
import hashlib
import os
import re
import shutil
import threading
import time
from pathlib import Path

import numpy as np
import yaml

from .store import STATUSES, NoAliasDumper, load_frontmatter, now_iso, slugify

STATUS_ORDER = ("to-read", "reading", "later", "read", "not-relevant", "")
STATUS_LABEL = {"to-read": "To read", "reading": "Reading", "later": "Later", "read": "Read",
                "not-relevant": "Not relevant", "": "No status"}
SNOOZE_DAYS = 14
REFS_TTL = 14 * 86400         # references of a paper barely change
RECS_TTL = 86400              # recommendations are refreshed at most daily (unless asked)
RECS_RETRY = 3600             # after a failed refresh (offline), don't auto-retry for an hour
RRF_K = 60
MAX_POSITIVES = 100
REC_POOL = 30                 # recommendations kept per topic
PAGE = 5                      # the queue never shows more than this at once

_MEMBER_RE = re.compile(r"^\s*[-*]\s*\[\[([a-z0-9][a-z0-9-]*)(?:\|[^\]]*)?\]\]")
_lock = threading.RLock()


class TopicError(ValueError):
    pass


def _now() -> dt.datetime:
    return dt.datetime.now().astimezone()


def _parse_time(s) -> dt.datetime | None:
    if isinstance(s, dt.datetime):
        return s if s.tzinfo else s.astimezone()
    try:
        t = dt.datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def norm_title(t: str) -> str:
    return re.sub(r"[^a-z0-9]+", " ", (t or "").lower()).strip()


# ---------------------------------------------------------------- files
def topics_dir(store) -> Path:
    d = Path(store.root) / "topics"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(store, slug: str) -> Path:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", slug or ""):
        raise KeyError(slug)
    return topics_dir(store) / f"{slug}.md"


def _read(store, slug: str) -> dict:
    p = _path(store, slug)
    if not p.exists():
        raise KeyError(slug)
    raw = p.read_text(encoding="utf-8").replace("\r\n", "\n")
    meta, body = {}, raw
    if raw.startswith("---\n"):
        end = raw.find("\n---\n", 3)
        if end == -1 and raw.rstrip().endswith("\n---"):
            end = len(raw.rstrip()) - 4
        if end != -1:
            try:
                loaded = load_frontmatter(raw[4:end + 1])
                meta = loaded if isinstance(loaded, dict) else {}
            except yaml.YAMLError:
                meta = {}
            body = raw[end + 5:]
    members: list[str] = []
    for line in body.splitlines():
        m = _MEMBER_RE.match(line)
        if m and m.group(1) not in members:
            members.append(m.group(1))
    dismissed = [d for d in (meta.get("dismissed") or []) if isinstance(d, dict)]
    snoozed = meta.get("snoozed") if isinstance(meta.get("snoozed"), dict) else {}
    created = meta.get("created")
    return {
        "slug": slug,
        "name": str(meta.get("name") or slug),
        "description": str(meta.get("description") or ""),
        "created": created.isoformat() if isinstance(created, (dt.date, dt.datetime)) else str(created or ""),
        "members": members,
        "dismissed": [{"s2_id": str(d.get("s2_id") or ""), "title": str(d.get("title") or ""),
                       "at": str(d.get("at") or "")} for d in dismissed],
        "snoozed": {str(k): str(v) for k, v in snoozed.items() if v},
    }


def _write(store, t: dict) -> None:
    now = _now()
    snoozed = {k: v for k, v in (t.get("snoozed") or {}).items() if (_parse_time(v) or now) > now}
    meta = {"name": t["name"], "description": t.get("description") or "", "created": t.get("created") or now_iso()}
    if t.get("dismissed"):
        meta["dismissed"] = t["dismissed"]
    if snoozed:
        meta["snoozed"] = snoozed
    front = yaml.dump(meta, Dumper=NoAliasDumper, sort_keys=False, allow_unicode=True, width=1000)
    body = "".join(f"- [[{m}]]\n" for m in t["members"])
    p = _path(store, t["slug"])
    tmp = p.with_suffix(".md.tmp")
    tmp.write_text(f"---\n{front}---\n{body}", encoding="utf-8")
    tmp.replace(p)


def _all_slugs(store) -> list[str]:
    return sorted(p.stem for p in topics_dir(store).glob("*.md") if re.fullmatch(r"[a-z0-9][a-z0-9-]*", p.stem))


def _notes_by_id(store) -> dict[str, dict]:
    return {n["id"]: n for n in store.all()}


def _brief(n: dict) -> dict:
    return {"id": n["id"], "title": n["title"], "type": n["type"], "year": n["year"],
            "authors": n["authors"][:3], "status": n.get("status") or "", "saved_because": n.get("saved_because") or "",
            "s2_id": n.get("s2_id") or ""}


def _counts(members: list[str], by_id: dict) -> dict[str, int]:
    c = {s: 0 for s in STATUS_ORDER}
    for m in members:
        if m in by_id:
            c[by_id[m].get("status") or ""] += 1
    return c


# ---------------------------------------------------------------- CRUD
def list_topics(store, by_id: dict | None = None) -> list[dict]:
    by_id = by_id if by_id is not None else _notes_by_id(store)
    out = []
    for slug in _all_slugs(store):
        try:
            t = _read(store, slug)
        except (KeyError, OSError, UnicodeDecodeError):
            continue
        live = [m for m in t["members"] if m in by_id]
        out.append({"slug": slug, "name": t["name"], "description": t["description"], "created": t["created"],
                    "count": len(live), "counts": _counts(live, by_id)})
    out.sort(key=lambda t: t["name"].casefold())
    return out


def get_topic(store, slug: str) -> dict:
    with _lock:
        return _read(store, slug)


def create_topic(store, name: str, description: str = "", members: list[str] | None = None) -> dict:
    name = " ".join((name or "").split())[:120]
    if not name:
        raise TopicError("Give the topic a name")
    with _lock:
        if any(t["name"].casefold() == name.casefold() for t in list_topics(store, {})):
            raise TopicError(f"There is already a topic called “{name}”")
        base = slugify(name, 50)
        slug, i = base, 2
        while _path(store, slug).exists():
            slug, i = f"{base}-{i}", i + 1
        valid = _valid_members(store, members or [])
        t = {"slug": slug, "name": name, "description": " ".join((description or "").split())[:500],
             "created": now_iso(), "members": valid, "dismissed": [], "snoozed": {}}
        _write(store, t)
        return _read(store, slug)


def update_topic(store, slug: str, name: str | None = None, description: str | None = None) -> dict:
    with _lock:
        t = _read(store, slug)
        if name is not None:
            name = " ".join(name.split())[:120]
            if not name:
                raise TopicError("Give the topic a name")
            if any(x["slug"] != slug and x["name"].casefold() == name.casefold() for x in list_topics(store, {})):
                raise TopicError(f"There is already a topic called “{name}”")
            t["name"] = name
        if description is not None:
            t["description"] = " ".join(description.split())[:500]
        _write(store, t)
        return _read(store, slug)


def delete_topic(store, slug: str) -> None:
    with _lock:
        p = _path(store, slug)
        if not p.exists():
            raise KeyError(slug)
        dest = Path(store.trash_dir) / f"topic-{slug}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}"
        dest.mkdir(parents=True, exist_ok=True)
        shutil.move(str(p), dest / p.name)
    _drop_recs(store, slug)


def _valid_members(store, ids: list[str]) -> list[str]:
    out = []
    for nid in ids:
        if not isinstance(nid, str) or nid in out:
            continue
        try:
            n = store.get(nid)
        except KeyError:
            raise TopicError(f"No note with id '{nid}'")
        if n["type"] == "fragment":
            raise TopicError("Add the paper or note itself to a topic, not one of its fragments")
        out.append(nid)
    return out


def add_notes(store, slug: str, ids: list[str]) -> dict:
    with _lock:
        t = _read(store, slug)
        for nid in _valid_members(store, ids):
            if nid not in t["members"]:
                t["members"].append(nid)
        _write(store, t)
        return _read(store, slug)


def remove_note(store, slug: str, nid: str) -> dict:
    with _lock:
        t = _read(store, slug)
        t["members"] = [m for m in t["members"] if m != nid]
        _write(store, t)
        return _read(store, slug)


def topics_for(store, nid: str) -> list[dict]:
    out = []
    for slug in _all_slugs(store):
        try:
            t = _read(store, slug)
        except (KeyError, OSError, UnicodeDecodeError):
            continue
        if nid in t["members"]:
            out.append({"slug": slug, "name": t["name"]})
    out.sort(key=lambda t: t["name"].casefold())
    return out


def topic_detail(store, slug: str) -> dict:
    """The topic with its live members grouped by status: groups = [{status, label, count, items}]."""
    t = get_topic(store, slug)
    by_id = _notes_by_id(store)
    live = [m for m in t["members"] if m in by_id]
    groups = []
    for s in STATUS_ORDER:
        items = [_brief(by_id[m]) for m in live if (by_id[m].get("status") or "") == s]
        groups.append({"status": s, "label": STATUS_LABEL[s], "count": len(items), "items": items})
    return {"slug": slug, "name": t["name"], "description": t["description"], "created": t["created"],
            "count": len(live), "counts": _counts(live, by_id), "members": live, "groups": groups,
            "dismissed": len(t["dismissed"])}


def papers_by_status(store, status: str, topic: str | None = None) -> list[dict]:
    """Library papers/notes with a reading status, optionally only those in one topic."""
    if status not in STATUSES and status != "":
        raise TopicError(f"status must be one of {', '.join(STATUSES)}")
    by_id = _notes_by_id(store)
    ids = get_topic(store, topic)["members"] if topic else sorted(by_id)
    return [_brief(by_id[i]) for i in ids if i in by_id and by_id[i]["type"] != "fragment"
            and (by_id[i].get("status") or "") == status]


# ---------------------------------------------------------------- dismiss / snooze
def dismiss(store, slug: str, s2_id: str = "", title: str = "") -> dict:
    if not (s2_id or title):
        raise TopicError("Say which recommendation to dismiss")
    with _lock:
        t = _read(store, slug)
        if not any((s2_id and d["s2_id"] == s2_id) or (not s2_id and norm_title(d["title"]) == norm_title(title))
                   for d in t["dismissed"]):
            t["dismissed"].append({"s2_id": s2_id, "title": title[:300], "at": now_iso()})
        _write(store, t)
        return _read(store, slug)


def snooze(store, slug: str, key: str, days: int = SNOOZE_DAYS) -> dict:
    """key = a recommendation's s2_id, or "note:<id>" for a library paper in the queue."""
    if not key:
        raise TopicError("Say what to snooze")
    days = max(1, min(int(days or SNOOZE_DAYS), 365))
    with _lock:
        t = _read(store, slug)
        t["snoozed"][key] = (_now() + dt.timedelta(days=days)).isoformat(timespec="seconds")
        _write(store, t)
        return _read(store, slug)


def _snoozed(t: dict) -> set[str]:
    now = _now()
    return {k for k, v in t["snoozed"].items() if (_parse_time(v) or now) > now}


def _dismissed_keys(t: dict) -> tuple[set[str], set[str]]:
    return ({d["s2_id"] for d in t["dismissed"] if d["s2_id"]},
            {norm_title(d["title"]) for d in t["dismissed"] if d["title"]})


# ---------------------------------------------------------------- embeddings helpers
def _vectors(idx: dict, ids: list[str]) -> np.ndarray | None:
    m = idx.get("m")
    if m is None:
        return None
    pos = {i: k for k, i in enumerate(idx.get("ids") or [])}
    rows = [pos[i] for i in ids if i in pos]
    return m[rows] if rows else None


def _unit(v: np.ndarray) -> np.ndarray:
    n = float(np.linalg.norm(v))
    return v / n if n > 0 else v


def _anchors(t: dict, by_id: dict) -> list[str]:
    """Papers that define what the topic is about: the ones read or being read, else every member
    that isn't marked not relevant."""
    live = [m for m in t["members"] if m in by_id]
    core = [m for m in live if by_id[m].get("status") in ("read", "reading")]
    return core or [m for m in live if by_id[m].get("status") != "not-relevant"]


# ---------------------------------------------------------------- local queue (to-read papers)
def local_queue(store, idx: dict, slug: str, by_id: dict | None = None, t: dict | None = None) -> list[dict]:
    """The topic's own to-read papers, most relevant first: cosine similarity to the centroid of
    the topic's read/reading papers, plus 0.15 for each topic paper that cites it."""
    t = t or get_topic(store, slug)
    by_id = by_id if by_id is not None else idx.get("by_id") or _notes_by_id(store)
    live = [m for m in t["members"] if m in by_id]
    snoozed = _snoozed(t)
    todo = [m for m in live if by_id[m].get("status") == "to-read" and f"note:{m}" not in snoozed]
    if not todo:
        return []
    anchors = [a for a in _anchors(t, by_id) if a not in todo] or []
    cited = {m: sum(1 for o in live if o != m and m in (by_id[o].get("cites") or [])) for m in todo}
    av = _vectors(idx, anchors)
    centroid = _unit(av.mean(axis=0)) if av is not None and len(av) else None
    pos = {i: k for k, i in enumerate(idx.get("ids") or [])}
    out = []
    for k, m in enumerate(todo):
        sim, near = 0.0, ""
        if centroid is not None and m in pos and idx.get("m") is not None:
            v = idx["m"][pos[m]]
            sim = float(v @ centroid)
            sims = av @ v
            j = int(np.argmax(sims))
            near = [a for a in anchors if a in pos][j] if float(sims[j]) > 0 else ""
        score = sim + 0.15 * cited[m]
        if cited[m]:
            reason = f"Cited by {cited[m]} paper{'s' if cited[m] != 1 else ''} in this topic"
        elif near:
            reason = f"Similar to {by_id[near]['title']}"
        else:
            reason = "In this topic, not read yet"
        item = _brief(by_id[m])
        item.update(kind="library", key=f"note:{m}", reason=reason, score=round(score, 4), _order=k)
        out.append(item)
    out.sort(key=lambda x: (-x["score"], x["_order"]))
    for x in out:
        x.pop("_order", None)
    return out


# ---------------------------------------------------------------- recommendations (background, cached)
_running: dict[str, threading.Thread] = {}
_run_lock = threading.Lock()


def _recs_cache(store) -> dict:
    return store.load_json("topic-recs.json")


def _drop_recs(store, slug: str) -> None:
    with _lock:
        c = _recs_cache(store)
        if slug in c:
            del c[slug]
            store.save_json("topic-recs.json", c)


def _positives_negatives(t: dict, by_id: dict) -> tuple[list[str], list[str]]:
    from .citations import s2_key
    live = [by_id[m] for m in t["members"] if m in by_id and by_id[m]["type"] == "paper"]
    pos = [s2_key(n) for n in live if s2_key(n) and n.get("status") != "not-relevant"][-MAX_POSITIVES:]
    neg = [s2_key(n) for n in live if s2_key(n) and n.get("status") == "not-relevant"]
    neg += [d["s2_id"] for d in t["dismissed"] if d["s2_id"]]
    return pos, list(dict.fromkeys(neg))


def _signature(pos: list[str], neg: list[str]) -> str:
    return hashlib.sha1(("|".join(sorted(pos)) + "#" + "|".join(sorted(neg))).encode()).hexdigest()[:16]


def _cached_refs(store, key: str) -> list[dict]:
    """scholar.references for one paper, cached in .index/refs-cache.json for REFS_TTL."""
    from . import citations
    cache = store.load_json("refs-cache.json")
    hit = cache.get(key)
    if hit and time.time() - float(hit.get("at", 0)) < REFS_TTL:
        return hit.get("refs") or []
    refs = citations._fetch(key)  # throttled; raises citations.Unreachable when offline
    with _lock:
        cache = store.load_json("refs-cache.json")
        cache[key] = {"at": time.time(), "refs": refs}
        store.save_json("refs-cache.json", cache)
    return refs


def compute_recs(store, idx: dict, embedder, slug: str) -> dict:
    """Recommend papers not in the library for a topic, fusing three ranked lists with reciprocal
    rank fusion (score = sum over lists of 1 / (60 + rank)):
      (a) papers cited by 2+ of the topic's papers (ranked by how many cite them),
      (b) Semantic Scholar multi-paper recommendations (positives = the topic's papers, negatives =
          its not-relevant papers and everything dismissed), in the order S2 returns them,
      (c) similarity of each candidate's title + abstract to the topic's centroid.
    Returns {"items", "errors", "offline"}. Network failures in one source don't stop the others."""
    from . import citations, scholar
    t = get_topic(store, slug)
    by_id = idx.get("by_id") or _notes_by_id(store)
    lib = citations.LibraryIndex(list(by_id.values()))
    pos, neg = _positives_negatives(t, by_id)
    d_ids, d_titles = _dismissed_keys(t)
    errors: list[str] = []
    offline = False
    cands: dict[str, dict] = {}

    def key_of(c: dict) -> str:
        return c.get("s2_id") or ("t:" + norm_title(c.get("title", "")))

    def excluded(c: dict) -> bool:
        return (not c.get("title") or lib.match(c) is not None or (c.get("s2_id") and c["s2_id"] in d_ids)
                or norm_title(c["title"]) in d_titles)

    # (a) cited by 2+ topic papers
    cite_count: dict[str, int] = {}
    for key in pos:
        try:
            refs = _cached_refs(store, key)
        except citations.Unreachable as e:
            offline = True
            errors.append(str(e))
            break
        except Exception as e:  # rate limited, 5xx: skip this paper
            errors.append(f"references: {e}")
            continue
        seen = set()
        for r in refs:
            if excluded(r):
                continue
            k = key_of(r)
            if k in seen:
                continue
            seen.add(k)
            cite_count[k] = cite_count.get(k, 0) + 1
            cands.setdefault(k, {"s2_id": r.get("s2_id", ""), "title": r["title"], "doi": r.get("doi", ""),
                                 "arxiv": r.get("arxiv", ""), "year": None, "authors": [], "abstract": ""})
    list_a = sorted((k for k, n in cite_count.items() if n >= 2), key=lambda k: (-cite_count[k], cands[k]["title"]))

    # (b) Semantic Scholar recommendations
    list_b: list[str] = []
    if pos and not offline:
        try:
            citations._throttle()
            for p in scholar.recommend_for_papers(pos, neg, limit=60):
                if excluded(p):
                    continue
                k = key_of(p)
                base = cands.setdefault(k, {"s2_id": p.get("s2_id", ""), "title": p["title"]})
                for f in ("doi", "arxiv", "year", "authors", "abstract", "venue", "url"):
                    if p.get(f) and not base.get(f):
                        base[f] = p[f]
                if k not in list_b:
                    list_b.append(k)
        except scholar.ScholarError as e:
            if "Couldn't reach" in str(e):
                offline = True
            errors.append(f"recommendations: {e}")
    # (c) semantic similarity to the topic's centroid (and to its closest paper, for the reason)
    sim: dict[str, float] = {}
    near: dict[str, str] = {}
    anchors = _anchors(t, by_id)
    av = _vectors(idx, anchors)
    anchor_ids = [a for a in anchors if a in set(idx.get("ids") or [])]
    if av is not None and len(av) and cands:
        centroid = _unit(av.mean(axis=0))
        for k, c in cands.items():
            try:
                v = embedder.embed_query(f"{c['title']}\n\n{c.get('abstract') or ''}")
            except Exception:
                v = None
            if v is None or v.shape[0] != centroid.shape[0]:
                continue
            sim[k] = float(v @ centroid)
            sims = av @ v
            j = int(np.argmax(sims))
            if float(sims[j]) > 0.05:
                near[k] = anchor_ids[j]
    list_c = sorted((k for k, s in sim.items() if s > 0), key=lambda k: -sim[k])

    fused: dict[str, float] = {}
    for lst in (list_a, list_b, list_c):
        for r, k in enumerate(lst):
            fused[k] = fused.get(k, 0.0) + 1.0 / (RRF_K + r + 1)
    # a candidate only seen as a reference of ONE topic paper, with no other signal, isn't evidence
    keep = [k for k in fused if k in set(list_a) | set(list_b)]
    keep.sort(key=lambda k: (-fused[k], cands[k]["title"]))
    items = []
    for k in keep[:REC_POOL]:
        c = cands[k]
        n_cite = cite_count.get(k, 0)
        if n_cite >= 2:
            reason = f"Cited by {n_cite} papers in this topic"
        elif k in near and sim.get(k, 0) > 0.05:
            reason = f"Similar to {by_id[near[k]]['title']}"
        else:
            reason = "Semantic Scholar recommends it for this topic"
        sources = [s for s, lst in (("cited", list_a), ("s2", list_b), ("similar", list_c)) if k in lst]
        items.append({"kind": "rec", "key": c.get("s2_id") or k, "s2_id": c.get("s2_id", ""), "title": c["title"],
                      "year": c.get("year"), "authors": (c.get("authors") or [])[:3], "doi": c.get("doi", ""),
                      "arxiv": c.get("arxiv", ""), "abstract": (c.get("abstract") or "")[:1500],
                      "venue": c.get("venue", ""), "url": c.get("url", ""),
                      "reason": reason, "score": round(fused[k], 5), "sources": sources, "cited_by": n_cite})
    return {"items": items, "errors": errors, "offline": offline and not items, "sig": _signature(pos, neg)}


def refresh_recs(store, idx_fn, embedder, slug: str) -> dict:
    """Compute and cache a topic's recommendations (synchronously). Offline or failed: keep the old
    items and record when it was tried, so the queue can say it's showing cached ones."""
    res = compute_recs(store, idx_fn(), embedder, slug)
    with _lock:
        cache = _recs_cache(store)
        old = cache.get(slug) or {}
        entry = dict(old)
        entry["tried"] = time.time()
        failed = not res["items"] and res["errors"]
        if failed:
            entry["error"] = res["errors"][0]
            entry["offline"] = bool(res["offline"])
        else:
            entry.update(items=res["items"], at=time.time(), sig=res["sig"], error="", offline=False)
        cache[slug] = entry
        store.save_json("topic-recs.json", cache)
    return entry


def kick_refresh(store, idx_fn, embedder, slug: str) -> bool:
    """Start a background refresh for the topic unless one is already running. True if started."""
    with _run_lock:
        th = _running.get(slug)
        if th and th.is_alive():
            return False

        def run():
            try:
                refresh_recs(store, idx_fn, embedder, slug)
            except KeyError:
                pass  # topic deleted meanwhile
            except Exception as e:
                print(f"[research_brain] recommendations for topic {slug} failed: {e!r}")
            finally:
                with _run_lock:
                    _running.pop(slug, None)
        th = threading.Thread(target=run, daemon=True, name=f"topic-recs-{slug}")
        _running[slug] = th
        th.start()
        return True


def is_refreshing(slug: str) -> bool:
    with _run_lock:
        th = _running.get(slug)
        return bool(th and th.is_alive())


def wait_refresh(slug: str, timeout: float = 30.0) -> None:
    with _run_lock:
        th = _running.get(slug)
    if th:
        th.join(timeout)


def _needs_refresh(entry: dict, sig: str) -> bool:
    now = time.time()
    if entry.get("tried") and now - float(entry["tried"]) < RECS_RETRY and entry.get("error"):
        return False  # failed recently (offline): don't hammer
    if not entry.get("at"):
        return True
    return now - float(entry["at"]) > RECS_TTL or entry.get("sig") != sig


def cached_recs(store, slug: str, t: dict, by_id: dict) -> list[dict]:
    """The cached recommendations still worth showing: not dismissed, not snoozed, not now in the library."""
    from .citations import LibraryIndex
    entry = _recs_cache(store).get(slug) or {}
    lib = LibraryIndex(list(by_id.values()))
    d_ids, d_titles = _dismissed_keys(t)
    sn = _snoozed(t)
    out = []
    for it in entry.get("items") or []:
        if it.get("s2_id") in d_ids or norm_title(it.get("title", "")) in d_titles or it.get("key") in sn:
            continue
        if lib.match(it) is not None:
            continue
        out.append(dict(it))
    return out


def find_rec(store, slug: str, s2_id: str) -> dict | None:
    for it in (_recs_cache(store).get(slug) or {}).get("items") or []:
        if it.get("s2_id") == s2_id or it.get("key") == s2_id:
            return dict(it)
    return None


def queue(store, idx: dict, embedder, slug: str, idx_fn=None, offset: int = 0, limit: int = PAGE,
          auto: bool = True) -> dict:
    """The topic's reading queue: its to-read papers (ranked) first, then cached recommendations.
    Never computes recommendations inline: a stale or missing cache starts a background refresh."""
    t = get_topic(store, slug)
    by_id = idx.get("by_id") or _notes_by_id(store)
    local = local_queue(store, idx, slug, by_id, t)
    recs = cached_recs(store, slug, t, by_id)
    entry = _recs_cache(store).get(slug) or {}
    pos, neg = _positives_negatives(t, by_id)
    refreshing = is_refreshing(slug)
    if auto and idx_fn is not None and pos and not refreshing and _needs_refresh(entry, _signature(pos, neg)) \
            and os.environ.get("RB_RECS_AUTO", "1") != "0":
        refreshing = kick_refresh(store, idx_fn, embedder, slug)
    items = local + recs
    limit = max(1, min(int(limit or PAGE), 50))
    offset = max(0, int(offset or 0))
    note = ""
    if not pos:
        state = "none"
        note = "" if not t["members"] else "Recommendations need papers with a DOI, arXiv id or Semantic Scholar id."
    elif entry.get("error") and entry.get("offline"):
        state = "offline"
        note = (f"Offline. Showing recommendations from {_ago(entry['at'])}." if entry.get("at") and recs
                else "Offline. Showing only your to-read papers.")
    elif entry.get("error") and not entry.get("at"):
        state = "error"
        note = "Couldn't get recommendations just now. Showing your to-read papers."
    elif refreshing and not entry.get("at"):
        state = "refreshing"
        note = "Looking for recommendations."
    else:
        state = "fresh" if entry.get("at") else "none"
    return {"slug": slug, "name": t["name"], "items": items[offset:offset + limit], "total": len(items),
            "offset": offset, "limit": limit, "to_read": len(local), "recommended": len(recs),
            "recs_state": state, "refreshing": refreshing, "note": note,
            "recs_at": entry.get("at")}


def global_queue(store, idx: dict, embedder, idx_fn=None, offset: int = 0, limit: int = PAGE) -> dict:
    """Next to read across every topic: round-robin over the topics' own queues (so no one topic
    crowds out the others), de-duplicated. With no topics, the library's to-read papers, newest first."""
    by_id = idx.get("by_id") or _notes_by_id(store)
    slugs = [t["slug"] for t in list_topics(store, by_id)]
    per: list[tuple[dict, list[dict]]] = []
    notes = []
    for slug in slugs:
        try:
            q = queue(store, idx, embedder, slug, idx_fn=idx_fn, offset=0, limit=50)
        except KeyError:
            continue
        per.append((q, q["items"]))
        if q["note"] and q["recs_state"] == "offline":
            notes.append(q["note"])
    items, seen = [], set()
    if per:
        depth = max(len(lst) for _, lst in per)
        for r in range(depth):
            for q, lst in per:
                if r < len(lst):
                    it = lst[r]
                    k = it.get("id") or it.get("s2_id") or norm_title(it["title"])
                    if k in seen:
                        continue
                    seen.add(k)
                    items.append(dict(it, topic={"slug": q["slug"], "name": q["name"]}))
    else:
        todo = [n for n in by_id.values() if n["type"] == "paper" and n.get("status") == "to-read"]
        todo.sort(key=lambda n: n.get("created") or "", reverse=True)
        for n in todo:
            it = _brief(n)
            it.update(kind="library", key=f"note:{n['id']}", reason="Added to your library, not read yet", score=0.0,
                      topic=None)
            items.append(it)
    limit = max(1, min(int(limit or PAGE), 50))
    offset = max(0, int(offset or 0))
    return {"items": items[offset:offset + limit], "total": len(items), "offset": offset, "limit": limit,
            "topics": len(per), "note": "Offline. Some recommendations are from earlier." if notes else ""}


def _ago(ts: float) -> str:
    s = max(0, time.time() - float(ts))
    if s < 3600:
        return "a few minutes ago" if s < 600 else f"{int(s // 60)} minutes ago"
    if s < 86400:
        h = int(s // 3600)
        return f"{h} hour{'s' if h != 1 else ''} ago"
    d = int(s // 86400)
    return f"{d} day{'s' if d != 1 else ''} ago"


# ---------------------------------------------------------------- regions -> topic suggestions
def suggest_from_regions(store, regions: list[dict]) -> list[dict]:
    """regions: [{"id", "label", "members": [note ids]}] (auto-detected graph regions). Returns those
    not already covered by a topic (Jaccard overlap < 0.6), with the label as a suggested name."""
    existing = []
    for slug in _all_slugs(store):
        try:
            existing.append(set(_read(store, slug)["members"]))
        except (KeyError, OSError, UnicodeDecodeError):
            continue
    out = []
    for r in regions:
        mem = set(r["members"])
        if len(mem) < 2:
            continue
        if any(len(mem & e) / max(1, len(mem | e)) >= 0.6 for e in existing):
            continue
        label = (r.get("label") or "").strip()
        name = (label[:1].upper() + label[1:]) if label else f"Region {r['id'] + 1}"
        out.append({"region": r["id"], "name": name, "label": label, "size": len(mem), "members": r["members"]})
    return out

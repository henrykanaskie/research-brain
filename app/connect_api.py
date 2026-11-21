"""AI connection analysis: your AI agent reads papers that a similarity search thinks belong together and marks
the pairs that really do as AI connections. These are kept apart from the links you draw yourself (they are
drawn as faint dashed lines on the map, not red ones, and pull only lightly), so running it never fills the map
with red lines you didn't make. Right-click one to remove it or to make it a link of your own.

    POST /api/links/ai-connect                {ids?, folder?, topics?, unsorted?}: start a job over those papers
    GET  /api/links/ai-connect/jobs/{job}     its progress, and the links it made
    POST /api/links/ai-connect/jobs/{job}/stop
    POST /api/links/ai-connect/jobs/{job}/undo   take back the links it made
    POST /api/links/ai/remove                 {a, b}: drop one AI connection (it isn't suggested again)
    POST /api/links/ai/keep                   {a, b}: make it a link of your own (a red line)
    GET  /api/links/ai-connect/legacy         how many of your links were made by an older version of this
    POST /api/links/ai-connect/legacy         {undo?}: turn those back into AI connections (or undo that)

How: within the chosen papers, each one's closest few by text similarity become candidate pairs (strongest
first), plus each paper's closest few within its own group when groups are given (a reorganizing's new topics),
so papers filed together are always checked against each other. The agent gets them in batches, each with the titles and openings of the papers involved, and keeps only
the pairs a researcher would want linked; it may add other clearly connected pairs among the same papers. Every
pair it was asked about is remembered (.index/ai-links.json), so running it again only asks about new pairs,
and a link you remove is not put back. Private papers are never sent. Jobs run one at a time.
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

import numpy as np
from fastapi import HTTPException
from pydantic import BaseModel

PER_PAPER = 4        # candidate partners per paper
BATCH = 60           # pairs per question to the agent
MAX_BATCHES = 25     # per job (1,500 pairs); the rest wait for the next run
MAX_POOL = 4000
CACHE = "ai-links.json"

INSTRUCTION = (
    "You are checking which of the student's PAPERS are genuinely connected. Each paper is listed with its id in "
    "[[...]], its title and the start of its abstract or text. CANDIDATE PAIRS lists pairs of paper ids that a "
    "text-similarity search found. Keep a pair only when a researcher would want the two papers linked: they study "
    "the same problem or phenomenon, use or extend the same method, one builds on, tests or contradicts the other, "
    "or their results bear directly on each other. Sharing only a broad field is not enough. You may also add other "
    "pairs from PAPERS that are clearly connected in that way. Reply with JSON only, no other text: "
    '{"links": [{"a": "paper-id", "b": "paper-id", "why": "a few words on how they connect"}]}')


class ConnectIn(BaseModel):
    ids: list[str] = []
    groups: list[list[str]] = []  # papers filed together (a reorganizing's topics): checked against each other
    folder: str = ""          # every paper in this folder of papers (app/linked.py)
    topics: list[str] = []    # every paper in these topics
    unsorted: bool = False    # every paper in no topic


class Ctx:
    store = None
    index = None      # () -> the index (by_id, ids, m)
    ask = None        # (instruction, context, purpose, sources) -> text; raises HTTPException
    visible = None    # by_id -> by_id the agent may see
    fulltext = None   # id -> text
    pool = None       # (ids, folder, topics, unsorted) -> paper ids
    publish = None


class PairIn(BaseModel):
    a: str
    b: str


class LegacyIn(BaseModel):
    undo: bool = False


ctx = Ctx()
REV = [0]   # bumped whenever the AI connections change (the map's cache key)
_jobs: dict[str, dict] = {}
_run_lock = threading.Lock()     # one job at a time; a second one waits its turn
_cache_lock = threading.Lock()


def install(app, *, store, index, ask, visible, fulltext, pool, publish=None):
    ctx.store, ctx.index, ctx.ask, ctx.visible, ctx.fulltext, ctx.pool, ctx.publish = (
        store, index, ask, visible, fulltext, pool, publish)

    @app.post("/api/links/ai-connect")
    def api_connect(body: ConnectIn):
        ids = ctx.pool(body.ids, body.folder, body.topics, body.unsorted)
        if len(ids) < 2:
            raise HTTPException(400, "It takes at least two papers to find connections between them.")
        pool = set(ids)
        groups = [[i for i in g if i in pool] for g in body.groups]
        return start(ids, [g for g in groups if len(g) > 1])

    @app.get("/api/links/ai-connect/jobs/{jid}")
    def api_connect_job(jid: str):
        return _public(_job(jid))

    @app.post("/api/links/ai-connect/jobs/{jid}/stop")
    def api_connect_stop(jid: str):
        j = _job(jid)
        j["stop"] = True
        return _public(j)

    @app.post("/api/links/ai-connect/jobs/{jid}/undo")
    def api_connect_undo(jid: str):
        j = _job(jid)
        if j["state"] in ("waiting", "running"):
            raise HTTPException(409, "It's still finding connections. Stop it first.")
        n = 0
        with _cache_lock:
            cache = _load_cache()
            for l in j["links"]:
                e = cache.get(_key(l["a"], l["b"]))
                if e and e.get("v") == 1:
                    e["v"] = 0  # asked about, and not a connection now: not suggested again
                    n += 1
            _save_cache(cache)
        REV[0] += 1
        _publish([x for l in j["links"] for x in (l["a"], l["b"])])
        j["links"], j["linked"], j["undone"] = [], 0, n
        return _public(j)


    @app.post("/api/links/ai/remove")
    def api_ai_remove(body: PairIn):
        if not _set(body.a, body.b, -1):
            raise HTTPException(404, "There's no AI connection between those two.")
        _publish([body.a, body.b])
        return {"ok": True}

    @app.post("/api/links/ai/keep")
    def api_ai_keep(body: PairIn):
        try:
            if body.b not in ctx.store.get(body.a)["links"]:
                ctx.store.link(body.a, body.b)
        except KeyError:
            raise HTTPException(404, "One of those papers is gone.")
        _set(body.a, body.b, 2)  # yours now: drawn as your link, never as an AI connection
        _publish([body.a, body.b])
        return {"ok": True}

    @app.get("/api/links/ai-connect/legacy")
    def api_ai_legacy():
        return {"count": len(legacy_pairs())}

    @app.post("/api/links/ai-connect/legacy")
    def api_ai_legacy_convert(body: LegacyIn):
        return {"converted": convert_legacy(undo=body.undo)}


def _set(a: str, b: str, v: int) -> bool:
    """Set the remembered answer for a pair: 1 an AI connection, 0 not one, -1 you removed it, 2 your own link."""
    with _cache_lock:
        cache = _load_cache()
        e = cache.get(_key(a, b))
        if not e or e.get("v") != 1:
            return False
        e["v"], e["at"] = v, round(time.time())
        _save_cache(cache)
    REV[0] += 1
    return True


def ai_links(by_id: dict) -> list[dict]:
    """Every AI connection between two notes that still exist: [{a, b, why}]. A pair you've linked yourself is
    left out (your link is drawn instead)."""
    with _cache_lock:
        cache = _load_cache()
    out = []
    for k, e in cache.items():
        if not isinstance(e, dict) or e.get("v") != 1:
            continue
        a, _, b = k.partition("|")
        if a in by_id and b in by_id and a != b:
            if b in (by_id[a].get("links") or []) or a in (by_id[b].get("links") or []):
                continue
            out.append({"a": a, "b": b, "why": e.get("why", "")})
    return out


def legacy_pairs() -> list[tuple[str, str]]:
    """Links of yours that an earlier version of the connection analysis made (it wrote its answers as your own
    links): pairs it answered yes to, that it was asked about while they weren't linked, and that are linked
    now. Pairs you've confirmed (made your own) are left alone."""
    with _cache_lock:
        cache = _load_cache()
    out = []
    for k, e in cache.items():
        if not isinstance(e, dict) or e.get("v") != 1 or e.get("new"):
            continue
        a, _, b = k.partition("|")
        try:
            na = ctx.store.get(a)
        except KeyError:
            continue
        if b in (na.get("links") or []):
            out.append((a, b))
    return out


def convert_legacy(undo: bool = False) -> int:
    """Turn the links an earlier version made into AI connections (they stay on the map, as faint dashed lines),
    or with undo put back the ones converted before."""
    n = 0
    if undo:
        with _cache_lock:
            cache = _load_cache()
            back = [k for k, e in cache.items() if isinstance(e, dict) and e.get("was_link")]
        for k in back:
            a, _, b = k.partition("|")
            try:
                if b not in ctx.store.get(a)["links"]:
                    ctx.store.link(a, b)
                n += 1
            except (KeyError, ValueError):
                continue
        with _cache_lock:
            cache = _load_cache()
            for k in back:
                if k in cache:
                    cache[k].pop("was_link", None)
            _save_cache(cache)
    else:
        pairs = legacy_pairs()
        for a, b in pairs:
            try:
                ctx.store.unlink(a, b)
                n += 1
            except KeyError:
                continue
        with _cache_lock:
            cache = _load_cache()
            for a, b in pairs:
                e = cache.get(_key(a, b))
                if e:
                    e["was_link"] = True
                    e["new"] = True  # an AI connection now, never a legacy link again
            _save_cache(cache)
    REV[0] += 1
    return n


def _job(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


def _public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k not in ("ids", "groups")}


def _publish(ids: list[str]) -> None:
    if ctx.publish and ids:
        try:
            ctx.publish({"type": "notes-changed", "ids": list(dict.fromkeys(ids))[:500]})
        except Exception:
            pass


def start(ids: list[str], groups: list[list[str]] | None = None) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "state": "waiting", "papers": len(ids), "pairs": 0, "checked": 0, "left": 0, "linked": 0,
           "batches": 0, "done_batches": 0, "links": [], "message": "", "started": time.time(), "ids": list(ids),
           "groups": [list(g) for g in (groups or [])]}
    _jobs[jid] = job
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"rb-connect-{jid}").start()
    return _public(job)


# ---------------------------------------------------------------- remembered answers
def _cache_path() -> Path:
    return Path(ctx.store.index_dir) / CACHE


def _load_cache() -> dict:
    try:
        d = json.loads(_cache_path().read_text(encoding="utf-8"))
        return d if isinstance(d, dict) else {}
    except (OSError, ValueError):
        return {}


def _save_cache(d: dict) -> None:
    p = _cache_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(d), encoding="utf-8")
        tmp.replace(p)
    except OSError as e:
        print(f"[research_brain] couldn't save {CACHE}: {e!r}")


def _key(a: str, b: str) -> str:
    return "|".join(sorted((a, b)))


# ---------------------------------------------------------------- candidates
def _distinct(sub: np.ndarray) -> np.ndarray:
    """The papers' vectors minus what they all share, re-normalised: in a library from one field, plain neural
    similarities are nearly all high, so "closest" picks almost at random; what sets each paper apart doesn't.
    (Too few papers to average: the plain vectors.)"""
    if len(sub) < 6:
        return sub
    c = sub - sub.mean(axis=0)
    return c / (np.linalg.norm(c, axis=1, keepdims=True) + 1e-12)


def candidates(idx: dict, ids: list[str], per: int = PER_PAPER,
               groups: list[list[str]] | None = None) -> list[tuple[str, str, float]]:
    """Each paper's `per` most similar papers among `ids` (text embeddings, by what sets them apart), and its
    closest few within its own group, as pairs, strongest first. Pairs already joined (a link, a citation) are
    left out."""
    by_id, m = idx["by_id"], idx.get("m")
    if m is None:
        return []
    pos = {i: r for r, i in enumerate(idx["ids"])}
    ids = [i for i in ids if i in pos][:MAX_POOL]
    if len(ids) < 2:
        return []
    sub = _distinct(np.asarray(m, dtype=float)[[pos[i] for i in ids]])
    sims = sub @ sub.T
    np.fill_diagonal(sims, -np.inf)
    k = min(per, len(ids) - 1)
    best: dict[tuple[str, str], float] = {}
    for r, i in enumerate(ids):
        top = np.argpartition(-sims[r], k - 1)[:k]
        for c in top:
            s = float(sims[r, c])
            if not np.isfinite(s) or s <= 0.01:
                continue
            key = tuple(sorted((i, ids[c])))
            best[key] = max(best.get(key, 0.0), s)
    # papers filed together: each one's closest few in its own group are asked about too, whatever the rest
    # of the library looks like (so a paper is never left in a topic with no connection checked)
    at = {i: r for r, i in enumerate(ids)}
    for g in groups or []:
        rows = [at[i] for i in g if i in at]
        if len(rows) < 2:
            continue
        gs = sims[np.ix_(rows, rows)]
        kg = min(3, len(rows) - 1)
        for a, r in enumerate(rows):
            for c in np.argsort(-gs[a], kind="stable")[:kg]:
                s = float(gs[a, c])
                if not np.isfinite(s):
                    continue
                key = tuple(sorted((ids[r], ids[rows[c]])))
                best[key] = max(best.get(key, 0.0), max(s, 0.02))
    out = []
    for (a, b), s in best.items():
        na, nb = by_id[a], by_id[b]
        if b in (na.get("links") or []) or a in (nb.get("links") or []):
            continue
        if b in (na.get("cites") or []) or a in (nb.get("cites") or []):
            continue
        out.append((a, b, s))
    out.sort(key=lambda x: -x[2])
    return out


def parse(text: str, allowed: set) -> list[dict]:
    m = re.search(r"\{.*\}", text or "", re.S)
    if not m:
        return []
    try:
        data = json.loads(m.group(0))
    except ValueError:
        return []
    out, seen = [], set()
    for l in (data.get("links") or []) if isinstance(data, dict) else []:
        if not isinstance(l, dict):
            continue
        a, b = l.get("a"), l.get("b")
        if not (isinstance(a, str) and isinstance(b, str)) or a == b or a not in allowed or b not in allowed:
            continue
        k = _key(a, b)
        if k in seen:
            continue
        seen.add(k)
        out.append({"a": a, "b": b, "why": " ".join(str(l.get("why") or "").split())[:200]})
    return out


def _context(by_id: dict, pairs: list[tuple[str, str, float]]) -> tuple[str, list[str]]:
    papers = list(dict.fromkeys(x for a, b, _ in pairs for x in (a, b)))
    room = max(200, min(1200, 60000 // max(1, len(papers))))
    rows = []
    for i in papers:
        n = by_id[i]
        text = (n.get("abstract") or "").strip() or ctx.fulltext(i)[:3000]
        text = " ".join(text.split())[:room]
        rows.append(f"[[{i}]] {n['title']}" + (f" ({n['year']})" if n.get("year") else "") + (f"\n{text}" if text else ""))
    lines = [f"{a} -- {b}" for a, b, _ in pairs]
    return "PAPERS:\n\n" + "\n\n".join(rows) + "\n\nCANDIDATE PAIRS:\n" + "\n".join(lines), papers


# ---------------------------------------------------------------- the job
def _run(job: dict) -> None:
    with _run_lock:
        if job.get("stop"):
            job["state"] = "stopped"
            return
        job["state"] = "running"
        try:
            _work(job)
            job["state"] = "stopped" if job.get("stop") else "done"
        except HTTPException as e:
            job["state"], job["message"] = "error", str(e.detail)
        except Exception as e:
            traceback.print_exc()
            job["state"], job["message"] = "error", str(e)[:300]


def _work(job: dict) -> None:
    store = ctx.store
    idx = ctx.index()
    by_id = ctx.visible(idx["by_id"])
    ids = [i for i in job["ids"] if i in by_id and by_id[i]["type"] == "paper"]
    with _cache_lock:
        cache = _load_cache()
    groups = [[i for i in g if i in by_id] for g in job.get("groups") or []]
    pairs = [p for p in candidates({**idx, "by_id": by_id}, ids, groups=groups) if _key(p[0], p[1]) not in cache]
    todo = pairs[:BATCH * MAX_BATCHES]
    job["pairs"], job["left"] = len(pairs), len(pairs) - len(todo)
    batches = [todo[i:i + BATCH] for i in range(0, len(todo), BATCH)]
    job["batches"] = len(batches)
    if not batches:
        job["message"] = "Every likely pair has been checked already." if ids else "Nothing to check."
        return
    for batch in batches:
        if job.get("stop"):
            break
        context, papers = _context(by_id, batch)
        try:
            found = parse(ctx.ask(INSTRUCTION, context, "connection analysis", papers), set(papers))
        except HTTPException:
            if not job["done_batches"]:
                raise  # no agent, or it refused: say so
            job["message"] = "The AI stopped answering part way; run it again to check the rest."
            job["left"] += sum(len(b) for b in batches[job["done_batches"]:])
            break
        made = []
        for l in found:  # AI connections, kept apart from your links: a pair you've linked already stays yours
            try:
                if l["b"] not in store.get(l["a"])["links"]:
                    made.append(l)
            except KeyError:
                continue
        now = time.time()
        with _cache_lock:
            cache = _load_cache()
            yes = {_key(l["a"], l["b"]) for l in found}
            for a, b, _ in batch:
                k = _key(a, b)
                if k not in cache or cache[k].get("v") not in (-1, 2):  # never undo your own answer
                    cache[k] = {"v": int(k in yes), "at": round(now), "new": True}
            for l in found:
                k = _key(l["a"], l["b"])
                if k not in cache or cache[k].get("v") not in (-1, 2):
                    cache[k] = {"v": 1, "at": round(now), "why": l["why"], "new": True}
            _save_cache(cache)
        REV[0] += 1
        for l in made:
            l["a_title"], l["b_title"] = by_id[l["a"]]["title"], by_id[l["b"]]["title"]
        job["links"].extend(made)
        job["linked"] += len(made)
        job["checked"] += len(batch)
        job["done_batches"] += 1
        _publish([x for l in made for x in (l["a"], l["b"])])

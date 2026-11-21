"""AI connection analysis: your AI agent reads papers that a similarity search thinks belong together and links
the pairs that really do (manual links, the same as ones you draw yourself, so they pull the papers together
on the map and show in each paper's connections).

    POST /api/links/ai-connect                {ids?, folder?, topics?, unsorted?}: start a job over those papers
    GET  /api/links/ai-connect/jobs/{job}     its progress, and the links it made
    POST /api/links/ai-connect/jobs/{job}/stop
    POST /api/links/ai-connect/jobs/{job}/undo   take back the links it made

How: within the chosen papers, each one's closest few by text similarity become candidate pairs (strongest
first). The agent gets them in batches, each with the titles and openings of the papers involved, and keeps only
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


ctx = Ctx()
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
        return start(ids)

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
        for l in j["links"]:
            try:
                if l["b"] in ctx.store.get(l["a"])["links"]:
                    ctx.store.unlink(l["a"], l["b"])
                    n += 1
            except KeyError:
                continue
        _publish([x for l in j["links"] for x in (l["a"], l["b"])])
        j["links"], j["linked"], j["undone"] = [], 0, n
        return _public(j)


def _job(jid: str) -> dict:
    j = _jobs.get(jid)
    if not j:
        raise HTTPException(404, "No such job")
    return j


def _public(j: dict) -> dict:
    return {k: v for k, v in j.items() if k != "ids"}


def _publish(ids: list[str]) -> None:
    if ctx.publish and ids:
        try:
            ctx.publish({"type": "notes-changed", "ids": list(dict.fromkeys(ids))[:500]})
        except Exception:
            pass


def start(ids: list[str]) -> dict:
    jid = uuid.uuid4().hex[:10]
    job = {"id": jid, "state": "waiting", "papers": len(ids), "pairs": 0, "checked": 0, "left": 0, "linked": 0,
           "batches": 0, "done_batches": 0, "links": [], "message": "", "started": time.time(), "ids": list(ids)}
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
def candidates(idx: dict, ids: list[str], per: int = PER_PAPER) -> list[tuple[str, str, float]]:
    """Each paper's `per` most similar papers among `ids` (text embeddings), as pairs, strongest first. Pairs
    already joined (a link, a citation) are left out."""
    by_id, m = idx["by_id"], idx.get("m")
    if m is None:
        return []
    pos = {i: r for r, i in enumerate(idx["ids"])}
    ids = [i for i in ids if i in pos][:MAX_POOL]
    if len(ids) < 2:
        return []
    sub = np.asarray(m)[[pos[i] for i in ids]]
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
    pairs = [p for p in candidates({**idx, "by_id": by_id}, ids) if _key(p[0], p[1]) not in cache]
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
        for l in found:
            try:
                if l["b"] not in store.get(l["a"])["links"]:
                    store.link(l["a"], l["b"])
                    made.append(l)
            except (KeyError, ValueError):
                continue
        now = time.time()
        with _cache_lock:
            cache = _load_cache()
            yes = {_key(l["a"], l["b"]) for l in found}
            for a, b, _ in batch:
                cache[_key(a, b)] = {"v": int(_key(a, b) in yes), "at": round(now)}
            for l in found:
                cache[_key(l["a"], l["b"])] = {"v": 1, "at": round(now), "why": l["why"]}
            _save_cache(cache)
        for l in made:
            l["a_title"], l["b_title"] = by_id[l["a"]]["title"], by_id[l["b"]]["title"]
        job["links"].extend(made)
        job["linked"] += len(made)
        job["checked"] += len(batch)
        job["done_batches"] += 1
        _publish([x for l in made for x in (l["a"], l["b"])])

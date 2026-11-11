"""Open questions: the user's questions as notes that keep gathering evidence as new material arrives.

A question is a note with `type: question` in vault/notes/ (so it has wikilinks, backlinks, search, the graph,
topics and the privacy flag like any note), plus three properties:

    question_status   open | answered | parked
    answer            the user's current answer or thinking (Markdown + LaTeX)
    last_seen         when the user last looked at its evidence ("N new since you last looked")

Evidence is the user's triage decisions, which can't be derived again, so it lives with the user's other state
in vault/state/questions/<question id>.json (backed up with the vault):

    {"items": [{"id", "key", "kind": passage|card|note|paper,
                "ref": {"note": id, "page"?: N, "section"?: "3.2", "card"?: id, "parent"?: id},
                "score", "found_at", "verdict": new|supports|against|related|dismissed,
                "user_note", "snippet", "title"}, ...]}

Matching is local only: nothing about a question is ever sent to an AI agent by this module.
    candidates    the passage index (app/chunks.py: BM25 + embedding hybrid over paper text, abstracts, the
                  user's summaries, notes and fragments; questions themselves are never indexed), whole-note
                  similarity from the library index (app/embed.py), and idea cards (app/cards.py search and
                  for_paper) when that module exists.
    score         cosine similarity in the passage index's space (ChunkIndex.similarity): the TF-IDF
                  vocabulary of the passages, or the sentence-transformers model. One threshold per embedder
                  kind (THRESHOLDS; settings "questions" in the app settings file).
    calm          a candidate must clear the threshold, is deduplicated against every item already stored (any
                  verdict), never comes back once dismissed (dismissing a passage blocks its page/section,
                  dismissing a note, paper or card blocks that source), at most PER_SOURCE new items come from
                  one source per scan, and at most `daily_cap` (default 10) new items per question per day.

The Matcher runs scans in a background thread: debounced after any note save/delete (Store.listen), any live
event (notes changed outside the app, metadata, idea cards), and a slow poll; a question is skipped when
neither it, the library, the cards, the settings nor the day changed since its last scan.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import shutil
import threading
import time
from pathlib import Path

VERDICTS = ("new", "supports", "against", "related", "dismissed")
KEPT = ("supports", "against", "related")
KINDS = ("passage", "card", "note", "paper")
# TF-IDF: calibrated on tests/fixtures/questions/plasma_corpus.json, where relevant passages score 0.15-0.29
# and the best unrelated one 0.08 (tests/test_questions.py keeps that margin). The dense value is for
# all-MiniLM-L6-v2, whose cosine for a relevant passage vs. a short question is typically 0.45-0.7 and
# for unrelated text below 0.25; raise it in settings if evidence is noisy.
THRESHOLDS = {"tfidf": 0.12, "dense": 0.40}
DAILY_CAP = 10
PER_SOURCE = 2          # new passages from one paper per scan
CANDIDATES = 40         # passages pulled from the hybrid search per question
NOTE_CANDIDATES = 8     # whole notes pulled from the library index
SNIPPET = 600
DEBOUNCE = float(os.environ.get("RB_QUESTIONS_DEBOUNCE", "2.0"))
POLL = float(os.environ.get("RB_QUESTIONS_POLL", "30"))
REV = [0]               # bumped whenever stored evidence changes (part of the /api/graph cache key)

_lock = threading.RLock()
_ID_RE = re.compile(r"[a-z0-9][a-z0-9-]*")


class QuestionError(ValueError):
    pass


# ---------------------------------------------------------------- time
def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def _parse(s) -> dt.datetime | None:
    try:
        t = dt.datetime.fromisoformat(str(s))
        return t if t.tzinfo else t.astimezone()
    except (TypeError, ValueError):
        return None


def _today() -> str:
    return dt.datetime.now().astimezone().date().isoformat()


# ---------------------------------------------------------------- settings
def get_settings() -> dict:
    """{"threshold_tfidf", "threshold_dense", "daily_cap"} from the app settings file (defaults filled in)."""
    try:
        from . import paths
        raw = paths.load_settings().get("questions") or {}
    except Exception:
        raw = {}
    out = {"threshold_tfidf": THRESHOLDS["tfidf"], "threshold_dense": THRESHOLDS["dense"], "daily_cap": DAILY_CAP}
    for k in ("threshold_tfidf", "threshold_dense"):
        try:
            v = float(raw.get(k))
            if 0.0 < v < 1.0:
                out[k] = v
        except (TypeError, ValueError):
            pass
    try:
        c = int(raw.get("daily_cap"))
        if 1 <= c <= 100:
            out["daily_cap"] = c
    except (TypeError, ValueError):
        pass
    return out


def save_settings(updates: dict) -> dict:
    cur = get_settings()
    for k in ("threshold_tfidf", "threshold_dense"):
        if updates.get(k) is not None:
            try:
                v = float(updates[k])
            except (TypeError, ValueError):
                raise QuestionError(f"{k} must be a number between 0 and 1")
            if not 0.0 < v < 1.0:
                raise QuestionError(f"{k} must be between 0 and 1")
            cur[k] = round(v, 3)
    if updates.get("daily_cap") is not None:
        try:
            c = int(updates["daily_cap"])
        except (TypeError, ValueError):
            raise QuestionError("daily_cap must be a whole number")
        if not 1 <= c <= 100:
            raise QuestionError("daily_cap must be between 1 and 100")
        cur["daily_cap"] = c
    from . import paths
    paths.save_settings({"questions": cur})
    return get_settings()


def embedder_family(kind: str) -> str:
    return "tfidf" if (kind or "tfidf") == "tfidf" else "dense"


def threshold_for(kind: str, settings: dict | None = None) -> float:
    s = settings or get_settings()
    return s["threshold_tfidf"] if embedder_family(kind) == "tfidf" else s["threshold_dense"]


# ---------------------------------------------------------------- storage
def qdir(store) -> Path:
    d = Path(store.root) / "state" / "questions"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _path(store, qid: str) -> Path:
    if not _ID_RE.fullmatch(qid or ""):
        raise KeyError(qid)
    return qdir(store) / f"{qid}.json"


def load(store, qid: str) -> dict:
    p = _path(store, qid)
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    items = [i for i in (data.get("items") or []) if isinstance(i, dict) and i.get("id") and i.get("key")]
    for i in items:
        if i.get("verdict") not in VERDICTS:
            i["verdict"] = "new"
        i.setdefault("user_note", "")
    data["items"] = items
    return data


def _save(store, qid: str, data: dict) -> None:
    p = _path(store, qid)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), encoding="utf-8")
    tmp.replace(p)
    REV[0] += 1


def drop(store, qid: str) -> None:
    """The question was deleted: move its evidence to the vault's .trash with the note."""
    try:
        p = _path(store, qid)
    except KeyError:
        return
    if not p.exists():
        return
    with _lock:
        dest = Path(store.trash_dir) / f"question-evidence-{qid}-{dt.datetime.now().strftime('%Y%m%d-%H%M%S')}.json"
        try:
            shutil.move(str(p), dest)
        except OSError:
            pass
        REV[0] += 1


def is_question(n: dict | None) -> bool:
    return bool(n) and n.get("type") == "question"


# ---------------------------------------------------------------- privacy
def ai_allowed(question: dict, agent=None) -> tuple[bool, str]:
    """(may an AI agent see this question?, the reason when it may not). Questions are private by default;
    without the privacy module (app/privacy.py) they never go to an agent."""
    try:
        from . import privacy
    except Exception:
        return False, "Questions stay on this computer."
    try:
        if agent is None:
            from . import agents
            agent = agents.active()
    except Exception:
        agent = None
    try:
        if privacy.allow_ai(question, agent):
            return True, ""
    except Exception:
        return False, "Questions stay on this computer."
    return False, ("This question is private, so it stays on this computer and no AI agent sees it. "
                   "Matching evidence is done locally. Mark the question shareable to use AI features on it.")


def source_allowed(n: dict | None, agent=None) -> bool:
    """May this evidence source go to the AI agent (for the optional summary)? Unknown sources: no."""
    if not n:
        return False
    try:
        from . import privacy
        if agent is None:
            from . import agents
            agent = agents.active()
        return bool(privacy.allow_ai(n, agent))
    except Exception:
        return False


# ---------------------------------------------------------------- matching (local only)
_LINK_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]*))?\]\]")


def question_text(q: dict) -> str:
    """What the question is about: its title and its elaboration (the body). [[links]] become their words."""
    def plain(s: str) -> str:
        return _LINK_RE.sub(lambda m: m.group(2) or m.group(1), s or "")
    parts = [plain(q.get("title") or ""), plain(q.get("body") or "")]
    if q.get("tags"):
        parts.append(" ".join(q["tags"]))
    return "\n\n".join(p.strip() for p in parts if p and p.strip())


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip()


_LABEL_RE = re.compile(r"^(Abstract|Student's summary|Student's notes|Student's note|Quoted passage):\s*")


def _snippet(text: str) -> str:
    """A passage as the user reads it: without the labels the passage index puts in front for the model."""
    return _LABEL_RE.sub("", _norm(text))[:SNIPPET]


_WORD_RE = re.compile(r"[a-z0-9][a-z0-9_]*")
MIN_OVERLAP = 3   # TF-IDF only: distinct question words (stemmed) a candidate must share


def _stems(text: str) -> set[str]:
    """Content words of `text`, crudely stemmed (first 5 letters), so "shielded" meets "shielding"."""
    try:
        from sklearn.feature_extraction.text import ENGLISH_STOP_WORDS as stop
    except Exception:  # pragma: no cover
        stop = frozenset()
    out = set()
    for w in _WORD_RE.findall((text or "").lower()):
        if w in stop or w.isdigit() or len(w) < 2:
            continue
        out.add(w[:5] if len(w) > 5 else w)
    return out


def overlap(q_stems: set[str], text: str) -> int:
    return len(q_stems & _stems(text))


def _h(s: str) -> str:
    return hashlib.sha1(_norm(s).lower()[:300].encode()).hexdigest()[:10]


def _loc(ref: dict) -> str:
    return f"{ref.get('note', '')}:{ref.get('page') or ''}:{ref.get('section') or ''}"


def ev_key(kind: str, ref: dict, text: str = "") -> str:
    if kind == "passage":
        return f"passage:{_loc(ref)}:{_h(text)}"
    if kind == "card":
        return f"card:{ref.get('card', '')}"
    return f"{kind}:{ref.get('note', '')}"


def _eid(key: str) -> str:
    return hashlib.sha1(key.encode()).hexdigest()[:12]


def _blocks(items: list[dict]) -> set[str]:
    """What dismissed items keep out: a passage blocks its page/section; a note, paper or card its source."""
    out: set[str] = set()
    for i in items:
        if i.get("verdict") != "dismissed":
            continue
        ref = i.get("ref") or {}
        if i.get("kind") == "passage":
            out.add("loc:" + _loc(ref))
        elif i.get("kind") == "card":
            out.add("card:" + str(ref.get("card", "")))
        else:
            out.add("src:" + str(ref.get("note", "")))
    return out


def _blocked(c: dict, blocks: set[str]) -> bool:
    ref = c["ref"]
    if c["kind"] == "passage" and "loc:" + _loc(ref) in blocks:
        return True
    if c["kind"] == "card" and "card:" + str(ref.get("card", "")) in blocks:
        return True
    src = str(ref.get("note", ""))
    return bool(src) and ("src:" + src in blocks or "src:" + str(ref.get("parent", "")) in blocks)


def _cards():
    try:
        from . import cards
        return cards
    except Exception:
        return None


def _card_fields(c) -> dict | None:
    """A card from app/cards.py as {id, text, paper, page}, whatever exact shape it has."""
    if not isinstance(c, dict):
        return None
    card = c.get("card") if isinstance(c.get("card"), dict) else c
    cid = str(card.get("id") or c.get("id") or "")
    text = ""
    for k in ("text", "claim", "statement", "title", "summary", "body"):
        if isinstance(card.get(k), str) and card[k].strip():
            text = card[k].strip()
            break
    if not cid or not text:
        return None
    paper = str(card.get("paper_id") or card.get("paper") or card.get("parent") or card.get("note") or "")
    loc = card.get("locator") if isinstance(card.get("locator"), dict) else {}
    page = card.get("page") if card.get("page") is not None else loc.get("page")
    section = str(loc.get("section") or "")
    if card.get("latex") and isinstance(card["latex"], str):
        text = f"{text} ${card['latex'].strip()}$"
    return {"id": cid, "text": text, "paper": paper, "page": page if isinstance(page, int) else None,
            "section": section, "kind_label": str(card.get("kind") or card.get("type") or "")}


def _card_search(text: str, k: int = 10) -> list[dict]:
    mod = _cards()
    if mod is None or not hasattr(mod, "search"):
        return []
    try:
        res = mod.search(text, k)
    except Exception:
        return []
    return [x for x in (_card_fields(c) for c in (res or [])) if x]


def _cards_for_paper(pid: str) -> list[dict]:
    mod = _cards()
    if mod is None or not hasattr(mod, "for_paper"):
        return []
    try:
        res = mod.for_paper(pid, confirmed_only=True)
    except Exception:
        return []
    return [x for x in (_card_fields(c) for c in (res or [])) if x]


def cards_signature(store) -> str:
    """Changes when idea cards change (the module's own signature(), else the card files' mtimes)."""
    mod = _cards()
    if mod is not None and hasattr(mod, "signature"):
        try:
            return "m:" + str(mod.signature())
        except Exception:
            pass
    root = Path(store.root)
    parts = []
    for d in (root / "cards", root / "state" / "cards"):
        if d.is_dir():
            for p in sorted(d.rglob("*")):
                try:
                    st = p.stat()
                    parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
                except OSError:
                    pass
    for p in sorted((root / "state").glob("cards*")) if (root / "state").is_dir() else []:
        try:
            st = p.stat()
            parts.append(f"{p.name}:{st.st_mtime_ns}:{st.st_size}")
        except OSError:
            pass
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


def library_signature(notes: list[dict]) -> str:
    """Changes when any source a question could match changes (questions themselves don't count)."""
    h = hashlib.sha1()
    for n in sorted(notes, key=lambda n: n["id"]):
        if n.get("type") == "question":
            continue
        h.update(f"{n['id']}|{n.get('updated') or ''}|{int(bool(n.get('has_text')))}\n".encode())
    return h.hexdigest()[:16]


class Context:
    """What matching needs from the server: the store, the passage index, the embedder and the note index."""

    def __init__(self, store, chunk_index, embedder, index_fn, note_text=None):
        self.store, self.chunk_index, self.embedder, self.index_fn = store, chunk_index, embedder, index_fn
        self.note_text = note_text


def find_candidates(ctx: Context, q: dict, notes: list[dict] | None = None) -> list[dict]:
    """Scored candidates for question q: [{kind, ref, score, text, title}], best first. Local only."""
    text = question_text(q)
    if not text.strip():
        return []
    notes = ctx.store.all() if notes is None else notes
    by_id = {n["id"]: n for n in notes}
    out: dict[str, dict] = {}
    passage_srcs: set[str] = set()

    def add(c: dict) -> None:
        key = ev_key(c["kind"], c["ref"], c.get("text", ""))
        c["key"] = key
        if key not in out or out[key]["score"] < c["score"]:
            out[key] = c

    # 1) hybrid passage search (BM25 + embeddings), scored by absolute cosine
    try:
        hits = ctx.chunk_index.search(text, k=CANDIDATES, per_note=PER_SOURCE + 1, notes=notes)
    except Exception as e:
        print(f"[research_brain] question matching: passage search failed: {e!r}")
        hits = []
    for p in hits:
        n = by_id.get(p["id"])
        if not n or is_question(n) or n["id"] == q["id"]:
            continue
        score = float(p.get("sim", 0.0))
        if n["type"] == "fragment":
            parent = by_id.get(n.get("parent") or "")
            if parent is not None and is_question(parent):
                continue
            c = {"kind": "note", "ref": {"note": n["id"], "parent": n.get("parent") or "", "page": n.get("page")},
                 "score": score, "text": p["text"], "title": n["title"]}
        elif n["type"] == "paper" and p.get("src") == "text":
            ref = {"note": n["id"], "page": p.get("page")}
            if p.get("section"):
                ref["section"] = p["section"]
            c = {"kind": "passage", "ref": ref, "score": score, "text": p["text"], "title": n["title"]}
        elif n["type"] == "paper":
            c = {"kind": "paper", "ref": {"note": n["id"]}, "score": score, "text": p["text"], "title": n["title"]}
        else:
            c = {"kind": "note", "ref": {"note": n["id"]}, "score": score, "text": p["text"], "title": n["title"]}
        passage_srcs.add(n["id"])
        add(c)

    # 2) whole-note similarity (catches notes whose writing is spread over many short chunks)
    try:
        idx = ctx.index_fn()
        m, ids = idx.get("m"), idx.get("ids") or []
        if m is not None and len(ids):
            qv = ctx.embedder.embed_query(text)
            if qv is not None and qv.shape[0] == m.shape[1]:
                import numpy as np
                sims = m @ qv
                order = [int(i) for i in np.argsort(-sims)]
                picked = []
                for i in order:
                    nid = ids[i]
                    n = by_id.get(nid)
                    if not n or is_question(n) or nid == q["id"] or nid in passage_srcs or sims[i] <= 0:
                        continue
                    if n["type"] == "fragment" and is_question(by_id.get(n.get("parent") or "")):
                        continue
                    picked.append(n)
                    if len(picked) >= NOTE_CANDIDATES:
                        break
                if picked:
                    from .embed import note_text
                    texts = [note_text(n)[:3000] for n in picked]
                    scores = ctx.chunk_index.similarity(text, texts)
                    for n, t, s in zip(picked, texts, scores):
                        kind = "paper" if n["type"] == "paper" else "note"
                        ref = {"note": n["id"]}
                        if n["type"] == "fragment":
                            ref.update(parent=n.get("parent") or "", page=n.get("page"))
                        add({"kind": kind, "ref": ref, "score": s, "text": t, "title": n["title"]})
    except Exception as e:
        print(f"[research_brain] question matching: note similarity failed: {e!r}")

    # 3) idea cards (app/cards.py), if that feature is installed
    cands = _card_search(text, 10)
    seen_papers = [pid for pid in dict.fromkeys(c["ref"]["note"] for c in out.values() if c["kind"] in ("passage", "paper"))][:6]
    for pid in seen_papers:
        cands += _cards_for_paper(pid)
    uniq = {c["id"]: c for c in cands}
    if uniq:
        cl = list(uniq.values())
        try:
            scores = ctx.chunk_index.similarity(text, [c["text"] for c in cl])
        except Exception:
            scores = [0.0] * len(cl)
        for c, s in zip(cl, scores):
            if c["paper"] and is_question(by_id.get(c["paper"])):
                continue
            ref = {"card": c["id"], "note": c["paper"]}
            if c.get("page"):
                ref["page"] = c["page"]
            elif c.get("section"):
                ref["section"] = c["section"]
            title = by_id[c["paper"]]["title"] if c["paper"] in by_id else ""
            add({"kind": "card", "ref": ref, "score": s, "text": c["text"], "title": title})
    return sorted(out.values(), key=lambda c: -c["score"])


def scan(ctx: Context, qid: str, notes: list[dict] | None = None, settings: dict | None = None) -> list[dict]:
    """Look for new evidence for one question now. Returns the items added (verdict "new")."""
    q = ctx.store.get(qid)
    if not is_question(q):
        raise KeyError(qid)
    settings = settings or get_settings()
    thr = threshold_for(ctx.embedder.kind, settings)
    cands = [c for c in find_candidates(ctx, q, notes) if c["score"] >= thr]
    if embedder_family(ctx.embedder.kind) == "tfidf":
        # TF-IDF weights are noisy in a small library (one shared rare word can score high), so a match must
        # also share a few of the question's words. Sentence embeddings match meaning, so they don't need it.
        qs = _stems(question_text(q))
        need = min(MIN_OVERLAP, max(1, len(qs)))
        cands = [c for c in cands if overlap(qs, c.get("text", "")) >= need]
    if not cands:
        return []
    with _lock:
        data = load(ctx.store, qid)
        items = data["items"]
        have = {i["key"] for i in items}
        have_src = {(i.get("ref") or {}).get("note") for i in items if i.get("kind") in ("passage", "card")}
        blocks = _blocks(items)
        today = _today()
        added_today = sum(1 for i in items if str(i.get("found_at") or "")[:10] == today)
        room = settings["daily_cap"] - added_today
        per_src: dict[str, int] = {}
        specific = {str(c["ref"].get("note") or "") for c in cands if c["kind"] in ("passage", "card")}
        added = []
        for c in cands:
            if room <= 0:
                break
            if c["key"] in have or _blocked(c, blocks):
                continue
            src = str(c["ref"].get("note") or c["ref"].get("card") or "")
            if c["kind"] in ("note", "paper") and (src in have_src or src in specific):
                continue  # a passage or card from this source is (or is about to be) evidence: more specific
            if per_src.get(src, 0) >= PER_SOURCE:
                continue
            per_src[src] = per_src.get(src, 0) + 1
            item = {"id": _eid(c["key"]), "key": c["key"], "kind": c["kind"], "ref": c["ref"],
                    "score": round(float(c["score"]), 4), "found_at": now_iso(), "verdict": "new", "user_note": "",
                    "snippet": _snippet(c.get("text", "")), "title": c.get("title") or ""}
            items.append(item)
            have.add(c["key"])
            added.append(item)
            room -= 1
        if added:
            _save(ctx.store, qid, data)
        return added


# ---------------------------------------------------------------- triage and counts
def set_verdict(store, qid: str, eid: str, verdict: str | None = None, user_note: str | None = None) -> dict:
    if verdict is not None and verdict not in VERDICTS:
        raise QuestionError(f"verdict must be one of {', '.join(VERDICTS)}")
    with _lock:
        data = load(store, qid)
        for i in data["items"]:
            if i["id"] == eid:
                if verdict is not None:
                    i["verdict"] = verdict
                    i["decided_at"] = now_iso()
                if user_note is not None:
                    i["user_note"] = str(user_note)[:2000]
                _save(store, qid, data)
                return dict(i)
    raise KeyError(eid)


def counts(q: dict, data: dict) -> dict:
    """{new (since last seen), untriaged, supports, against, related, dismissed, total}."""
    seen = _parse(q.get("last_seen")) if q.get("last_seen") else None
    c = {v: 0 for v in VERDICTS}
    since = 0
    for i in data["items"]:
        c[i["verdict"]] += 1
        if i["verdict"] == "new":
            t = _parse(i.get("found_at"))
            if seen is None or (t is not None and t > seen):
                since += 1
    return {"new": since, "untriaged": c["new"], "supports": c["supports"], "against": c["against"],
            "related": c["related"], "dismissed": c["dismissed"], "total": len(data["items"])}


def is_new(q: dict, item: dict) -> bool:
    if item.get("verdict") != "new":
        return False
    seen = _parse(q.get("last_seen")) if q.get("last_seen") else None
    t = _parse(item.get("found_at"))
    return seen is None or (t is not None and t > seen)


def mark_seen(store, qid: str) -> dict:
    with store.lock:
        n = store.get(qid)
        if not is_question(n):
            raise KeyError(qid)
        n["last_seen"] = now_iso()
        saved = store.save(n, touch=False)  # looking isn't an edit
    REV[0] += 1
    return saved


def list_questions(store, notes: list[dict] | None = None) -> list[dict]:
    notes = store.all() if notes is None else notes
    out = []
    for n in notes:
        if not is_question(n):
            continue
        data = load(store, n["id"])
        latest = max((str(i.get("found_at") or "") for i in data["items"]), default="")
        c = counts(n, data)
        out.append({"id": n["id"], "title": n["title"], "question_status": n.get("question_status") or "open",
                    "updated": n.get("updated") or "", "created": n.get("created") or "", "last_seen": n.get("last_seen") or "",
                    "answered": bool((n.get("answer") or "").strip()), "tags": n.get("tags") or [],
                    "counts": c, "new_evidence": c["new"], "latest_evidence": latest})
    order = {"open": 0, "parked": 1, "answered": 2}
    # open first, then parked, then answered; within each, most new evidence first, then most recently edited
    out.sort(key=lambda x: (order.get(x["question_status"], 3), -x["new_evidence"], _neg_time(x["updated"])))
    return out


def _neg_time(s: str) -> float:
    t = _parse(s)
    return -t.timestamp() if t else 0.0


def graph_extras(store, by_id: dict) -> tuple[dict, list[dict]]:
    """({question id: new evidence count}, question_links [{q, paper, verdict}]) for /api/graph. A link goes
    from a question to the paper (or note) its kept evidence comes from: a fragment's or card's paper."""
    new: dict[str, int] = {}
    links: list[dict] = []
    seen: set = set()
    for qid, q in by_id.items():
        if not is_question(q):
            continue
        data = load(store, qid)
        new[qid] = counts(q, data)["new"]
        for i in data["items"]:
            if i["verdict"] not in KEPT:
                continue
            ref = i.get("ref") or {}
            target = ref.get("note") or ""
            t = by_id.get(target)
            if t is not None and t.get("type") == "fragment" and t.get("parent") in by_id:
                target = t["parent"]
            if not target or target not in by_id or target == qid:
                continue
            k = (qid, target, i["verdict"])
            if k in seen:
                continue
            seen.add(k)
            links.append({"q": qid, "paper": target, "verdict": i["verdict"]})
    return new, links


# ---------------------------------------------------------------- the background matcher
class Matcher:
    """Debounced background scans of every open question. poke() from anywhere; never blocks the caller."""

    def __init__(self, ctx: Context, debounce: float = DEBOUNCE, poll: float = POLL):
        self.ctx, self.debounce, self.poll = ctx, debounce, poll
        self._wake = threading.Event()
        self._idle = threading.Event()
        self._idle.set()
        self._due = 0.0
        self._force: set[str] = set()
        self._done: dict[str, tuple] = {}
        self._thread: threading.Thread | None = None
        self._stop = False
        self._tlock = threading.Lock()
        self.runs = 0
        self.last_error = ""
        self.enabled = os.environ.get("RB_QUESTIONS_MATCH", "1") != "0"

    # -- triggers
    def poke(self, qid: str | None = None, force: bool = False, delay: float | None = None) -> None:
        if not self.enabled:
            return
        with self._tlock:
            if qid and force:
                self._force.add(qid)
            self._due = time.monotonic() + (self.debounce if delay is None else delay)
            self._idle.clear()
        self._ensure_thread()
        self._wake.set()

    def on_store(self, kind: str, nid: str) -> None:
        if kind == "deleted":
            drop(self.ctx.store, nid)
            self._done.pop(nid, None)
        self.poke()

    def on_event(self, ev: dict) -> None:
        t = str((ev or {}).get("type") or "")
        if t == "notes-changed" or t.startswith("card") or t.startswith("metadata"):
            self.poke()

    # -- thread
    def _ensure_thread(self) -> None:
        with self._tlock:
            if self._thread is not None and self._thread.is_alive():
                return
            self._stop = False
            self._thread = threading.Thread(target=self._loop, name="rb-questions", daemon=True)
            self._thread.start()

    def start(self) -> None:
        if self.enabled:
            self._ensure_thread()
            self.poke(delay=self.debounce)

    def stop(self) -> None:
        self._stop = True
        self._wake.set()

    def _loop(self) -> None:
        while not self._stop:
            with self._tlock:
                due = self._due
            now = time.monotonic()
            if due and now < due:  # debouncing: wait until things go quiet
                self._wake.wait(timeout=due - now)
                self._wake.clear()
                continue
            if not due:  # nothing asked: the slow poll catches what nobody told us (a PDF's text appearing)
                woke = self._wake.wait(timeout=self.poll)
                self._wake.clear()
                if self._stop:
                    break
                if woke:
                    continue
            with self._tlock:
                self._due = 0.0
                force, self._force = self._force, set()
                self._idle.clear()
            try:
                self.run_all(force=force)
            except Exception as e:
                self.last_error = repr(e)
                print(f"[research_brain] question matching failed: {e!r}")
            finally:
                with self._tlock:
                    if not self._due:
                        self._idle.set()

    def wait_idle(self, timeout: float = 30.0) -> bool:
        """Block until no scan is pending or running (tests)."""
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if self._idle.wait(timeout=0.05):
                with self._tlock:
                    if not self._due:
                        return True
        return False

    # -- the work
    def run_all(self, force: set[str] | None = None) -> dict[str, int]:
        """Scan every open question whose inputs changed (or that is in `force`). {question id: items added}."""
        force = force or set()
        store = self.ctx.store
        notes = store.all()
        lib = library_signature(notes)
        cards = cards_signature(store)
        settings = get_settings()
        key_common = (lib, cards, settings["daily_cap"], threshold_for(self.ctx.embedder.kind, settings),
                      self.ctx.embedder.kind, _today())
        out = {}
        self.runs += 1
        for q in notes:
            if not is_question(q):
                continue
            if (q.get("question_status") or "open") != "open" and q["id"] not in force:
                continue
            key = key_common + (_h(question_text(q)) + str(len(question_text(q))),)
            if self._done.get(q["id"]) == key and q["id"] not in force:
                continue
            try:
                added = scan(self.ctx, q["id"], notes, settings)
            except KeyError:
                continue
            self._done[q["id"]] = key
            out[q["id"]] = len(added)
        return out

    def rescan(self, qid: str) -> list[dict]:
        """Scan one question now, in the caller's thread (POST /api/questions/{id}/rescan)."""
        added = scan(self.ctx, qid)
        self._done.pop(qid, None)
        return added

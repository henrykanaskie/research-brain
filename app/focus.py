"""What in this paper matters for a question, a topic or a phrase: the "read only what's useful" view.

The viewer's Focus tab asks for the passages of one paper that bear on something you care about: an open
question, a topic, or words you type. Each of the paper's passages (paragraph-sized, never crossing a page)
is scored against it with the library's local search (BM25-style TF-IDF, or the sentence-embedding model
when it's installed), the best ones are kept, and inside each the sentence or two that carry it are picked
(app/locate.py), so the viewer can mark them on the page. Nothing leaves the computer and no AI is used, so
this works for private questions and private papers alike.
"""
from __future__ import annotations

import math

from . import chunks, locate

FLOOR = {"tfidf": 0.06, "dense": 0.25}   # below this a passage isn't about it at all
RELATIVE = 0.45                          # and it must score at least this share of the best passage


def focus_text_question(q: dict) -> str:
    return "\n".join(x for x in (q.get("title"), q.get("body")) if x)


def focus_text_topic(t: dict, notes_by_id: dict) -> str:
    members = [notes_by_id[m]["title"] for m in t.get("members") or [] if m in notes_by_id][:30]
    return "\n".join([t.get("name") or "", t.get("description") or "", *members])


def _quote(passage: str, query: str) -> str:
    sents = locate.sentences(passage)
    if not sents:
        return passage[:300]
    ct = locate.terms(query)
    df: dict[str, int] = {}
    for s in sents:
        for t in set(locate.terms(s)):
            df[t] = df.get(t, 0) + 1
    idf = {t: math.log(1 + len(sents) / (1 + d)) for t, d in df.items()}
    score, text = locate._best(ct, sents, idf)
    return locate._quote(text if score > 0 else sents[0], ct)


def relevant(note: dict, fulltext: str, query: str, index, family: str, k: int = 8) -> list[dict]:
    """[{page, score, text, quote, section?}] best first, for passages of the paper's own text."""
    rows = [r for r in chunks.chunk_note(note, fulltext) if r["src"] == "text" and r.get("page")]
    if not rows or not (query or "").strip():
        return []
    sims = index.similarity(query, [r["text"] for r in rows])
    best = max(sims) if sims else 0.0
    floor = max(FLOOR["dense" if family != "tfidf" else "tfidf"], best * RELATIVE)
    ranked = sorted(((s, r) for s, r in zip(sims, rows) if s >= floor), key=lambda x: -x[0])[:k]
    out = []
    for s, r in ranked:
        text = chunks.strip_markers(r["text"])
        row = {"page": r["page"], "score": round(float(s), 3), "text": text[:900], "quote": _quote(text, query)}
        if r.get("section"):
            row["section"] = r["section"]
        out.append(row)
    return out

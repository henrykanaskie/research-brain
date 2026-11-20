"""Conversations with the AI agent, remembered between sessions.

One file per conversation in <library>/state/chats/<key>.json, where the key says what it is about:
`library` (the whole library), `general` (anything, no library), `paper-<id>` (one paper) or `topics-<slug>+<slug>` (a group of topics).

Kept small on purpose:
- only the last KEEP messages are stored in full (and each is cut to MSG_CHARS); older ones are folded into
  a short running summary (the question, and the start of the answer), at most SUMMARY_CHARS;
- the summary goes to the agent with the recent messages, so a long conversation keeps its thread without
  resending everything (app/budget.py still trims the recent messages);
- all conversations together stay under TOTAL_CHARS and MAX_THREADS: the least recently used go first.
Nothing here is sent anywhere; the files stay on this computer with the library.
"""
from __future__ import annotations

from . import fsio

import json
import re
import threading
import time
from pathlib import Path

KEEP = 40
MSG_CHARS = 40_000  # a long answer is kept whole (the agent rereads it when you go on)
SUMMARY_CHARS = 4000
TOTAL_CHARS = 3_000_000
MAX_THREADS = 150
# a key may end in ~<stamp>: an earlier conversation, kept when you start a new one (Past chats)
KEY_RE = re.compile(r"(?:library|general|paper-[a-z0-9][a-z0-9-]{0,160}|topics-[a-z0-9][a-z0-9-]{0,80}(\+[a-z0-9][a-z0-9-]{0,80}){0,7})"
                    r"(?:~[a-z0-9]{4,20})?")
_lock = threading.Lock()


class ChatError(ValueError):
    pass


def _dir(store) -> Path:
    d = Path(store.root) / "state" / "chats"
    d.mkdir(parents=True, exist_ok=True)
    return d


def valid_key(key: str) -> str:
    key = (key or "").strip()
    if not KEY_RE.fullmatch(key):
        raise ChatError("Not a conversation name")
    return key


def _path(store, key: str) -> Path:
    return _dir(store) / f"{valid_key(key)}.json"


def _clean(m: dict) -> dict | None:
    if not isinstance(m, dict) or m.get("role") not in ("user", "assistant"):
        return None
    text = str(m.get("content") or "")
    if not text.strip():
        return None
    out = {"role": m["role"], "content": text if len(text) <= MSG_CHARS else text[:MSG_CHARS].rsplit(" ", 1)[0] + " […]"}
    if isinstance(m.get("sources"), list):  # just what the answer cited: ids and titles, not the passages
        out["sources"] = [{"id": str(s.get("id", ""))[:200], "title": str(s.get("title", ""))[:300]}
                          for s in m["sources"][:12] if isinstance(s, dict) and s.get("id")]
    if isinstance(m.get("sel"), dict):
        out["sel"] = {"page": m["sel"].get("page"), "text": str(m["sel"].get("text", ""))[:500]}
    if isinstance(m.get("novelty"), str) and re.fullmatch(r"[0-9a-f]{6,40}", m["novelty"]):  # a novelty check's run (app/novelty.py)
        out["novelty"] = m["novelty"]
    if m.get("cut") is True:  # voice mode: you talked over it, so the answer stops there
        out["cut"] = True
    if isinstance(m.get("remembered"), list):  # what the answer saved to Memory › About you
        out["remembered"] = [str(x)[:200] for x in m["remembered"][:2] if isinstance(x, str)]
    if isinstance(m.get("papers"), list):  # a web answer's paper cards (app/finder.py from_answer)
        keep = []
        for it in m["papers"][:12]:
            if not isinstance(it, dict) or not it.get("id"):
                continue
            p = it.get("paper") if isinstance(it.get("paper"), dict) else {}
            keep.append({"id": str(it["id"])[:80], "key": str(it.get("key", ""))[:300], "search": str(it.get("search", ""))[:80],
                         "title": str(it.get("title", ""))[:400], "why": str(it.get("why", ""))[:300],
                         "status": "dismissed" if it.get("status") == "dismissed" else "deck",
                         "library_id": str(it.get("library_id", ""))[:200],
                         "paper": {k: (str(p.get(k))[:1500] if k == "abstract" else p.get(k)) for k in
                                   ("year", "authors", "venue", "access", "doi", "arxiv", "url", "abstract") if p.get(k) is not None}})
        if keep:
            out["papers"] = keep
    return out


def _gist(text: str, n: int) -> str:
    t = " ".join(str(text).split())
    if len(t) <= n:
        return t
    cut = t[:n]
    end = max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! "))
    return (cut[:end + 1] if end > n * 0.5 else cut.rsplit(" ", 1)[0]) + " …"


def fold(summary: str, dropped: list[dict]) -> str:
    """The running summary with `dropped` (oldest first) added: each question and the start of its answer.
    The oldest lines go first when it is over SUMMARY_CHARS."""
    lines = [ln for ln in (summary or "").splitlines() if ln.strip()]
    q = None
    for m in dropped:
        if m["role"] == "user":
            if q is not None:
                lines.append(f"- Q: {_gist(q, 220)}")
            q = m["content"]
        else:
            a = _gist(m["content"], 260)
            lines.append(f"- Q: {_gist(q, 220)} → A: {a}" if q is not None else f"- A: {a}")
            q = None
    if q is not None:
        lines.append(f"- Q: {_gist(q, 220)}")
    while lines and sum(len(ln) + 1 for ln in lines) > SUMMARY_CHARS:
        lines.pop(0)
    return "\n".join(lines)


def load(store, key: str) -> dict:
    p = _path(store, key)
    try:
        d = json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"key": key, "title": "", "summary": "", "messages": [], "updated": 0}
    msgs = [c for c in (_clean(m) for m in d.get("messages") or []) if c]
    return {"key": key, "title": str(d.get("title") or ""), "summary": str(d.get("summary") or ""),
            "messages": msgs, "updated": d.get("updated") or 0}


def summary_for(store, key: str | None) -> str:
    if not key:
        return ""
    try:
        return load(store, key)["summary"]
    except ChatError:
        return ""


def save(store, key: str, messages: list, title: str = "") -> dict:
    """Store a conversation: the last KEEP messages in full, older ones folded into its summary."""
    key = valid_key(key)
    with _lock:
        cur = load(store, key)
        msgs = [c for c in (_clean(m) for m in messages or []) if c]
        summary = cur["summary"]
        if not msgs:  # a new conversation: forget this one entirely
            try:
                _path(store, key).unlink()
            except OSError:
                pass
            return {"key": key, "title": title, "summary": "", "messages": [], "updated": 0}
        if len(msgs) > KEEP:
            cut = len(msgs) - KEEP
            if msgs[cut]["role"] == "assistant":  # don't split a question from its answer
                cut += 1
            summary = fold(summary, msgs[:cut])
            msgs = msgs[cut:]
        d = {"key": key, "title": (title or cur["title"])[:300], "summary": summary, "messages": msgs,
             "updated": time.time()}
        p = _path(store, key)
        tmp = p.with_suffix(".json.tmp")
        tmp.write_text(json.dumps(d, ensure_ascii=False), encoding="utf-8")
        fsio.replace(tmp, p)
        _prune(store)
        return d


def delete(store, key: str) -> None:
    with _lock:
        try:
            _path(store, key).unlink()
        except OSError:
            pass


def list_threads(store) -> list[dict]:
    out = []
    for p in _dir(store).glob("*.json"):
        try:
            d = json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        msgs = d.get("messages") or []
        first = next((m.get("content", "") for m in msgs if m.get("role") == "user"), "")
        out.append({"key": p.stem, "title": d.get("title") or "", "updated": d.get("updated") or 0,
                    "count": len(msgs), "summarised": bool(d.get("summary")), "first": _gist(first, 120)})
    out.sort(key=lambda t: t["updated"], reverse=True)
    return out


def _prune(store) -> None:
    """Keep all conversations together under TOTAL_CHARS and MAX_THREADS (least recently used go first)."""
    files = []
    for p in _dir(store).glob("*.json"):
        try:
            st = p.stat()
            files.append((st.st_mtime, st.st_size, p))
        except OSError:
            continue
    files.sort(reverse=True)  # newest first
    total = 0
    for i, (_, size, p) in enumerate(files):
        total += size
        if i >= MAX_THREADS or total > TOTAL_CHARS:
            try:
                p.unlink()
            except OSError:
                pass


# ---------------------------------------------------------------- continue on your phone
# A conversation written out as one prompt you paste into any AI app (on your phone, or anywhere), so you can
# leave and keep talking. Nothing is sent: the page copies it, saves it as a file or hands it to the share
# sheet. It follows the export rule (app/privacy.py): private items stay out. A message that names or copies a
# private note is left out with a line saying so, [[links]] to private notes become "a private note", and
# papers that are private aren't described.
HANDOFF_CHARS = 60_000      # what most phone apps take in one paste; older turns are condensed past it
HANDOFF_LEFT_OUT = "(Left out here: this message quoted a private note.)"


def _who(n: dict) -> str:
    a = [str(x) for x in (n.get("authors") or []) if x]
    who = a[0].split(",")[0].split()[-1] if a and a[0].split() else ""
    if len(a) > 2:
        who += " et al."
    elif len(a) == 2:
        who += " and " + (a[1].split(",")[0].split() or [""])[-1]
    return who


def _paper_line(n: dict) -> str:
    bits = [b for b in (_who(n), str(n.get("year") or "")) if b]
    ident = (f"doi:{n['doi']}" if n.get("doi") else f"arXiv:{n['arxiv']}" if n.get("arxiv") else n.get("url") or "")
    return f"\"{n.get('title') or 'Untitled'}\"" + (f" ({', '.join(bits)})" if bits else "") + (f", {ident}" if ident else "")


def handoff(store, key: str, messages: list, title: str = "") -> dict:
    """The conversation as a prompt to paste into another AI: {text, messages, left_out, condensed}."""
    from . import privacy
    key = valid_key(key)
    base = key.split("~")[0]
    msgs = [c for c in (_clean(m) for m in messages or []) if c]
    summary = load(store, key)["summary"] if msgs else ""
    if not msgs:
        saved = load(store, key)
        msgs, summary = saved["messages"], saved["summary"]
    if not msgs:
        raise ChatError("There's nothing in this conversation yet.")
    summary = "\n".join(ln for ln in (summary or "").splitlines() if ln.strip() and not privacy.copies_private(ln))

    def get(nid):
        try:
            return store.get(nid)
        except (KeyError, OSError, ValueError):
            return None

    def shareable(n):
        try:
            return n is not None and not privacy.is_private(n)
        except Exception:  # can't tell: treat it as private, as every other send does
            return False

    def clean_text(t: str) -> str | None:
        t = privacy.hide_ids(privacy.scrub(t))
        return None if privacy.copies_private(t) else t  # stricter than a send: 8 copied words are enough

    # what it was about
    if base == "general":
        about = "General questions (not about my library)."
    elif base.startswith("paper-"):
        n = get(base[6:])
        about = f"The paper {_paper_line(n)}." if shareable(n) else "One of my papers (its details are private)."
        if shareable(n) and n.get("abstract"):
            about += "\n\nIts abstract:\n" + _gist(n["abstract"], 2500)
        if shareable(n) and n.get("summary") and clean_text(n["summary"]):
            about += "\n\nMy own summary of it:\n" + clean_text(n["summary"])
    elif base.startswith("topics-"):
        about = f"The papers in my topics: {title or base[7:].replace('+', ', ')}."
    else:
        about = "The papers and notes in my research library."

    left_out, papers, turns = 0, {}, []
    for m in msgs:
        text = clean_text(m["content"])
        if text is None:
            left_out += 1
            text = HANDOFF_LEFT_OUT
        who = "Me" if m["role"] == "user" else "Assistant"
        if m.get("sel") and m["sel"].get("text") and clean_text(m["sel"]["text"]):
            pg = f" (page {m['sel']['page']})" if m["sel"].get("page") else ""
            text = f"[About this passage{pg}: \"{clean_text(m['sel']['text'])}\"]\n{text}"
        for s in m.get("sources") or []:
            n = get(s["id"])
            if shareable(n) and n.get("type") == "paper":
                papers.setdefault(s["id"], _paper_line(n))
        turns.append(f"{who}:\n{text.strip()}")

    head = ("I'm continuing a conversation I started with an AI assistant in research brain, my research library "
            "app. I had to step away, so please pick up where we left off. You can't see my library, so work from "
            "what's below and ask me if you need more about a paper.\n\n"
            f"What we were talking about: {about}")
    tail = ("Please continue from here as the same assistant. Reply in one or two lines saying where we left off, "
            "then wait for my next message.")
    condensed = 0

    def build():
        parts = [head]
        if summary:
            parts.append("Earlier in the conversation (condensed):\n" + summary)
        parts.append("The conversation so far:\n\n" + "\n\n".join(turns))
        if papers:
            parts.append("Papers from my library it drew on:\n" + "\n".join(f"- {p}" for p in papers.values()))
        parts.append(tail)
        return "\n\n---\n\n".join(parts)

    text = build()
    while len(text) > HANDOFF_CHARS and len(turns) > 2:  # condense the oldest turns until it fits
        drop = 2 if len(turns) > 3 else 1
        summary = fold(summary, [{"role": "user" if t.startswith("Me:") else "assistant", "content": t.split(":\n", 1)[-1]}
                                 for t in turns[:drop]])
        turns, condensed = turns[drop:], condensed + drop
        text = build()
    if len(text) > HANDOFF_CHARS:
        text = text[:HANDOFF_CHARS].rsplit(" ", 1)[0] + " […]\n\n---\n\n" + tail
    if privacy.find_leaks(text):  # the backstop: never hand over something that copies a private note
        raise ChatError("This conversation quotes your private notes, so it can't be written out.")
    return {"text": text, "messages": len(turns), "left_out": left_out, "condensed": condensed}

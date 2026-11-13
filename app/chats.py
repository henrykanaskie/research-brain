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

import json
import re
import threading
import time
from pathlib import Path

KEEP = 40
MSG_CHARS = 6000
SUMMARY_CHARS = 4000
TOTAL_CHARS = 3_000_000
MAX_THREADS = 150
KEY_RE = re.compile(r"library|general|paper-[a-z0-9][a-z0-9-]{0,160}|topics-[a-z0-9][a-z0-9-]{0,80}(\+[a-z0-9][a-z0-9-]{0,80}){0,7}")
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
        tmp.replace(p)
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

"""Privacy of your own ideas: which items may be sent to an AI agent, and the one check every send passes.

Data leaves this computer in two ways only: through an AI agent (the CLI or model server the user chose,
which may talk to a cloud provider) and through exports the user makes. GitHub never sees the vault.
This module decides what is *private* and enforces it.

What is private
---------------
    is_private(item)   `private: true` in the item (frontmatter), or the item's type is in the library's
                       "private by default" set and it has no explicit `private: false`. Defaults:
                       {"question", "note"}, where "note" means a standalone note (type note, no parent).
                       Papers, fragments and summaries are shareable by default. A fragment or note
                       attached to a private parent is private too (the parent wins over the child's own
                       `private: false`).
    allow_ai(item, agent)   False for a private item, unless the agent is a local model server
                       (kind "local-server") and the setting `private_ok_for_local_ai` is on (default off).
    filter_for_ai(items, agent)   the items allow_ai lets through (for context builders and agent tools).

Settings live in the library (vault/state/privacy.json), so they move with it and survive a browser change:
    {"private_by_default": ["question", "note"], "private_ok_for_local_ai": false}

The choke point
---------------
    guard_prompt(text, agent, *, sources=None)
        Raises PrivacyError ("This includes private notes…") if any private item's id or text is in `text`.
        Called by the agents layer (app/agents/__init__.py) on every outgoing prompt, whatever path built
        it: plain asks, streams, every turn of the universal Research loop, the Claude native Research
        path's context and each tool result it hands the model. Context builders filter first
        (filter_for_ai), so the guard is the backstop that catches what they missed.

        How it detects a leak, fast (a 50k-character prompt against 2,000 private items in well under
        20 ms), using an index that is rebuilt when the library changes:
          ids      a private id inside [[...]] (a wikilink or a context heading), or bare when the id has
                   at least three hyphen-separated parts (a bare one-word id such as "plasma" would match
                   ordinary prose);
          text     every private item's text (title, summary, notes, properties, quote, its own full text)
                   is normalised (lower case, words only) and cut into overlapping 8-word shingles, which are
                   hashed. The outgoing text is shingled the same way; two or more NON-overlapping matching
                   shingles from the same private item (so at least 16 copied words, or two separate 8-word
                   runs) is a leak. Shingles with fewer than 4 specific words (stop words and generic
                   academic words such as "paper", "propose", "method", "results" don't count) are never
                   indexed, so shared stock phrases ("in this paper we propose a novel method …") don't
                   trigger it. A fragment's quote from a
                   shareable paper is the paper's text, not the user's, and is not fingerprinted.
    scrub(text)   replaces [[links]] to private notes (by id or title) with "[[a private note]]", so a shareable
                   note that links to a private idea doesn't reveal its title or id. The agents layer scrubs
                   before it guards.

Audit log
---------
    log_send(agent, purpose, item_ids, n_chars)   one JSON line per send in vault/state/ai_log.jsonl: time,
        agent id/name/kind, model, purpose, the library ids in the prompt, and its size. Never any content.
        Rotated at 5 MB (ai_log.1.jsonl keeps the previous file). GET /api/privacy/log reads it.

Nothing here imports numpy or the web stack.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import threading
import time
from pathlib import Path

SETTINGS_FILE = "privacy.json"
LOG_FILE = "ai_log.jsonl"
LOG_MAX_BYTES = int(os.environ.get("RB_AI_LOG_MAX_BYTES", str(5 * 1024 * 1024)))
DEFAULT_PRIVATE = ("question", "note")
DEFAULTS = {"private_by_default": list(DEFAULT_PRIVATE), "private_ok_for_local_ai": False}
# Kinds the settings page offers. "note" = a standalone note. Other agents may add types; any type in the
# library is offered too (see kinds()).
KNOWN_KINDS = ("note", "question", "paper", "fragment")
SHINGLE = 8
MIN_MATCHES = 2
FULLTEXT_CHARS = 200_000    # of a private item's own extracted text (text/<id>.txt) that is fingerprinted
RECHECK_SECONDS = 2.0       # how often the full library signature is re-read when nothing else changed
REDACTED = "[[a private note]]"


try:  # PrivacyError is an AgentError, so every path that already reports agent errors reports this one
    from .agents.base import AgentError as _AgentError
except Exception:  # pragma: no cover
    _AgentError = RuntimeError


class PrivacyError(_AgentError):
    """An outgoing prompt contained private material; nothing was sent."""

    def __init__(self, message: str, ids: list[str] | None = None):
        super().__init__(message)
        self.ids = list(ids or [])


# ---------------------------------------------------------------- configuration
_lock = threading.RLock()
_store = None
_settings_cache: dict | None = None
_settings_rev = 0
_index: dict = {}           # see _rebuild()
_item_cache: dict = {}      # id -> (key, frozenset of shingle hashes)
_checked_at = 0.0
_log_lock = threading.Lock()


def configure(store) -> None:
    """Point the module at the library being served (app/server.py does this at start-up)."""
    global _store, _settings_cache, _index, _item_cache, _checked_at
    with _lock:
        _store = store
        _settings_cache = None
        _index = {}
        _item_cache = {}
        _checked_at = 0.0


def configured() -> bool:
    return _store is not None


def _state_dir() -> Path | None:
    if _store is None:
        return None
    d = Path(_store.root) / "state"
    d.mkdir(parents=True, exist_ok=True)
    return d


def settings() -> dict:
    """The library's privacy settings, defaults filled in."""
    global _settings_cache
    with _lock:
        if _settings_cache is not None:
            return dict(_settings_cache, private_by_default=list(_settings_cache["private_by_default"]))
        data: dict = {}
        d = _state_dir()
        if d is not None:
            try:
                raw = json.loads((d / SETTINGS_FILE).read_text(encoding="utf-8"))
                data = raw if isinstance(raw, dict) else {}
            except (OSError, ValueError):
                data = {}
        out = {"private_by_default": list(DEFAULTS["private_by_default"]),
               "private_ok_for_local_ai": bool(DEFAULTS["private_ok_for_local_ai"])}
        kinds = data.get("private_by_default")
        if isinstance(kinds, list):
            out["private_by_default"] = sorted({str(k).strip().lower() for k in kinds if str(k).strip()})
        if isinstance(data.get("private_ok_for_local_ai"), bool):
            out["private_ok_for_local_ai"] = data["private_ok_for_local_ai"]
        _settings_cache = out
        return dict(out, private_by_default=list(out["private_by_default"]))


def save_settings(private_by_default=None, private_ok_for_local_ai=None) -> dict:
    """Change the settings (None keeps a value). Returns the new settings."""
    global _settings_cache, _settings_rev
    with _lock:
        cur = settings()
        if private_by_default is not None:
            if not isinstance(private_by_default, (list, tuple, set)):
                raise ValueError("private_by_default must be a list of types")
            kinds = sorted({str(k).strip().lower() for k in private_by_default if str(k).strip()})
            if any(not re.fullmatch(r"[a-z][a-z0-9_-]{0,30}", k) for k in kinds):
                raise ValueError("private_by_default holds type names such as note, question, paper, fragment")
            cur["private_by_default"] = kinds
        if private_ok_for_local_ai is not None:
            cur["private_ok_for_local_ai"] = bool(private_ok_for_local_ai)
        d = _state_dir()
        if d is not None:
            from .paths import write_json_atomic
            write_json_atomic(d / SETTINGS_FILE, cur)
        _settings_cache = cur
        _settings_rev += 1
        _approved.clear()
        return settings()


def kinds() -> list[str]:
    """Types the settings page offers: the known ones plus any other type in the library."""
    seen = list(KNOWN_KINDS)
    for t in sorted((_fresh_index().get("types") or set())):
        if t not in seen:
            seen.append(t)
    return seen


def version() -> tuple:
    """Changes whenever what counts as private may have changed (for caches such as /api/graph)."""
    idx = _fresh_index()
    return (_settings_rev, idx.get("key"), len(idx.get("private") or ()))


# ---------------------------------------------------------------- is it private?
_TRUE = {"true", "yes", "1", "on", "private"}
_FALSE = {"false", "no", "0", "off", "shared", "shareable", "public"}


def explicit_flag(item: dict) -> bool | None:
    """The item's own `private` value (True/False), or None when it doesn't say."""
    if not isinstance(item, dict):
        return None
    v = item["private_explicit"] if "private_explicit" in item else item.get("private")
    if v is None:
        ex = item.get("_extra")
        if isinstance(ex, dict):
            v = ex.get("private")
    if isinstance(v, bool):
        return v
    if isinstance(v, (int, float)):
        return bool(v)
    if isinstance(v, str):
        s = v.strip().lower()
        if s in _TRUE:
            return True
        if s in _FALSE:
            return False
    return None


def _own(item: dict, kinds_set: set) -> bool:
    """Private by the item's own flag or type default, ignoring its parent."""
    flag = explicit_flag(item)
    if flag is not None:
        return flag
    t = str(item.get("type") or "note").lower()
    if t == "note":
        return "note" in kinds_set and not str(item.get("parent") or "").strip()
    return t in kinds_set


def _compute(item: dict, by_id: dict, kinds_set: set, depth: int = 0) -> bool:
    parent = str(item.get("parent") or "").strip()
    if parent and depth < 4:
        p = by_id.get(parent)
        if p is not None and _compute(p, by_id, kinds_set, depth + 1):
            return True
    return _own(item, kinds_set)


def is_private(note_or_item: dict) -> bool:
    """True if the item must not be sent to an AI agent or exported by default (see the module docstring)."""
    if not isinstance(note_or_item, dict):
        return False
    kinds_set = set(settings()["private_by_default"])
    parent = str(note_or_item.get("parent") or "").strip()
    if parent:
        idx = _fresh_index()
        if parent in (idx.get("private") or ()):
            return True
        if not idx.get("by_id") and _store is not None:  # no index (yet): read the parent itself
            try:
                if _compute(_store.get(parent), {}, kinds_set, 1):
                    return True
            except Exception:
                pass
    return _own(note_or_item, kinds_set)


def is_private_id(nid: str) -> bool:
    return str(nid or "") in (_fresh_index().get("private") or ())


def private_ids() -> frozenset:
    return frozenset(_fresh_index().get("private") or ())


def local_exception(agent) -> bool:
    """True when this agent may see private items: a local model server, with the setting on."""
    return bool(agent is not None and getattr(agent, "kind", "") == "local-server"
                and settings()["private_ok_for_local_ai"])


def allow_ai(item, agent) -> bool:
    """May this item (a note dict, or a note id) go to this agent?"""
    if isinstance(item, str):
        priv = is_private_id(item)
    else:
        priv = is_private(item)
    return not priv or local_exception(agent)


def filter_for_ai(items, agent) -> list:
    """The items (note dicts or ids) that allow_ai lets through, in order."""
    if local_exception(agent):
        return list(items)
    return [x for x in items if allow_ai(x, agent)]


# ---------------------------------------------------------------- fingerprints
_WORD_RE = re.compile(r"\w+", re.U)
_LINK_RE = re.compile(r"\[\[([^\[\]\n|]+?)(?:\|([^\[\]\n]*))?\]\]")
_BARE_ID_RE = re.compile(r"[a-z0-9]+(?:-[a-z0-9]+){2,}")
STOP = frozenset("""a an and are as at be been being but by can could did do does for from had has have he her
his how i if in into is it its may might more most no not of on one or our she should so some such than that
the their them then there these they this those to too was we were what when where which while who will with
would you your also all any each other only very just about over under between both same own here out up
we us me my his hers its ours theirs let lets get got make made use used using based show shown shows see seen
paper papers work works study studies propose proposed novel new method methods approach approaches result
results problem problems case cases hand clear fact way ways figure fig table section et al data model models
present presented given number first second however therefore thus hence well many much like further eg ie""".split())
# STOP also holds generic academic words: an 8-word shingle needs at least MIN_CONTENT other words to be
# indexed, so boilerplate ("in this paper we propose a novel method for the problem") never counts as a copy.
MIN_CONTENT = 4
TEXT_FIELDS = ("title", "summary", "body", "abstract", "saved_because", "locator", "answer")


def words(text: str) -> list[str]:
    return _WORD_RE.findall((text or "").lower())


def _shingle_hashes(ws: list[str], index_side: bool) -> list[int]:
    n = SHINGLE
    if len(ws) < n:
        return []
    out = []
    if index_side:
        stop = [w in STOP or w.isdigit() for w in ws]
        run = sum(stop[:n])
        for i in range(len(ws) - n + 1):
            if i:
                run += stop[i + n - 1] - stop[i - 1]
            if run <= n - MIN_CONTENT:  # enough specific words in the 8
                out.append(hash(tuple(ws[i:i + n])))
    else:
        out = [hash(tuple(ws[i:i + n])) for i in range(len(ws) - n + 1)]
    return out


def _title_key(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "").strip()).casefold()


def _item_text(n: dict, parent_private: bool) -> str:
    parts = [str(n.get(k) or "") for k in TEXT_FIELDS]
    # a fragment's quote is the source's text; it is the user's only when the source is private too
    if n.get("type") != "fragment" or parent_private or not n.get("parent"):
        parts.append(str(n.get("quote") or ""))
    ex = n.get("_extra")
    if isinstance(ex, dict):  # properties other features (or Obsidian) add: idea cards, questions, ...
        for v in ex.values():
            if isinstance(v, str):
                parts.append(v)
            elif isinstance(v, list):
                parts += [x for x in v if isinstance(x, str)]
    # [[links]] are left out: a link names ANOTHER note (its id is that note's title in slug form), so its
    # words are not this item's text; links to private notes are checked separately, by id and title
    return _LINK_SPAN_RE.sub(" ", "\n".join(p for p in parts if p))


_LINK_SPAN_RE = re.compile(r"\[\[[^\[\]\n]*\]\]")


def _fulltext_of(nid: str) -> tuple[str, int]:
    if _store is None:
        return "", 0
    try:
        p = _store.text_path(nid)
        st = p.stat()
    except (KeyError, OSError):
        return "", 0
    try:
        with open(p, encoding="utf-8", errors="ignore") as f:
            return f.read(FULLTEXT_CHARS), st.st_mtime_ns
    except OSError:
        return "", 0


def _cheap_key():
    s = _store
    meta = getattr(s, "meta", None)
    return (getattr(s, "rev", 0), getattr(meta, "gen", None), _settings_rev)


def _fresh_index() -> dict:
    """The private-items index, rebuilt if the library or the settings changed."""
    global _checked_at
    if _store is None:
        return {}
    with _lock:
        now = time.monotonic()
        ck = _cheap_key()
        if _index and _index.get("cheap") == ck and now - _checked_at < RECHECK_SECONDS:
            return _index
        try:
            sig = _store.signature()
        except Exception:
            sig = None
        _checked_at = now
        key = (sig, _settings_rev, _cards_sig())
        if _index and _index.get("key") == key:
            _index["cheap"] = _cheap_key()
            return _index
        _rebuild(key)
        return _index


def refresh() -> None:
    """Rebuild now (after a privacy change that must take effect before the next check)."""
    global _checked_at
    with _lock:
        _checked_at = 0.0
        _index.clear()
        _approved.clear()
    _fresh_index()


def _cards_sig() -> str:
    try:
        from . import cards
        return cards.signature()
    except Exception:
        return ""


def _private_cards(private_notes: set) -> list[dict]:
    try:
        from . import cards
        return [c for c in cards.card_store().all_cards()
                if c.get("private") or str(c.get("paper") or "") in private_notes]
    except Exception:
        return []


def _rebuild(key) -> None:
    global _index
    try:
        notes = list(_store.all())
    except Exception as e:
        print(f"[research_brain] privacy index failed: {e!r}")
        notes = []
    kinds_set = set(settings()["private_by_default"])
    by_id = {n["id"]: n for n in notes if isinstance(n, dict) and n.get("id")}
    private = {i for i, n in by_id.items() if _compute(n, by_id, kinds_set)}
    shingles: dict[int, str] = {}
    bare: set[str] = set()
    titles: dict[str, str] = {}
    keep: dict = {}
    for nid in sorted(private):
        n = by_id[nid]
        parent_private = str(n.get("parent") or "") in private
        own_text, mtime = _fulltext_of(nid) if n.get("has_text") or n.get("type") == "paper" else ("", 0)
        ck = (n.get("updated"), mtime, parent_private, len(n.get("body") or ""), n.get("title"))
        hit = _item_cache.get(nid)
        if hit and hit[0] == ck:
            hs = hit[1]
        else:
            ws = words(_item_text(n, parent_private))
            hs = set(_shingle_hashes(ws, True))
            if own_text:
                hs.update(_shingle_hashes(words(own_text), True))
            hs = frozenset(hs)
        keep[nid] = (ck, hs)
        for h in hs:
            shingles.setdefault(h, nid)
        if nid.count("-") >= 2:
            bare.add(nid)
        tk = _title_key(n.get("title") or "")
        if tk and n.get("type") != "fragment":
            titles.setdefault(tk, nid)
    # private idea cards (written by hand, or on a private paper) are fingerprinted too, so the guard
    # backs up the cards module's own filters
    for c in _private_cards(private):
        cid = str(c.get("id") or "")
        if not cid:
            continue
        txt = " ".join(str(x) for x in (c.get("text"), c.get("latex"), (c.get("quantity") or {}).get("conditions")) if x)
        hs = frozenset(_shingle_hashes(words(txt), True))
        for h in hs:
            shingles.setdefault(h, cid)
        private.add(cid)
        bare.add(cid)
    _item_cache.clear()
    _item_cache.update(keep)
    # a shareable item's title that equals a private one's: links by that title are not private
    for i, n in by_id.items():
        if i not in private and n.get("type") != "fragment":
            titles.pop(_title_key(n.get("title") or ""), None)
    _index = {"key": key, "cheap": _cheap_key(), "private": frozenset(private), "shingles": shingles,
              "bare": frozenset(bare), "titles": titles, "all_ids": frozenset(by_id), "by_id": by_id,
              "types": {str(n.get("type") or "note") for n in by_id.values()}}


# ---------------------------------------------------------------- the check
def find_leaks(text: str, min_matches: int = MIN_MATCHES) -> list[str]:
    """Ids of private items whose id or text appears in `text` (empty = clean). `min_matches`: how many
    non-overlapping 8-word shingles of one item count as a copy (memory's short findings use 1)."""
    idx = _fresh_index()
    private = idx.get("private")
    if not text or not private:
        return []
    found: dict[str, None] = {}
    if "[[" in text:
        for m in _LINK_RE.finditer(text):
            t = m.group(1).strip()
            tl = t.lower()
            if tl in private:
                found[tl] = None
            else:
                hit = idx["titles"].get(_title_key(t))
                if hit:
                    found[hit] = None
    low = text.lower()
    if idx["bare"]:
        for tok in set(_BARE_ID_RE.findall(low)):
            if tok in idx["bare"]:
                found[tok] = None
    sh = idx["shingles"]
    if sh:
        ws = _WORD_RE.findall(low)
        last: dict[str, int] = {}
        count: dict[str, int] = {}
        n = SHINGLE
        for i in range(len(ws) - n + 1):
            owner = sh.get(hash(tuple(ws[i:i + n])))
            if owner is None:
                continue
            if i >= last.get(owner, -n):
                last[owner] = i + n
                c = count.get(owner, 0) + 1
                count[owner] = c
                if c >= min_matches:
                    found[owner] = None
    return list(found)


def _agent_label(agent) -> str:
    if agent is None:
        return "the AI agent"
    return str(getattr(agent, "name", "") or getattr(agent, "id", "") or "the AI agent")


def guard_prompt(text: str, agent, *, sources=None) -> None:
    """Refuse (raise PrivacyError) if any private item's text or id is in `text`. `sources`: ids the caller
    knows went into the text (only used in the message). No-op for the local-AI exception."""
    if local_exception(agent):
        return
    leaks = find_leaks(text or "")
    if not leaks:
        return
    titles = []
    by_id = _fresh_index().get("by_id") or {}
    for i in leaks[:3]:
        t = (by_id.get(i) or {}).get("title") or i
        titles.append(f"“{t[:60]}”")
    more = f" and {len(leaks) - 3} more" if len(leaks) > 3 else ""
    raise PrivacyError(
        f"This includes private notes ({', '.join(titles)}{more}), so nothing was sent to {_agent_label(agent)}. "
        "Private notes stay on this computer. Leave them out, or mark them shareable (the lock in the note's "
        "header) if you want the AI to read them.", ids=leaks)


def hide_ids(text: str) -> str:
    """Private ids written bare (e.g. in a model's own JSON request echoed back to it) become "(hidden)"."""
    idx = _fresh_index()
    priv = idx.get("private")
    if not text or not priv:
        return text
    return re.sub(r"[a-z0-9][a-z0-9-]*", lambda m: "(hidden)" if m.group(0) in priv else m.group(0), text)


def scrub(text: str, agent=None) -> str:
    """[[links]] to private notes (by id or title) become "[[a private note]]"."""
    if not text or "[[" not in text or local_exception(agent):
        return text
    idx = _fresh_index()
    private = idx.get("private")
    if not private:
        return text

    def sub(m):
        t = m.group(1).strip()
        if t.lower() in private or idx["titles"].get(_title_key(t)):
            return REDACTED
        return m.group(0)
    return _LINK_RE.sub(sub, text)


def copies_private(text: str) -> list[str]:
    """Private items a short text (a memory finding) names or copies at least 8 words of."""
    return find_leaks(text, min_matches=1)


def mentioned_ids(text: str, limit: int = 200) -> list[str]:
    """Library ids the text names as [[id]] (for the audit log)."""
    ids = _fresh_index().get("all_ids") or frozenset()
    if not text or not ids or "[[" not in text:
        return []
    out: dict[str, None] = {}
    for m in _LINK_RE.finditer(text):
        t = m.group(1).strip().lower()
        if t in ids:
            out[t] = None
            if len(out) >= limit:
                break
    return list(out)


# ---------------------------------------------------------------- audit log
def _log_path() -> Path | None:
    d = _state_dir()
    return None if d is None else d / LOG_FILE


def log_send(agent, purpose: str, item_ids, n_chars: int) -> dict | None:
    """Append one line to vault/state/ai_log.jsonl (no content). Never raises."""
    p = _log_path()
    if p is None:
        return None
    model = ""
    try:
        from . import agents as _agents
        model = _agents.model_for(getattr(agent, "id", "")) if agent is not None else ""
    except Exception:
        model = ""
    ids = list(dict.fromkeys(str(i) for i in (item_ids or []) if i))[:200]
    entry = {"at": dt.datetime.now().astimezone().isoformat(timespec="seconds"),
             "agent": str(getattr(agent, "id", "") or ""), "agent_name": str(getattr(agent, "name", "") or ""),
             "kind": str(getattr(agent, "kind", "") or ""), "model": model, "purpose": str(purpose or "other")[:40],
             "items": ids, "private_items": [i for i in ids if is_private_id(i)], "chars": int(n_chars or 0)}
    line = json.dumps(entry, ensure_ascii=False) + "\n"
    try:
        with _log_lock:
            try:
                if p.stat().st_size + len(line) > LOG_MAX_BYTES:
                    os.replace(p, p.with_name("ai_log.1.jsonl"))
            except FileNotFoundError:
                pass
            fd = os.open(p, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
            with os.fdopen(fd, "a", encoding="utf-8", newline="\n") as f:
                f.write(line)
    except OSError as e:
        print(f"[research_brain] couldn't write the AI log: {e!r}")
        return None
    return entry


def read_log(limit: int = 200) -> list[dict]:
    """The most recent entries, newest first (both the current and the rotated file)."""
    p = _log_path()
    if p is None:
        return []
    limit = max(1, min(int(limit or 200), 5000))
    out: list[dict] = []
    for f in (p, p.with_name("ai_log.1.jsonl")):
        try:
            lines = f.read_text(encoding="utf-8").splitlines()
        except OSError:
            continue
        for line in reversed(lines):
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if isinstance(e, dict):
                out.append(e)
                if len(out) >= limit:
                    return out
    return out


# ---------------------------------------------------------------- the agents layer's entry point
_approved: dict[tuple, float] = {}
APPROVE_TTL = 5.0


def _akey(agent, text: str) -> tuple:
    return (getattr(agent, "id", None), hash(text), len(text))


def check_send(agent, purpose: str, parts, *, sources=None, approve=()) -> list[str]:
    """What the agents layer calls before anything reaches a model: scrub each of `parts` (the outgoing
    strings), guard them, log the send, and remember the strings it approved, plus `approve` (strings, or a
    function of the scrubbed parts returning the exact strings a nested call will see), so the backend's
    own wrapper doesn't check and log the same send twice. Returns the scrubbed parts. Raises PrivacyError.

    A nested call whose longest part was approved a moment ago is the same send: only its other (short,
    e.g. a fixed instruction) parts are checked, and it isn't logged again."""
    parts = [p if isinstance(p, str) else "" for p in parts]
    if not configured() or not any(parts):
        return parts
    now = time.monotonic()
    with _lock:
        for k in [k for k, t in _approved.items() if t < now]:
            _approved.pop(k, None)
        ok = [bool(p) and _akey(agent, p) in _approved for p in parts]
    longest = max(range(len(parts)), key=lambda i: len(parts[i]))
    if all(o or not p for o, p in zip(ok, parts)):
        return parts
    clean = [p if o else scrub(p, agent) for p, o in zip(parts, ok)]
    todo = "\n".join(c for c, o in zip(clean, ok) if not o)
    guard_prompt(todo, agent, sources=sources)
    if not ok[longest]:
        joined = "\n".join(clean)
        log_send(agent, purpose, list(sources or []) + mentioned_ids(joined), len(joined))
    extra = approve(clean) if callable(approve) else approve
    with _lock:
        for s in list(clean) + list(extra or ()):
            if s:
                _approved[_akey(agent, s)] = now + APPROVE_TTL
    return clean


def approved(agent, text: str) -> bool:
    with _lock:
        t = _approved.get(_akey(agent, text))
        return t is not None and t >= time.monotonic()

"""How much each AI feature may send, so research_brain doesn't eat people's plan limits.

Everything research_brain asks an agent goes through a subscription the user already pays for (or a local
model), and those plans have usage limits. Each feature's context is capped here, in one table, at one of
three levels the user picks in Library settings › AI usage:

    light       the smallest prompts: answers lean on retrieved passages, short chat memory
    balanced    the default: a typical journal paper is sent whole only when that is cheap enough
    thorough    the most context (the limits before this module existed)

Other savings live where the prompt is built: answers are cached where the inputs repeat (link
explanations, critiques of an unchanged summary), earlier chat turns are trimmed, and with
"lighter model for small jobs" on (the default) short, mechanical jobs (explaining a link, checking a
review answer, suggesting idea cards, summarising a question's evidence) run on the provider's small
model when research_brain knows one. Nothing here runs the AI by itself: every request starts from
something the user did.

Token counts are estimates: about 4 characters of English per token.
"""
from __future__ import annotations

import contextlib
import contextvars
import datetime as dt

LEVELS = ("light", "balanced", "thorough")
DEFAULT_LEVEL = "balanced"
CHARS_PER_TOKEN = 4

#                           light    balanced  thorough
TABLE: dict[str, tuple[int, int, int]] = {
    "paper_full_chars":     (12_000, 24_000,  48_000),   # paper chat: send the whole paper up to this size
    "paper_passages":       (8,      12,      18),       # ...else this many of its best passages
    "related_papers":       (2,      3,       4),        # "also use similar papers"
    "chat_passages":        (8,      12,      12),       # library chat
    "history_messages":     (12,     20,      40),       # earlier messages a chat resends
    "history_chars":        (20_000, 60_000,  160_000),  # all of them together (the latest exchange always in full)
    "critique_full_chars":  (24_000, 48_000,  180_000),  # critique: the whole paper up to this size...
    "critique_passages":    (10,     16,      24),       # ...else its opening, its end and these passages
    "research_steps":       (3,      5,       6),        # research mode: lookups before it must answer
    "research_seed":        (4,      6,       8),        # passages found before its first step (resent each step)
    "research_result_chars": (3_000, 4_500,   6_000),    # one lookup's result as shown to the model
    "research_transcript_chars": (12_000, 24_000, 36_000),
}

# The provider's small model, used for short mechanical jobs when "lighter model for small jobs" is on.
# Only for agents whose CLI takes a stable alias; others keep the model the user picked.
LIGHT_MODELS = {"claude": "haiku", "gemini": "gemini-2.5-flash"}

SMALL_JOBS = ("explain", "review check", "card-extraction", "question summary", "region names")

_small = contextvars.ContextVar("rb_small_job", default=False)


def _settings() -> dict:
    try:
        from . import paths
        return paths.load_settings()
    except Exception:
        return {}


def level() -> str:
    lv = str(_settings().get("ai_usage") or DEFAULT_LEVEL)
    return lv if lv in LEVELS else DEFAULT_LEVEL


def get(name: str) -> int:
    return TABLE[name][LEVELS.index(level())]


def light_model_on() -> bool:
    v = _settings().get("light_model_small_jobs")
    return True if v is None else bool(v)


@contextlib.contextmanager
def small_job():
    """Within this block, agents.model_for() answers with the provider's small model (when on and known)."""
    tok = _small.set(True)
    try:
        yield
    finally:
        _small.reset(tok)


def small_model(backend_id: str) -> str:
    if not _small.get() or not light_model_on():
        return ""
    return LIGHT_MODELS.get(backend_id, "")


LAST_EXCHANGE_CHARS = 40_000  # the latest question and answer go in full, up to this each


def _cut(text: str, cap: int) -> str:
    """Text cut to about cap characters, keeping its beginning and its end (an answer's conclusion matters)."""
    if len(text) <= cap:
        return text
    head = max(200, int(cap * 0.6))
    tail = max(0, cap - head)
    return text[:head].rsplit(" ", 1)[0] + " […] " + (text[-tail:].split(" ", 1)[-1] if tail else "")


def trim_history(history: list[dict]) -> list[dict]:
    """The conversation the agent sees: the latest exchange in full (it must be able to read the end of its own
    last answer), then earlier messages, newest first, in full while they fit the budget; one that doesn't fit
    keeps its beginning and end, and older ones are left to the conversation's summary (app/chats.py)."""
    n, total = get("history_messages"), get("history_chars")
    msgs = list(history or [])[-n:]
    out: list[dict] = []
    room = total
    for i, h in enumerate(reversed(msgs)):
        c = str(h.get("content", ""))
        if i < 2:  # the latest question and answer
            c = _cut(c, LAST_EXCHANGE_CHARS)
        elif room <= 0:
            break
        else:
            if len(c) > room:
                c = _cut(c, max(room, 600))
            room -= len(c)  # the budget is for the earlier messages; the latest exchange comes on top
        out.append({**h, "content": c})
    return list(reversed(out))


def tokens(chars: int) -> int:
    return max(0, round(int(chars or 0) / CHARS_PER_TOKEN))


def settings_out() -> dict:
    return {"level": level(), "levels": list(LEVELS), "light_model_small_jobs": light_model_on(),
            "light_models": LIGHT_MODELS, "table": {k: dict(zip(LEVELS, v)) for k, v in TABLE.items()}}


def save(level_: str | None = None, light_model: bool | None = None) -> dict:
    from . import paths
    upd: dict = {}
    if level_ is not None:
        if level_ not in LEVELS:
            raise ValueError(f"AI usage must be one of {', '.join(LEVELS)}")
        upd["ai_usage"] = level_
    if light_model is not None:
        upd["light_model_small_jobs"] = bool(light_model)
    if upd:
        paths.save_settings(upd)
    return settings_out()


def usage(entries: list[dict], days: int = 7) -> dict:
    """Estimated tokens sent per day and per feature over the last `days`, from the AI log (no content)."""
    now = dt.datetime.now().astimezone()
    since = now - dt.timedelta(days=days)
    by_day: dict[str, int] = {}
    by_purpose: dict[str, dict] = {}
    total = requests = 0
    for e in entries:
        try:
            at = dt.datetime.fromisoformat(str(e.get("at")))
        except ValueError:
            continue
        if at.tzinfo is None:
            at = at.astimezone()
        if at < since:
            continue
        t = tokens(e.get("chars", 0))
        total += t
        requests += 1
        day = at.date().isoformat()
        by_day[day] = by_day.get(day, 0) + t
        p = by_purpose.setdefault(str(e.get("purpose") or "other"), {"tokens": 0, "requests": 0})
        p["tokens"] += t
        p["requests"] += 1
    days_list = [(now - dt.timedelta(days=i)).date().isoformat() for i in range(days - 1, -1, -1)]
    return {"days": days, "tokens": total, "requests": requests,
            "by_day": [{"day": d, "tokens": by_day.get(d, 0)} for d in days_list],
            "by_purpose": sorted(({"purpose": k, **v} for k, v in by_purpose.items()), key=lambda x: -x["tokens"]),
            "note": "Estimated as characters ÷ 4. Your agent's own instructions and its replies are not counted."}

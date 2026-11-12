"""Research directions: an AI agent reads the evidence you gathered and suggests where to go next.

For an open question (its kept evidence) or a topic (its papers), the agent writes gaps in what's known,
hypotheses worth testing, experiments or analyses that would test them, and what result would change your
answer, each tied to the sources it read (cited [[id]] p. N, which the app links to the passage).

Same controls as every AI feature:
- The question or topic must itself be allowed to reach an AI (app/privacy.py; a question's reach must be
  Library, Scout or Agent). Private notes and private papers never go: they are left out and listed as left out.
- You see exactly what will be sent before anything is (the preview: every source, included or left out and
  why, and the approximate size), and can untick sources. Your current answer goes only if you tick it.
- The call goes through agents.ask_parts like the rest: the privacy guard checks the outgoing text for
  passages of private notes, the AI log ("What was sent to AI") records it, and the AI usage level caps how
  much is sent (app/budget.py).
- The result is saved with the question or topic (state directions.json, the last few runs). "Save as a note"
  makes a note that is private by default, like every note you write.
"""
from __future__ import annotations

import datetime as dt

INSTRUCTION = (
    "On stdin: a researcher's {what} and the sources they gathered for it, each under a heading that says how "
    "to cite it. Suggest research directions, grounded in those sources. Write these sections, short and "
    "specific to their field:\n"
    "## Gaps\nWhat the sources leave open or disagree on (2-4 bullets).\n"
    "## Hypotheses\nClaims worth testing that follow from the sources (2-4).\n"
    "## How to test them\nConcrete experiments, measurements, simulations or analyses, with what each would show (2-4).\n"
    "## What would change the picture\nResults that would overturn or confirm the current view (1-3).\n"
    "Cite the source behind each point exactly as its heading shows (e.g. [[note-id]] p. 4). Mark your own "
    "reasoning beyond the sources as (speculative). Never invent sources, numbers or citations. Typeset math "
    "with $...$.{focus}")

MAX_SOURCES = {"light": 10, "balanced": 18, "thorough": 30}
SNIPPET = {"light": 500, "balanced": 900, "thorough": 1500}
KEEP_RUNS = 3


def _level() -> str:
    from . import budget
    return budget.level()


def instruction(what: str, focus: str = "") -> str:
    f = f"\n\nThey want directions especially about: {focus.strip()[:300]}" if focus.strip() else ""
    return INSTRUCTION.format(what=what, focus=f)


def question_sources(q: dict, data: dict, by_id: dict, allowed) -> list[dict]:
    """[{id, title, kind, verdict, allowed, why, block}] for a question's kept evidence."""
    from .questions import KEPT
    snip = SNIPPET[_level()]
    out = []
    for i in data["items"]:
        if i.get("verdict") not in KEPT:
            continue
        ref = i.get("ref") or {}
        note = f"\nTheir note on it: {i['user_note']}" if i.get("user_note") else ""
        if i.get("kind") == "external":
            p = i.get("paper") or {}
            head = (f"{i.get('title', '')} ({', '.join((p.get('authors') or [])[:2])} {p.get('year') or ''}; "
                    f"{ref.get('doi') or ref.get('url') or ''}; not in their library)")
            out.append({"id": i["id"], "note": "", "title": i.get("title") or "", "kind": "published paper", "verdict": i["verdict"],
                        "allowed": True, "why": "", "block": f"### {head} ({i['verdict']})\nAbstract: {(i.get('snippet') or '')[:snip]}{note}"})
            continue
        nid = ref.get("note") or ""
        n = by_id.get(nid)
        ok = bool(n) and allowed(n)
        where = f" p. {ref['page']}" if ref.get("page") else ""
        out.append({"id": i["id"], "note": nid, "title": (n or {}).get("title") or i.get("title") or nid, "kind": i.get("kind"),
                    "verdict": i["verdict"], "allowed": ok, "why": "" if ok else "private",
                    "block": f"### [[{nid}]]{where} ({i['verdict']})\n{(i.get('snippet') or '')[:snip]}{note}"})
    return out


def topic_sources(t: dict, by_id: dict, allowed) -> list[dict]:
    """[{id, title, kind, allowed, why, block}] for a topic's papers and notes: summary or abstract."""
    snip = SNIPPET[_level()]
    out = []
    for nid in t.get("members") or []:
        n = by_id.get(nid)
        if not n:
            continue
        ok = allowed(n)
        text = (n.get("summary") or "").strip() or (n.get("abstract") or "").strip() or (n.get("body") or "").strip()
        meta = ", ".join(x for x in [", ".join((n.get("authors") or [])[:2]), str(n.get("year") or "")] if x)
        out.append({"id": nid, "note": nid, "title": n.get("title") or nid, "kind": n.get("type"), "allowed": ok and bool(text),
                    "why": "" if ok and text else ("private" if not ok else "nothing written yet (no summary or abstract)"),
                    "block": f"### [[{nid}]] {n.get('title') or ''} ({meta})\n{text[:snip]}"})
    return out


def build(head: str, sources: list[dict], exclude: set[str], extra: str = "") -> tuple[str, list[dict], list[dict]]:
    """(context, used, left_out). The level's MAX_SOURCES caps how many go."""
    cap = MAX_SOURCES[_level()]
    used, left = [], []
    for s in sources:
        if not s["allowed"]:
            left.append({**s, "why": s["why"] or "private"})
        elif s["id"] in exclude:
            left.append({**s, "why": "you unticked it"})
        elif len(used) >= cap:
            left.append({**s, "why": f"over this usage level's limit of {cap} sources"})
        else:
            used.append(s)
    ctx = head + (f"\n\n{extra}" if extra else "") + "\n\n---\nSOURCES:\n\n" + "\n\n".join(s["block"] for s in used)
    return ctx, used, left


def preview(ctx_text: str, used: list[dict], left: list[dict], instr: str) -> dict:
    from . import budget
    strip = lambda s: {k: s[k] for k in ("id", "title", "kind", "verdict", "why") if k in s}  # noqa: E731
    return {"used": [strip(s) for s in used], "left_out": [strip(s) for s in left],
            "chars": len(ctx_text) + len(instr), "tokens": budget.tokens(len(ctx_text) + len(instr)), "level": _level()}


def save_run(store, key: str, run: dict) -> list[dict]:
    data = store.load_json("directions.json")
    runs = [run] + [r for r in (data.get(key) or [])][:KEEP_RUNS - 1]
    data[key] = runs
    store.save_json("directions.json", data)
    return runs


def runs(store, key: str) -> list[dict]:
    return store.load_json("directions.json").get(key) or []


def now() -> str:
    return dt.datetime.now(dt.timezone.utc).isoformat(timespec="seconds")

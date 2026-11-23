"""Region changes the chat proposes, applied only when you click Apply (static/regionedit.js).

When you ask the chat to change your regions ("move the krypton papers into Cross sections", "rename Downloads to
Probe diagnostics"), the answer ends with a fenced ```rb-regions block of JSON. The page shows it as a card with
Apply and Dismiss; nothing changes until you click, so a paper's text can never reorganize your map by itself.

    POST /api/regions/changes/preview  {changes}  what each change would do, by name and title, or why it can't
    POST /api/regions/changes/apply    {changes}  do them; every region file (and each changed main region) is put
                                                  aside first → {token, done}
    POST /api/regions/changes/undo     {token}    put the regions back as they were

A change (regions are named as the chat sees them; a name that isn't a region yet makes one):
    {"op": "move",   "ids": [note id], "to": "Region", "from": "Region"}   from omitted: out of every region
    {"op": "add",    "ids": [...], "to": "Region"}                          joins it, keeps its other regions
    {"op": "remove", "ids": [...], "from": "Region"}
    {"op": "rename", "region": "Old name", "to": "New name"}
    {"op": "main",   "ids": [...], "region": "Region"}                      drawn there when in several
Regions are never deleted from the chat (Remove takes papers out; an empty region can be deleted in Regions).
"""
from __future__ import annotations

import datetime as dt
import json
import re
import shutil
import uuid
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from . import topics as T

OPS = ("move", "add", "remove", "rename", "main")
MAX_CHANGES = 40
MAX_IDS = 400

# for the chat's instruction and context (app/server.py)
INSTRUCTION = (
    "\n\nREGION CHANGES. Only if the QUESTION explicitly asks you to change the student's regions (move, add or remove "
    "papers, make a new region, rename one, or choose the main region a paper is drawn in): say in a sentence or two "
    "what you'd change, then end your answer with ONE fenced block, language rb-regions, holding JSON "
    "{\"changes\": [...]}, each change one of: "
    "{\"op\":\"move\",\"ids\":[...],\"to\":\"Region\",\"from\":\"Region\"} (from: the region it leaves; omit it to take "
    "it out of all its regions), {\"op\":\"add\",\"ids\":[...],\"to\":\"Region\"} (also in that region), "
    "{\"op\":\"remove\",\"ids\":[...],\"from\":\"Region\"}, {\"op\":\"rename\",\"region\":\"Old\",\"to\":\"New\"}, "
    "{\"op\":\"main\",\"ids\":[...],\"region\":\"Region\"}. Use region names exactly as REGIONS lists them (a new name "
    "makes a new region) and note ids exactly as listed; never invent ids. Nothing changes until the student clicks "
    "Apply, so don't say it's done. Never output this block when they didn't ask for a change.")
ASKS_RE = re.compile(r"\b(regions?|sections?|groups?|move|moving|put|sort|re-?organi[sz]e|rename|regroup|categori[sz]e|"
                     r"file (?:it|them|these)|main region)\b", re.I)


def wants_regions(question: str) -> bool:
    """Whether the question may be asking to change regions (then the chat gets REGIONS and the instruction)."""
    return bool(ASKS_RE.search(question or ""))


def context(store, by_id: dict, question: str, room: int = 24000) -> str:
    """REGIONS for the chat: each region with its papers ([[id]] title), then papers in no region. by_id is what
    the AI may see (private notes left out). Papers whose titles share words with the question come first when the
    library is too big to list whole."""
    tops = [t for t in T.list_topics(store, {})]
    words = {w for w in re.findall(r"[a-z0-9]{4,}", (question or "").lower())}
    score = lambda nid: -len(words & set(re.findall(r"[a-z0-9]{4,}", (by_id[nid].get("title") or "").lower())))
    lines, used, seen = ["REGIONS (the student's regions on the map, with their papers):"], 0, set()
    for t in tops:
        try:
            mem = [m for m in T.get_topic(store, t["slug"])["members"] if m in by_id]
        except (KeyError, T.TopicError):
            continue
        lines.append(f"\n## {t['name']} ({len(mem)})")
        for m in sorted(mem, key=score):
            seen.add(m)
            row = f"- [[{m}]] {(by_id[m].get('title') or m)[:110]}"
            if used + len(row) > room:
                lines.append(f"- (and more not listed)")
                break
            lines.append(row)
            used += len(row)
    loose = sorted((i for i, n in by_id.items() if i not in seen and n.get("type") in ("paper", "note", "question")), key=score)
    if loose:
        lines.append("\n## (in no region)")
        for m in loose:
            row = f"- [[{m}]] {(by_id[m].get('title') or m)[:110]}"
            if used + len(row) > room:
                lines.append("- (and more not listed)")
                break
            lines.append(row)
            used += len(row)
    return "\n".join(lines)


class ChangesIn(BaseModel):
    changes: list[dict] = []


class UndoIn(BaseModel):
    token: str = ""


def _topics(store) -> dict[str, dict]:
    return {t["name"].casefold(): t for t in T.list_topics(store, {})}


def _ids(store, raw) -> tuple[list[str], list[str]]:
    ok, bad = [], []
    for x in list(raw or [])[:MAX_IDS]:
        nid = str(x).strip().strip("[]")
        try:
            n = store.get(nid)
        except (KeyError, ValueError):
            bad.append(nid)
            continue
        if n.get("type") == "fragment":
            bad.append(nid)
        elif nid not in ok:
            ok.append(nid)
    return ok, bad


def _name(x) -> str:
    return " ".join(str(x or "").split())[:120]


def check(store, changes: list[dict]) -> list[dict]:
    """Each change, resolved: {op, ids, papers: [{id, title}], to, from, region, new (makes a region), problem}."""
    if not isinstance(changes, list) or not changes:
        raise HTTPException(400, "There are no changes to make.")
    tops = _topics(store)
    out = []
    for c in changes[:MAX_CHANGES]:
        c = c if isinstance(c, dict) else {}
        op = str(c.get("op") or "").lower()
        r = {"op": op, "ids": [], "papers": [], "problem": ""}
        if op not in OPS:
            r["problem"] = f"unknown change “{op}”"
            out.append(r)
            continue
        if op == "rename":
            old, new = _name(c.get("region") or c.get("from")), _name(c.get("to"))
            r.update(region=old, to=new)
            if old.casefold() not in tops:
                r["problem"] = f"there is no region called “{old}”"
            elif not new:
                r["problem"] = "no new name"
            elif new.casefold() in tops and new.casefold() != old.casefold():
                r["problem"] = f"there is already a region called “{new}”"
            out.append(r)
            continue
        ids, bad = _ids(store, c.get("ids"))
        r["ids"] = ids
        r["papers"] = [{"id": i, "title": store.get(i).get("title") or i} for i in ids]
        if not ids:
            r["problem"] = "none of those papers are in your library" if bad else "no papers named"
        to = _name(c.get("to")) if op in ("move", "add") else ""
        frm = _name(c.get("from")) if op in ("move", "remove") else ""
        reg = _name(c.get("region")) if op == "main" else ""
        if op in ("move", "add"):
            if not to:
                r["problem"] = r["problem"] or "no region to put them in"
            r["to"], r["new"] = to, bool(to) and to.casefold() not in tops
        if frm:
            r["from"] = frm
            if frm.casefold() not in tops:
                r["problem"] = r["problem"] or f"there is no region called “{frm}”"
        elif op == "remove":
            r["problem"] = r["problem"] or "no region to take them out of"
        if op == "main":
            r["region"] = reg
            if reg.casefold() not in tops:
                r["problem"] = r["problem"] or f"there is no region called “{reg}”"
        if bad and not r["problem"]:
            r["skipped"] = bad  # ids that aren't notes: left out, the rest goes ahead
        out.append(r)
    return out


def _snapshot(store, mains: dict[str, str | None]) -> str:
    token = dt.datetime.now().strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
    dest = Path(store.trash_dir) / f"topics-before-reorganize-{token}"  # same as Reorganize's: its undo works too
    dest.mkdir(parents=True, exist_ok=True)
    for f in T.topics_dir(store).glob("*.md"):
        shutil.copy2(f, dest / f.name)
    (dest / "main-regions.json").write_text(json.dumps(mains), encoding="utf-8")
    return token


def _main_of(store, nid: str) -> str | None:
    return (store.get(nid).get("_extra") or {}).get("main_region") or None


def _set_main(store, nid: str, slug: str | None) -> None:
    with store.lock:
        n = store.get(nid)
        extra = dict(n.get("_extra") or {})
        if slug:
            extra["main_region"] = slug
        else:
            extra.pop("main_region", None)
        if extra != (n.get("_extra") or {}):
            n["_extra"] = extra
            store.save(n, touch=False)


def apply(store, changes: list[dict]) -> dict:
    rows = check(store, changes)
    todo = [r for r in rows if not r["problem"]]
    if not todo:
        raise HTTPException(400, "Nothing here can be done: " + "; ".join(r["problem"] for r in rows if r["problem"]))
    touched = {i for r in todo for i in r.get("ids", [])}
    with T._lock:
        token = _snapshot(store, {i: _main_of(store, i) for i in touched})
        done = []
        for r in todo:
            tops = _topics(store)

            def slug_for(name: str) -> str:
                t = tops.get(name.casefold())
                if t:
                    return t["slug"]
                made = T.create_topic(store, name)
                tops[name.casefold()] = made
                return made["slug"]
            op, ids = r["op"], r.get("ids", [])
            if op == "rename":
                T.update_topic(store, tops[r["region"].casefold()]["slug"], name=r["to"])
            elif op in ("move", "add"):
                to = slug_for(r["to"])
                T.add_notes(store, to, ids)
                if op == "move":
                    if r.get("from"):
                        T.remove_notes(store, tops[r["from"].casefold()]["slug"], ids)
                    else:
                        for t in T.list_topics(store, {}):
                            if t["slug"] != to:
                                T.remove_notes(store, t["slug"], ids)
                    for i in ids:  # still in another region: it's drawn where it was sent
                        left = T.topics_for(store, i)
                        _set_main(store, i, to if len(left) > 1 else None)
            elif op == "remove":
                slug = tops[r["from"].casefold()]["slug"]
                T.remove_notes(store, slug, ids)
                for i in ids:
                    if _main_of(store, i) == slug or len(T.topics_for(store, i)) <= 1:
                        _set_main(store, i, None)
            elif op == "main":
                slug = tops[r["region"].casefold()]["slug"]
                for i in ids:
                    if any(t["slug"] == slug for t in T.topics_for(store, i)):
                        _set_main(store, i, slug)
            done.append(r)
    return {"token": token, "done": done, "skipped": [r for r in rows if r["problem"]]}


def undo(store, token: str) -> dict:
    if not re.fullmatch(r"[0-9-]+-[0-9a-f]{6}", token or ""):
        raise HTTPException(400, "That can't be undone.")
    src = Path(store.trash_dir) / f"topics-before-reorganize-{token}"
    if not src.is_dir():
        raise HTTPException(404, "That can't be undone any more.")
    with T._lock:
        d = T.topics_dir(store)
        _snapshot(store, {})  # what it looked like after, in case
        for f in d.glob("*.md"):
            f.unlink()
        for f in src.glob("*.md"):
            shutil.copy2(f, d / f.name)
        try:
            mains = json.loads((src / "main-regions.json").read_text(encoding="utf-8"))
        except (OSError, ValueError):
            mains = {}
        for nid, slug in mains.items():
            try:
                _set_main(store, nid, slug)
            except KeyError:
                pass
    return {"ok": True}


def install(app, *, store, publish=None):
    def changed():
        if publish:
            publish({"type": "topics-changed"})

    @app.post("/api/regions/changes/preview")
    def api_preview(body: ChangesIn):
        return {"changes": check(store, body.changes)}

    @app.post("/api/regions/changes/apply")
    def api_apply(body: ChangesIn):
        out = apply(store, body.changes)
        changed()
        return out

    @app.post("/api/regions/changes/undo")
    def api_undo(body: UndoIn):
        out = undo(store, body.token)
        changed()
        return out

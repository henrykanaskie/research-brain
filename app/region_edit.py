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

Subsections (app/subsections.py: named groups of papers inside a region, kept only in the app):
    {"op": "subsections", "region": "Region", "subsections": [{"name": "Sub", "ids": [...]}, ...]}
                                         the region's subsections become these (its old ones go; [] takes them away)
    {"op": "subadd",    "ids": [...], "to": "Sub", "region": "Region"}   a new name makes one; region picks among
    {"op": "subremove", "ids": [...], "from": "Sub", "region": "Region"}  subsections of the same name
    {"op": "subrename", "subsection": "Old", "to": "New", "region": "Region"}
    {"op": "subdelete", "subsection": "Sub", "region": "Region"}
Any region on the map can have subsections, also one the map found by similarity that isn't kept yet.
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

from . import subsections as SUB
from . import topics as T

OPS = ("move", "add", "remove", "rename", "main", "subsections", "subadd", "subremove", "subrename", "subdelete")
SUB_OPS = OPS[5:]
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
    "{\"op\":\"main\",\"ids\":[...],\"region\":\"Region\"}. "
    "SUBSECTIONS are named groups of papers inside one region (shown when the student focuses that region). To give a "
    "region subsections, or restructure them: {\"op\":\"subsections\",\"region\":\"Region\",\"subsections\":"
    "[{\"name\":\"Sub\",\"ids\":[...]}, ...]} (replaces that region's subsections; use 2 to 7 subsections of the "
    "region's own papers, each with a short specific name, and leave papers that fit none out). Smaller edits: "
    "{\"op\":\"subadd\",\"ids\":[...],\"to\":\"Sub\",\"region\":\"Region\"} (a new name makes one), "
    "{\"op\":\"subremove\",\"ids\":[...],\"from\":\"Sub\",\"region\":\"Region\"}, "
    "{\"op\":\"subrename\",\"subsection\":\"Old\",\"to\":\"New\",\"region\":\"Region\"}, "
    "{\"op\":\"subdelete\",\"subsection\":\"Sub\",\"region\":\"Region\"}. Moving papers between regions "
    "(move, add, remove, main) works on regions marked kept; subsections work on every region. "
    "Use region names exactly as REGIONS lists them (a new name makes a new region) and note ids exactly as listed; never invent ids. Nothing changes until the student clicks "
    "Apply, so don't say it's done. Never output this block when they didn't ask for a change.")
ASKS_RE = re.compile(r"\b(regions?|sections?|sub-?sections?|groups?|move|moving|put|sort|re-?organi[sz]e|rename|regroup|categori[sz]e|"
                     r"file (?:it|them|these)|main region)\b", re.I)


def wants_regions(question: str) -> bool:
    """Whether the question may be asking to change regions (then the chat gets REGIONS and the instruction)."""
    return bool(ASKS_RE.search(question or ""))


# the regions on the map, as app/server.py finds them: [{name, kept, members, subs: [{id, name, members}]}]
_MAP: list = [None]


def map_regions() -> list[dict]:
    try:
        return list(_MAP[0]() or []) if _MAP[0] else []
    except Exception:
        return []


def context(store, by_id: dict, question: str, room: int = 24000, regions: list[dict] | None = None) -> str:
    """REGIONS for the chat: each region with its papers ([[id]] title), grouped by its subsections, then papers in no
    region. by_id is what the AI may see (private notes left out). Papers whose titles share words with the question
    come first when the library is too big to list whole."""
    regions = map_regions() if regions is None else regions
    words = {w for w in re.findall(r"[a-z0-9]{4,}", (question or "").lower())}
    score = lambda nid: -len(words & set(re.findall(r"[a-z0-9]{4,}", (by_id[nid].get("title") or "").lower())))
    lines, seen = ["REGIONS (the student's regions on the map, with their papers):"], set()
    used = [0]

    def rows(ids) -> bool:
        for m in sorted(ids, key=score):
            seen.add(m)
            row = f"- [[{m}]] {(by_id[m].get('title') or m)[:110]}"
            if used[0] + len(row) > room:
                lines.append("- (and more not listed)")
                return False
            lines.append(row)
            used[0] += len(row)
        return True
    shown = set()
    for r in regions:
        mem = [m for m in r.get("members", []) if m in by_id]
        if not mem:
            continue
        shown.add(str(r.get("name") or "").casefold())
        kept = "kept" if r.get("kept") else "found by the map, not kept"
        lines.append(f"\n## {r['name']} ({len(mem)}, {kept})")
        inside = set()
        for sub in r.get("subs", []):
            sm = [m for m in sub.get("members", []) if m in by_id]
            if not sm:
                continue
            lines.append(f"### subsection: {sub['name']} ({len(sm)})")
            inside.update(sm)
            rows(sm)
        rest = [m for m in mem if m not in inside]
        if inside and rest:
            lines.append("### (in no subsection)")
        rows(rest)
    for t in T.list_topics(store, {}):  # kept regions the map doesn't draw as their own (too few papers, or merged)
        if t["name"].casefold() in shown:
            continue
        try:
            mem = [m for m in T.get_topic(store, t["slug"])["members"] if m in by_id]
        except (KeyError, T.TopicError):
            continue
        lines.append(f"\n## {t['name']} ({len(mem)}, kept)")
        rows(mem)
    loose = [i for i, n in by_id.items() if i not in seen and n.get("type") in ("paper", "note", "question")]
    if loose:
        lines.append("\n## (in no region)")
        rows(loose)
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


def _papers(store, ids: list[str]) -> list[dict]:
    return [{"id": i, "title": store.get(i).get("title") or i} for i in ids]


def _region_named(regions: list[dict], name: str) -> dict | None:
    k = name.casefold()
    return next((r for r in regions if str(r.get("name") or "").casefold() == k), None)


def _sub_named(subs: list[dict], regions: list[dict], name: str, region: str = "") -> dict | None:
    """The subsection called `name`: the one in `region` when that's given and has one, else the first."""
    k = name.casefold()
    same = [x for x in subs if x["name"].casefold() == k]
    if region and same:
        reg = _region_named(regions, region)
        mine = {x["id"] for x in (reg or {}).get("subs", [])}
        inside = [x for x in same if x["id"] in mine]
        if inside:
            return inside[0]
    return same[0] if same else None


def _check_sub(store, c: dict, r: dict, regions: list[dict], subs: list[dict]) -> None:
    op, region = r["op"], _name(c.get("region"))
    if region:
        r["region"] = region
    if op == "subsections":
        reg = _region_named(regions, region)
        if not region:
            r["problem"] = "no region named"
            return
        if not reg:
            r["problem"] = f"there is no region called “{region}” on the map"
            return
        r["replaces"] = [x["name"] for x in reg.get("subs", [])]
        r["replace_ids"] = [x["id"] for x in reg.get("subs", [])]
        made, bad_all = [], []
        for x in list(c.get("subsections") or [])[:40]:
            x = x if isinstance(x, dict) else {}
            nm = _name(x.get("name"))[:SUB.MAX_NAME]
            ids, bad = _ids(store, x.get("ids"))
            bad_all += bad
            if nm and ids:
                made.append({"name": nm, "ids": ids, "papers": _papers(store, ids)})
        r["subsections"] = made
        r["ids"] = [i for x in made for i in x["ids"]]
        if c.get("subsections") and not made:
            r["problem"] = "none of those subsections has a name and papers in your library"
        elif not made and not r["replaces"]:
            r["problem"] = "that region has no subsections to take away"
        if bad_all and not r["problem"]:
            r["skipped"] = bad_all
        return
    if op in ("subadd", "subremove"):
        ids, bad = _ids(store, c.get("ids"))
        r["ids"], r["papers"] = ids, _papers(store, ids)
        nm = _name(c.get("to") if op == "subadd" else c.get("from"))[:SUB.MAX_NAME]
        r["to" if op == "subadd" else "from"] = nm
        found = _sub_named(subs, regions, nm, region) if nm else None
        if found:
            r["sid"] = found["id"]
        if not ids:
            r["problem"] = "none of those papers are in your library" if bad else "no papers named"
        elif not nm:
            r["problem"] = "no subsection named"
        elif op == "subadd":
            r["new"] = not found
        elif not found:
            r["problem"] = f"there is no subsection called “{nm}”"
        if bad and not r["problem"]:
            r["skipped"] = bad
        return
    nm = _name(c.get("subsection") or c.get("from"))[:SUB.MAX_NAME]
    r["subsection"] = nm
    found = _sub_named(subs, regions, nm, region) if nm else None
    if found:
        r["sid"] = found["id"]
    if not found:
        r["problem"] = f"there is no subsection called “{nm}”" if nm else "no subsection named"
    elif op == "subrename":
        r["to"] = _name(c.get("to"))[:SUB.MAX_NAME]
        if not r["to"]:
            r["problem"] = "no new name"


def check(store, changes: list[dict], regions: list[dict] | None = None) -> list[dict]:
    """Each change, resolved: {op, ids, papers: [{id, title}], to, from, region, new (makes a region), problem}."""
    if not isinstance(changes, list) or not changes:
        raise HTTPException(400, "There are no changes to make.")
    tops = _topics(store)
    regions = map_regions() if regions is None else regions
    subs = SUB.load(store)
    out = []
    for c in changes[:MAX_CHANGES]:
        c = c if isinstance(c, dict) else {}
        op = str(c.get("op") or "").lower()
        r = {"op": op, "ids": [], "papers": [], "problem": ""}
        if op not in OPS:
            r["problem"] = f"unknown change “{op}”"
            out.append(r)
            continue
        if op in SUB_OPS:
            _check_sub(store, c, r, regions, subs)
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
    (dest / SUB.FILE).write_text(json.dumps(SUB.load(store), ensure_ascii=False), encoding="utf-8")
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


def _apply_sub(store, r: dict, made: dict[str, str]) -> None:
    """One subsection change. made: name (casefolded) -> id of the subsections this Apply made, so a later change can
    add to one made by an earlier one."""
    op = r["op"]
    with SUB._lock:
        items = SUB.load(store)
        by_id = {x["id"]: x for x in items}
        if op == "subsections":
            items = [x for x in items if x["id"] not in set(r.get("replace_ids", []))]
            for x in r["subsections"]:
                nx = {"id": SUB.new_id(), "name": x["name"], "members": list(x["ids"]), "at": dt.datetime.now().timestamp()}
                items.append(nx)
                made[x["name"].casefold()] = nx["id"]
        elif op == "subadd":
            sid = r.get("sid") if r.get("sid") in by_id else made.get(r["to"].casefold())
            if sid in by_id:
                by_id[sid]["members"] = list(dict.fromkeys(by_id[sid]["members"] + r["ids"]))
            else:
                nx = {"id": SUB.new_id(), "name": r["to"], "members": list(r["ids"]), "at": dt.datetime.now().timestamp()}
                items.append(nx)
                made[r["to"].casefold()] = nx["id"]
        elif op == "subremove" and r.get("sid") in by_id:
            gone = set(r["ids"])
            by_id[r["sid"]]["members"] = [m for m in by_id[r["sid"]]["members"] if m not in gone]
        elif op == "subrename" and r.get("sid") in by_id:
            by_id[r["sid"]]["name"] = r["to"]
        elif op == "subdelete":
            items = [x for x in items if x["id"] != r.get("sid")]
        SUB.save(store, items)


def apply(store, changes: list[dict]) -> dict:
    rows = check(store, changes)
    todo = [r for r in rows if not r["problem"]]
    if not todo:
        raise HTTPException(400, "Nothing here can be done: " + "; ".join(r["problem"] for r in rows if r["problem"]))
    touched = {i for r in todo for i in r.get("ids", [])}
    with T._lock:
        token = _snapshot(store, {i: _main_of(store, i) for i in touched})
        done, made = [], {}
        for r in todo:
            if r["op"] in SUB_OPS:
                _apply_sub(store, r, made)
                done.append(r)
                continue
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
        try:  # (put aside before subsections existed: left as they are)
            subs = json.loads((src / SUB.FILE).read_text(encoding="utf-8"))
        except (OSError, ValueError):
            subs = None
        if isinstance(subs, list):
            with SUB._lock:
                SUB.save(store, subs)
        for nid, slug in mains.items():
            try:
                _set_main(store, nid, slug)
            except KeyError:
                pass
    return {"ok": True}


def install(app, *, store, publish=None, regions=None):
    """regions: () -> the map's regions [{name, kept, members, subs: [{id, name, members}]}] (app/server.py)."""
    _MAP[0] = regions

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

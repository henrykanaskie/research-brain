"""HTTP API for folders of papers kept where they are (app/linked.py), and the job that keeps up with them.

    GET    /api/linked-folders                    your folders, with how many PDFs each and any job running
    POST   /api/linked-folders/preview            {path, names?}: the folders inside it and their topic names
    POST   /api/linked-folders/suggest-names      {path}: topic names from your AI (folder and file names only)
    POST   /api/linked-folders                    {path, names}: add it, then import its PDFs (a job)
    POST   /api/linked-folders/{id}/check         look for new, moved or deleted PDFs now (a job)
    GET    /api/linked-folders/jobs/{job}         a job's progress
    DELETE /api/linked-folders/{id}               stop following it (its papers stay, without their PDFs)
"""
from __future__ import annotations

import json
import re
import threading
import time
import traceback
import uuid
from pathlib import Path

from fastapi import HTTPException
from pydantic import BaseModel

from . import diskspace, fsio, linked, topics as topics_mod

CHECK_EVERY = 600   # seconds between checks of every folder while the app runs
HOLD_LIMIT = 500 << 20  # PDFs downloaded to read that couldn't be sent back online: pause past this much
SUGGEST_INSTRUCTION = (
    "A researcher keeps PDFs of papers in the FOLDERS below; each folder is listed with the file names in it. "
    "Each folder will become a topic in their library. Give each folder a clear topic name of 1 to 5 plain words, "
    "the way a researcher would label it, based on what the papers are about. Keep a folder's own name when it "
    "already says that well. Two folders about the same thing may get the same name. Reply with JSON only, no "
    'other text: {"names": {"<folder>": "Topic name"}}')


class PathIn(BaseModel):
    path: str
    names: dict[str, str] = {}


class Ctx:
    store = None
    import_one = None      # (path, fid, rel, size) -> (paper id, how, bytes held on this computer)
    read_later = None      # (paper id) -> True if a waiting paper's text was read
    held = None            # () -> bytes of cloud PDFs read but not sent back yet
    file_in_topic = None   # (paper id, topic name) -> slug
    ask = None             # (instruction, context, purpose) -> text
    publish = None


ctx = Ctx()
_jobs: dict[str, dict] = {}
_running: dict[str, str] = {}   # folder id -> job id
_lock = threading.Lock()


def install(app, *, store, import_one, file_in_topic, ask, publish, read_later=None, held=None):
    ctx.store, ctx.import_one, ctx.file_in_topic, ctx.ask, ctx.publish = store, import_one, file_in_topic, ask, publish
    ctx.read_later, ctx.held = read_later, held

    @app.get("/api/linked-folders")
    def api_linked_folders():
        links = ctx.store.links
        out = []
        for f in links.folders():
            files = links.files_in(f["id"])
            root = Path(f["path"])
            out.append({"id": f["id"], "path": f["path"], "name": root.name, "papers": len(files),
                        "there": root.is_dir(), "cloud": diskspace.cloud_provider(root), "added": f.get("added"),
                        "job": _jobs.get(_running.get(f["id"], "")) or None})
        return {"folders": out}

    @app.post("/api/linked-folders/preview")
    def api_linked_preview(body: PathIn):
        root = _valid(body.path)
        files = linked.scan(root)
        have = ctx.store.links.folder_for_path(root)
        names = {**((have or {}).get("names") or {}), **body.names}
        topics = [t["name"] for t in topics_mod.list_topics(ctx.store)]
        rows = linked.plan(root, files, names, topics)
        places = ctx.store.links.by_place()
        known = sum(1 for x in files if have and (have["id"], x["rel"]) in places)
        online = sum(1 for x in files if x.get("cloud"))
        return {"path": str(root), "name": root.name, "folders": rows, "pdfs": len(files), "known": known,
                "cloud": diskspace.cloud_provider(root), "limit": len(files) >= linked.MAX_FILES,
                "added": bool(have), "topics": topics, "online": online}

    @app.post("/api/linked-folders/suggest-names")
    def api_linked_suggest(body: PathIn):
        root = _valid(body.path)
        files = linked.scan(root)
        by_dir: dict[str, list[str]] = {}
        for x in files:
            by_dir.setdefault(x["dir"], []).append(Path(x["rel"]).stem)
        if not by_dir:
            return {"names": {}}
        rows = []
        for d, stems in by_dir.items():
            label = d or root.name
            sample = "; ".join(re.sub(r"[_\s]+", " ", s)[:120] for s in stems[:15])
            rows.append(f'FOLDER "{label}" ({len(stems)} PDFs): {sample}')
        text = ctx.ask(SUGGEST_INSTRUCTION, "FOLDERS:\n\n" + "\n".join(rows)[:60000], "topic names for folders")
        m = re.search(r"\{.*\}", text or "", re.S)
        try:
            got = (json.loads(m.group(0)).get("names") or {}) if m else {}
        except (ValueError, AttributeError):
            got = {}
        labels = {(d or root.name).casefold(): d for d in by_dir}
        last: dict[str, list[str]] = {}  # an answer naming a folder by its own name only ("Sheaths")
        for d in by_dir:
            last.setdefault((d.rpartition("/")[2] or root.name).casefold(), []).append(d)
        names = {}
        for k, v in got.items() if isinstance(got, dict) else []:
            name = " ".join(str(v or "").split())[:80]
            key = str(k).strip().strip("/").casefold()
            d = labels.get(key)
            if d is None and len(last.get(key, [])) == 1:
                d = last[key][0]
            if d is not None and name:
                names[d] = name
        if not names:
            raise HTTPException(502, "Your AI didn't suggest names this time. The folder names are used instead; "
                                     "you can still edit them.")
        return {"names": names}

    @app.post("/api/linked-folders", status_code=201)
    def api_linked_add(body: PathIn):
        root = _valid(body.path)
        try:
            f = ctx.store.links.add_folder(root, {k: " ".join(str(v).split())[:80] for k, v in body.names.items()})
        except ValueError as e:
            raise HTTPException(400, str(e))
        return {"folder": f, "job": start(f["id"])}

    @app.post("/api/linked-folders/{fid}/check")
    def api_linked_check(fid: str):
        if not ctx.store.links.folder(fid):
            raise HTTPException(404, "No such folder")
        return {"job": start(fid)}

    @app.get("/api/linked-folders/jobs/{jid}")
    def api_linked_job(jid: str):
        j = _jobs.get(jid)
        if not j:
            raise HTTPException(404, "No such job")
        return j

    @app.post("/api/linked-folders/jobs/{jid}/stop")
    def api_linked_stop(jid: str):
        j = _jobs.get(jid)
        if not j:
            raise HTTPException(404, "No such job")
        j["stop"] = True
        return j

    @app.delete("/api/linked-folders/{fid}")
    def api_linked_remove(fid: str):
        f = ctx.store.links.folder(fid)
        if not f:
            raise HTTPException(404, "No such folder")
        gone = ctx.store.links.remove_folder(fid)
        for nid in gone:  # their papers stay; they just have no PDF now
            try:
                ctx.store.save(ctx.store.get(nid), touch=False)
            except (KeyError, TypeError):
                pass
        _publish(gone)
        return {"ok": True, "papers": len(gone)}

    threading.Thread(target=_keep_up, daemon=True, name="rb-linked-folders").start()


def _valid(path: str) -> Path:
    p = Path((path or "").strip()).expanduser()
    if not p.is_absolute() or not p.is_dir():
        raise HTTPException(400, "Pick a folder that exists on this computer (or in OneDrive, synced to it).")
    p = p.resolve()
    store = ctx.store
    mine = [Path(store.root).resolve(), Path(store.docs).resolve(), Path(store.pdf_dir).resolve()]
    for m in mine:
        if p == m or m in p.parents or p in m.parents:
            raise HTTPException(400, "That folder is research brain's own (or holds it). Pick the folder where you "
                                     "keep your PDFs.")
    return p


def _publish(ids: list[str]) -> None:
    if ctx.publish and ids:
        try:
            ctx.publish({"type": "notes-changed", "ids": list(ids)[:500]})
        except Exception:
            pass


# ---------------------------------------------------------------- the job: add new PDFs, follow moved ones
def start(fid: str) -> dict:
    with _lock:
        jid = _running.get(fid)
        if jid and _jobs.get(jid, {}).get("state") in ("running", "paused"):
            return _jobs[jid]
        jid = uuid.uuid4().hex[:10]
        f = ctx.store.links.folder(fid) or {}
        job = {"id": jid, "folder": fid, "name": Path(f.get("path", "")).name, "state": "running", "total": 0,
               "done": 0, "added": 0, "linked": 0, "duplicate": 0, "moved": 0, "missing": 0, "unread": 0,
               "read": 0, "failed": [], "current": "", "message": "", "started": time.time(), "paused_s": 0.0}
        _jobs[jid] = job
        _running[fid] = jid
    threading.Thread(target=_run, args=(job,), daemon=True, name=f"rb-linked-{jid}").start()
    return job


def _run(job: dict) -> None:
    store, fid = ctx.store, job["folder"]
    changed: list[str] = []
    try:
        folder = store.links.folder(fid)
        if not folder:
            job["state"] = "done"
            return
        root = Path(folder["path"])
        if not root.is_dir():
            job["state"], job["message"] = "done", f"{root} isn't there right now (a drive or OneDrive not connected?)."
            return
        files = linked.scan(root)
        own = Path(store.pdf_dir).resolve()  # research brain's own papers folder, if it was put in here: not twice
        if own == root.resolve() or root.resolve() in own.parents:
            pre = own.relative_to(root.resolve()).as_posix() + "/"
            files = [x for x in files if not (x["rel"] + "/").startswith(pre) and not x["rel"].startswith(pre)]
        now = {x["rel"]: x for x in files}
        mine = store.links.files_in(fid)
        ignored = store.links.ignored(fid)
        gone = {nid: x for nid, x in mine.items() if x["rel"] not in now}
        known = {x["rel"] for x in mine.values()}
        new = [x for x in files if x["rel"] not in known and x["rel"] not in ignored]
        job["missing"] = len(gone)
        # moved: a PDF that left its place and one with the same name and size that appeared elsewhere
        by_key = {(Path(x["rel"]).name.casefold(), x.get("size")): nid for nid, x in gone.items()}
        rest = []
        for x in new:
            nid = by_key.pop((Path(x["rel"]).name.casefold(), x["size"]), None)
            if nid is None:
                rest.append(x)
                continue
            old_topic = linked.topic_for(folder, gone[nid]["rel"])
            store.links.link(nid, fid, x["rel"], x["size"])
            new_topic = linked.topic_for(folder, x["rel"])
            if new_topic.casefold() != old_topic.casefold():
                _leave_topic(nid, old_topic)
                if new_topic:
                    ctx.file_in_topic(nid, new_topic)
            job["moved"] += 1
            job["missing"] -= 1
            changed.append(nid)
        # papers added earlier while there was no room: read them now, one at a time (search and Ask need the text)
        waiting = [nid for nid in store.links.files_in(fid) if not store.text_path(nid).exists()
                   and nid not in gone]
        job["total"] = len(rest) + len(waiting)
        for nid in waiting:
            if job.get("stop"):
                break
            job["current"] = store.links.files_in(fid).get(nid, {}).get("rel", "")
            if not _wait_for_room(job, folder):
                break
            if ctx.read_later and ctx.read_later(nid):
                job["read"] += 1
                changed.append(nid)
            job["done"] += 1
        for x in rest:
            if job.get("stop"):
                break
            job["current"] = x["rel"]
            if x.get("cloud") and not _wait_for_room(job, folder):
                break
            try:
                diskspace.ensure_room(Path(store.text_dir), 2 << 20, "the text of this PDF")
                nid, how, _held = ctx.import_one(fsio.long(root / x["rel"]), fid, x["rel"], x["size"])  # past 260 characters too
                topic = linked.topic_for(folder, x["rel"])
                if topic:
                    ctx.file_in_topic(nid, topic)
                if how.endswith("_unread"):  # online only: added from its name, read when first opened
                    how = how[:-len("_unread")]
                    job["unread"] += 1
                job[how] += 1
                changed.append(nid)
            except diskspace.DiskFull as e:
                job["state"], job["message"] = "error", str(e)
                return
            except Exception as e:
                if diskspace.is_disk_full(e):
                    job["state"], job["message"] = "error", diskspace.FULL_MESSAGE
                    return
                if len(job["failed"]) < 50:
                    job["failed"].append(f"{x['rel']}: {str(e)[:200]}")
                traceback.print_exc()
            job["done"] += 1
            if len(changed) >= 20:
                _publish(changed)
                changed = []
        job["state"] = "stopped" if job.get("stop") else "done"
    except Exception as e:
        job["state"], job["message"] = "error", str(e)[:300]
        traceback.print_exc()
    finally:
        job["current"] = ""
        _publish(changed)


def _wait_for_room(job: dict, folder: dict) -> bool:
    """Before downloading the next online-only PDF: if too many read ones are still on this computer (a Mac where
    sending them back didn't take), pause until you free space on the folder; it goes on by itself. False if
    stopped meanwhile."""
    if not ctx.held:
        return True
    t0 = None
    while ctx.held() > HOLD_LIMIT:
        if job.get("stop"):
            return False
        if t0 is None:
            t0 = time.time()
            job["state"] = "paused"
            job["message"] = (f"Paused so this computer keeps its space: {diskspace.human(ctx.held())} of PDFs read from "
                              f"“{Path(folder['path']).name}” are still downloaded. In Finder, right-click that folder › "
                              "Free up space. Reading goes on by itself.")
        time.sleep(5)
    if t0 is not None:
        job["paused_s"] += time.time() - t0
        job["state"], job["message"] = "running", ""
    return not job.get("stop")


def _leave_topic(nid: str, name: str) -> None:
    if not name:
        return
    t = next((t for t in topics_mod.list_topics(ctx.store) if t["name"].casefold() == name.casefold()), None)
    if t:
        try:
            topics_mod.remove_note(ctx.store, t["slug"], nid)
        except (KeyError, topics_mod.TopicError):
            pass


def _keep_up() -> None:
    """Check every folder a little after the app starts, then every CHECK_EVERY seconds."""
    time.sleep(20)
    while True:
        try:
            for f in ctx.store.links.folders():
                start(f["id"])
        except Exception:
            traceback.print_exc()
        time.sleep(CHECK_EVERY)

"""Folders of papers kept where they are (app/linked.py, app/linked_api.py): subfolders become topics, the files
are never moved, copied, changed or deleted, and the library follows new, moved and deleted PDFs."""
import time
from pathlib import Path

from test_api import client, make_pdf  # noqa: F401  (fixture)


def _tree(root: Path) -> dict[str, bytes]:
    files = {
        "Plasma/Sheaths/bohm.pdf": make_pdf("Bohm criterion revisited in sheaths", "Ions enter the sheath at the sound speed."),
        "Plasma/Sheaths/debye.pdf": make_pdf("Debye length and sheath thickness", "Collisionless sheath scaling."),
        "Plasma/Thrusters/hall.pdf": make_pdf("Breathing mode in Hall thrusters", "Low frequency oscillations."),
        "Atomic/cross.pdf": make_pdf("Electron impact cross sections of argon", "R-matrix calculations."),
        "loose.pdf": make_pdf("A loose paper about plasma diagnostics", "Langmuir probes."),
    }
    for rel, data in files.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(data)
    return files


def _wait(client, job):
    for _ in range(200):
        if job["state"] not in ("running", "paused"):
            return job
        time.sleep(0.05)
        job = client.get(f"/api/linked-folders/jobs/{job['id']}").json()
    raise AssertionError(f"job still running: {job}")


def _topic_members(client, name):
    t = next((t for t in client.get("/api/topics").json() if t["name"] == name), None)
    return set(client.get(f"/api/topics/{t['slug']}").json()["members"]) if t else set()


def test_folders_of_papers_stay_where_they_are(client, tmp_path):
    from app import server
    root = tmp_path / "OneDrive" / "Research"
    files = _tree(root)

    pv = client.post("/api/linked-folders/preview", json={"path": str(root)}).json()
    assert pv["pdfs"] == 5 and pv["known"] == 0
    names = {f["dir"]: f["topic"] for f in pv["folders"]}
    assert names == {"": "Research", "Atomic": "Atomic", "Plasma/Sheaths": "Sheaths", "Plasma/Thrusters": "Thrusters"}

    r = client.post("/api/linked-folders", json={"path": str(root), "names": {"Atomic": "Cross sections", "": ""}})
    assert r.status_code == 201, r.text
    job = _wait(client, r.json()["job"])
    assert job["state"] == "done" and job["added"] == 5, job

    # every file is still there, byte for byte, and nothing was copied into the papers folder
    for rel, data in files.items():
        assert (root / rel).read_bytes() == data
    assert list(Path(server.store.pdf_dir).glob("*.pdf")) == []

    links = server.store.links.linked_ids()
    by_rel = {p.relative_to(root).as_posix(): nid for nid, p in links.items()}
    assert set(by_rel) == set(files)
    bohm = by_rel["Plasma/Sheaths/bohm.pdf"]
    n = client.get(f"/api/notes/{bohm}").json()
    assert n["has_pdf"] and server.store.pdf_path(bohm) == root / "Plasma/Sheaths/bohm.pdf"
    assert client.get(f"/pdf/{bohm}").content == files["Plasma/Sheaths/bohm.pdf"]  # the viewer opens it where it is

    assert _topic_members(client, "Sheaths") == {by_rel["Plasma/Sheaths/bohm.pdf"], by_rel["Plasma/Sheaths/debye.pdf"]}
    assert _topic_members(client, "Cross sections") == {by_rel["Atomic/cross.pdf"]}
    assert not any(t["name"] == "Research" for t in client.get("/api/topics").json())  # cleared: no topic

    # a PDF moved to another folder keeps its paper and changes topic; a new PDF is added to its folder's topic
    (root / "Plasma/Thrusters/debye.pdf").write_bytes((root / "Plasma/Sheaths/debye.pdf").read_bytes())
    (root / "Plasma/Sheaths/debye.pdf").unlink()
    (root / "Plasma/Thrusters/new.pdf").write_bytes(make_pdf("Anomalous transport in thrusters", "Bohm diffusion."))
    job = _wait(client, client.post(f"/api/linked-folders/{pv and server.store.links.folders()[0]['id']}/check").json()["job"])
    assert job["moved"] == 1 and job["added"] == 1, job
    debye = by_rel["Plasma/Sheaths/debye.pdf"]
    assert server.store.pdf_path(debye) == root / "Plasma/Thrusters/debye.pdf"
    assert debye in _topic_members(client, "Thrusters") and debye not in _topic_members(client, "Sheaths")

    # deleting a paper leaves its PDF in the folder (whatever was asked) and doesn't add it back
    hall = by_rel["Plasma/Thrusters/hall.pdf"]
    assert client.delete(f"/api/notes/{hall}?pdf=trash").status_code == 200
    assert (root / "Plasma/Thrusters/hall.pdf").read_bytes() == files["Plasma/Thrusters/hall.pdf"]
    fid = server.store.links.folders()[0]["id"]
    job = _wait(client, client.post(f"/api/linked-folders/{fid}/check").json()["job"])
    assert job["added"] == 0, job

    # a PDF deleted from the folder: its paper stays, without a PDF
    (root / "loose.pdf").unlink()
    server.store.meta.sync(force=True) if getattr(server.store, "meta", None) else None
    loose = by_rel["loose.pdf"]
    assert client.get(f"/api/notes/{loose}").json()["has_pdf"] is False

    # stop following: the papers stay, without their PDFs; the folder is untouched
    assert client.delete(f"/api/linked-folders/{fid}").json()["ok"]
    assert client.get(f"/api/notes/{bohm}").json()["has_pdf"] is False
    assert (root / "Plasma/Sheaths/bohm.pdf").exists()


def test_the_same_pdf_twice_is_one_paper_in_both_topics(client, tmp_path):
    root = tmp_path / "Papers"
    data = make_pdf("One paper in two folders", "Same bytes.")
    for d in ("Reading", "Thesis"):
        (root / d).mkdir(parents=True)
        (root / d / "same.pdf").write_bytes(data)
    job = _wait(client, client.post("/api/linked-folders", json={"path": str(root)}).json()["job"])
    assert job["added"] == 1 and job["duplicate"] == 1, job
    assert len(_topic_members(client, "Reading") | _topic_members(client, "Thesis")) == 1
    assert _topic_members(client, "Reading") == _topic_members(client, "Thesis")


def test_unsafe_folders_are_refused(client, tmp_path):
    from app import server
    root = tmp_path / "Research"
    (root / "Inner").mkdir(parents=True)
    (root / "Inner" / "a.pdf").write_bytes(make_pdf("Overlap test paper", "Text."))
    assert client.post("/api/linked-folders", json={"path": str(root)}).status_code == 201
    r = client.post("/api/linked-folders", json={"path": str(root / "Inner")})
    assert r.status_code == 400 and "overlaps" in r.json()["detail"]
    r = client.post("/api/linked-folders/preview", json={"path": str(server.store.docs)})
    assert r.status_code == 400
    r = client.post("/api/linked-folders/preview", json={"path": str(tmp_path / "nope")})
    assert r.status_code == 400


def test_ai_names_come_from_folder_and_file_names_only(client, tmp_path, monkeypatch):
    from app import linked_api
    root = tmp_path / "Lab"
    _tree(root)
    seen = {}

    def ask(instruction, context, purpose):
        seen["context"] = context
        return '{"names": {"Sheaths": "Plasma sheaths", "Atomic": "Atomic data", "Lab": "General"}}'
    monkeypatch.setattr(linked_api.ctx, "ask", ask)
    r = client.post("/api/linked-folders/suggest-names", json={"path": str(root)}).json()
    assert r["names"] == {"Plasma/Sheaths": "Plasma sheaths", "Atomic": "Atomic data", "": "General"}
    assert "bohm" in seen["context"] and "Ions enter" not in seen["context"]  # names, never the PDFs' text


def _cloud_tree(tmp_path):
    root = tmp_path / "OneDrive" / "Reading"
    (root / "Sheaths").mkdir(parents=True)
    (root / "Sheaths" / "Kinetic theory of the plasma sheath.pdf").write_bytes(make_pdf("Kinetic theory of the plasma sheath", "The Bohm criterion from kinetic theory."))
    (root / "Sheaths" / "10.1103_PhysRevA.106.032809.pdf").write_bytes(make_pdf("Bethe stopping power formula", "Stopping power of matter."))
    return root


def test_online_only_pdfs_are_read_one_at_a_time_and_sent_back(client, tmp_path, monkeypatch):
    """Online-only PDFs (OneDrive, space freed up) are each downloaded, read and sent straight back online, so every
    paper has its full text and only the text stays on the computer."""
    from app import acquire, diskspace, server
    root = _cloud_tree(tmp_path)
    monkeypatch.setattr(acquire, "cloud_only", lambda st: True)
    events = []
    real_extract = server.extract
    monkeypatch.setattr(server, "extract", lambda p, *a, **kw: events.append(("read", Path(p).name)) or real_extract(p, *a, **kw))
    monkeypatch.setattr(diskspace, "give_back", lambda p, confirm=4.0: events.append(("back", Path(p).name)) or True)

    pv = client.post("/api/linked-folders/preview", json={"path": str(root)}).json()
    assert pv["online"] == 2
    job = _wait(client, client.post("/api/linked-folders", json={"path": str(root)}).json()["job"])
    assert job["added"] == 2 and job["unread"] == 0, job
    # each one read, then sent back, before the next is touched
    assert [e[0] for e in events] == ["read", "back", "read", "back"]
    assert events[0][1] == events[1][1] and events[2][1] == events[3][1]
    for nid in server.store.links.linked_ids():
        assert server.store.text_path(nid).exists()
    assert server.held_bytes() == 0


def test_with_no_room_a_paper_waits_and_is_read_when_needed(client, tmp_path, monkeypatch):
    from app import acquire, diskspace, server
    root = _cloud_tree(tmp_path)
    monkeypatch.setattr(acquire, "cloud_only", lambda st: True)
    monkeypatch.setattr(diskspace, "give_back", lambda p, confirm=4.0: True)
    real_room = diskspace.ensure_room
    room = {"pdf": False}

    def no_room_for_pdfs(path, need, what="this PDF"):  # room for text, not for downloading a PDF
        if what == "this PDF" and not room["pdf"]:
            raise diskspace.DiskFull("no room")
        return real_room(path, need, what)
    monkeypatch.setattr(diskspace, "ensure_room", no_room_for_pdfs)
    job = _wait(client, client.post("/api/linked-folders", json={"path": str(root)}).json()["job"])
    assert job["added"] == 2 and job["unread"] == 2, job
    by_rel = {p.relative_to(root).as_posix(): nid for nid, p in server.store.links.linked_ids().items()}
    kin = server.store.get(by_rel["Sheaths/Kinetic theory of the plasma sheath.pdf"])
    assert kin["title"] == "Kinetic theory of the plasma sheath"  # from its file name, for now
    assert server.store.get(by_rel["Sheaths/10.1103_PhysRevA.106.032809.pdf"])["doi"].lower() == "10.1103/physreva.106.032809"

    # room again: asking about it reads it on the spot; the next check reads the rest
    room["pdf"] = True
    assert "Bohm criterion" in server._fulltext_needed(kin["id"])
    fid = server.store.links.folders()[0]["id"]
    job = _wait(client, client.post(f"/api/linked-folders/{fid}/check").json()["job"])
    assert job["read"] == 1, job
    assert all(server.store.text_path(n).exists() for n in by_rel.values())


def test_a_mac_that_cannot_send_pdfs_back_pauses_then_goes_on(client, tmp_path, monkeypatch):
    """If sending a read PDF back online doesn't take, research brain stops downloading once a little is held, says
    so, and goes on by itself when you free space on the folder."""
    from app import acquire, diskspace, linked_api, server
    root = _cloud_tree(tmp_path)
    downloaded = set()  # inodes read (so now on the computer) until the user frees space
    monkeypatch.setattr(acquire, "cloud_only", lambda st: st.st_ino not in downloaded)
    real_extract = server.extract
    monkeypatch.setattr(server, "extract", lambda p, *a, **kw: downloaded.add(Path(p).stat().st_ino) or real_extract(p, *a, **kw))
    monkeypatch.setattr(diskspace, "give_back", lambda p, confirm=4.0: False)
    monkeypatch.setattr(linked_api, "HOLD_LIMIT", 1)  # pause after the first one
    real_sleep = time.sleep
    monkeypatch.setattr(linked_api.time, "sleep", lambda s: real_sleep(0.05))
    job = client.post("/api/linked-folders", json={"path": str(root)}).json()["job"]
    for _ in range(200):
        job = client.get(f"/api/linked-folders/jobs/{job['id']}").json()
        if job["state"] == "paused":
            break
        real_sleep(0.05)
    assert job["state"] == "paused" and "Free up space" in job["message"], job
    assert job["done"] == 1 and server.held_bytes() > 0
    downloaded.clear()  # Finder › Free up space: the read PDF is online only again
    job = _wait(client, job)
    assert job["state"] == "done" and job["added"] == 2, job

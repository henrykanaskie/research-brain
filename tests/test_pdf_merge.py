"""One paper, one PDF (app/pdfmerge.py): a paper added without its PDF and the PDF imported later as a second paper
are joined (POST /api/notes/{a}/take-pdf), and a PDF imported later whose title is a paper's you have without a PDF
goes to that paper by itself."""
import time

from test_api import client, make_pdf  # noqa: F401  (fixture)

TITLE = "Ion acoustic instabilities in magnetized plasma sheaths"


def _import(client, pdf, name="paper.pdf"):
    r = client.post("/api/import/pdf", files={"file": (name, pdf, "application/pdf")})
    assert r.status_code == 201, r.text
    return r.json()


def _setup(client):
    """A: a paper without a PDF, in a region, linked to a note, with a highlight. B: the same paper's PDF, imported
    separately (a different title, so it isn't matched by itself), with a highlight, a link and a [[mention]]."""
    a = client.post("/api/notes", json={"type": "paper", "title": TITLE, "authors": ["K. Kajita"], "year": 2025,
                                        "doi": "10.1000/kajita"}).json()["id"]
    other = client.post("/api/notes", json={"type": "note", "title": "Reading list"}).json()["id"]
    client.post(f"/api/notes/{a}/link", json={"target": other})
    client.post("/api/notes", json={"type": "fragment", "parent": a, "quote": "From the abstract", "body": "mine"})
    topic = client.post("/api/topics", json={"name": "Sheaths", "members": [a]}).json()["slug"]

    b = _import(client, make_pdf("Draft manuscript sheath instability notes", "The ion acoustic wave grows near the wall."))
    assert b["has_pdf"] and b["id"] != a
    b = b["id"]
    third = client.post("/api/notes", json={"type": "paper", "title": "Wall probes"}).json()["id"]
    client.post(f"/api/notes/{b}/link", json={"target": third})
    hl = client.post("/api/notes", json={"type": "fragment", "parent": b, "quote": "The ion acoustic wave grows",
                                         "body": "key result", "page": 1}).json()["id"]
    client.put(f"/api/notes/{b}", json={"status": "read", "tags": ["waves"]})
    client.put(f"/api/notes/{third}", json={"body": f"See [[{b}]] and [[Draft manuscript sheath instability notes|the draft]]."})
    client.post("/api/topics", json={"name": "Waves", "members": [b]})
    return a, b, other, third, hl, topic


def test_take_pdf_moves_everything_and_removes_the_copy(client):
    from app import server
    a, b, other, third, hl, topic = _setup(client)
    pdf_bytes = client.get(f"/pdf/{b}").content
    assert pdf_bytes.startswith(b"%PDF")

    # the picker: papers with a PDF for A, papers without one for B, closest title first
    m = client.get(f"/api/notes/{a}/pdf-matches").json()
    assert [r["id"] for r in m["items"]] == [b] and not m["has_pdf"]
    m = client.get(f"/api/notes/{b}/pdf-matches").json()
    rows = {r["id"]: r for r in m["items"]}
    assert m["has_pdf"] and set(rows) == {a, third}  # papers only, and only those without a PDF
    assert rows[a]["authors"] == ["K. Kajita"] and rows[a]["year"] == 2025
    assert [r["id"] for r in client.get(f"/api/notes/{third}/pdf-matches").json()["items"]] == [b]
    m = client.get(f"/api/notes/{b}/pdf-matches", params={"q": "ion acoustic"}).json()
    assert [r["id"] for r in m["items"]] == [a]
    assert [r["id"] for r in client.get(f"/api/notes/{b}/pdf-matches", params={"q": "kajita"}).json()["items"]] == [a]

    r = client.post(f"/api/notes/{a}/take-pdf", json={"from": b})
    assert r.status_code == 200, r.text
    n = r.json()
    assert n["has_pdf"] and n["has_text"] and n["merged"]["from"] == b
    assert n["title"] == TITLE and n["doi"] == "10.1000/kajita" and n["year"] == 2025  # A keeps its own fields
    assert n["status"] == "read" and "waves" in n["tags"]  # filled from B (A was only "to read")
    assert client.get(f"/pdf/{a}").content == pdf_bytes
    assert "ion acoustic wave grows" in server.store.text_path(a).read_text(encoding="utf-8")

    # B is gone; its highlight, links, mention and regions are A's
    assert client.get(f"/api/notes/{b}").status_code == 404
    assert client.get(f"/api/notes/{hl}").json()["parent"] == a
    kids = {k["id"] for k in server.store.children(a)}
    assert hl in kids and len(kids) == 2
    full = client.get(f"/api/notes/{a}").json()
    assert set(full["links"]) >= {other, third}
    assert a in client.get(f"/api/notes/{third}").json()["links"]
    assert b not in client.get(f"/api/notes/{third}").json()["links"]
    body = client.get(f"/api/notes/{third}").json()["body"]
    assert f"[[{a}|{b}]]" in body and f"[[{a}|the draft]]" in body
    names = {t["name"] for t in full["topics"]}
    assert names == {"Sheaths", "Waves"}
    for t in client.get("/api/topics").json():
        assert b not in client.get(f"/api/topics/{t['slug']}").json()["members"]
    ids = {x["id"] for x in client.get("/api/graph").json()["nodes"]}
    assert a in ids and b not in ids

    # the PDF is stored once, under A; importing it again opens A
    assert not server.store.pdf_path(b).exists()
    again = _import(client, pdf_bytes, "again.pdf")
    assert again["id"] == a and again.get("duplicate")


def test_take_pdf_refuses(client):
    a, b, other, *_ = _setup(client)
    with_pdf = _import(client, make_pdf("Langmuir probe sweeps in argon discharges", "Probe text."))["id"]
    assert client.post(f"/api/notes/{with_pdf}/take-pdf", json={"from": b}).status_code == 409  # already has one
    assert client.post(f"/api/notes/{a}/take-pdf", json={"from": other}).status_code == 400   # a note, not a paper
    assert client.post(f"/api/notes/{other}/take-pdf", json={"from": b}).status_code == 400
    assert client.post(f"/api/notes/{a}/take-pdf", json={"from": a}).status_code == 400
    third = client.post("/api/notes", json={"type": "paper", "title": "No PDF either"}).json()["id"]
    assert client.post(f"/api/notes/{a}/take-pdf", json={"from": third}).status_code == 400   # nothing to give
    assert client.post(f"/api/notes/{a}/take-pdf", json={"from": "nope"}).status_code == 404
    assert client.get(f"/api/notes/{b}").status_code == 200  # nothing was changed
    assert not client.get(f"/api/notes/{a}").json()["has_pdf"]


def test_import_attaches_a_pdf_to_the_paper_with_its_title(client):
    a = client.post("/api/notes", json={"type": "paper", "title": TITLE, "authors": ["K. Kajita"]}).json()["id"]
    # a short title never matches by itself, and nor does a paper that already has a PDF
    short = client.post("/api/notes", json={"type": "paper", "title": "Sheaths"}).json()["id"]
    n = _import(client, make_pdf("Ion acoustic instabilities in magnetized plasma sheaths.", "Waves near the wall."))
    assert n["id"] == a and n["has_pdf"] and n.get("title_match")
    assert client.get(f"/api/notes/{a}").json()["has_pdf"]
    n2 = _import(client, make_pdf("Sheaths", "Other text."))
    assert n2["id"] != short
    n3 = _import(client, make_pdf("Ion acoustic instabilities in magnetized plasma sheaths", "A different file."))
    assert n3["id"] != a  # A has its PDF now: a different file is a new paper

    # a paper whose own DOI says it's another paper is not matched
    b = client.post("/api/notes", json={"type": "paper", "title": "Electron emission from hot tungsten surfaces",
                                        "doi": "10.1000/other"}).json()["id"]
    pdf = make_pdf("Electron emission from hot tungsten surfaces", "doi:10.1000/different")
    n4 = _import(client, pdf)
    assert n4["id"] != b


def test_followed_folder_pdf_matches_by_title_and_merges(client, tmp_path):
    """A PDF in a folder of papers you follow goes to the paper with its title; and taking a followed PDF keeps
    following that file, as the other paper's."""
    from app import server
    a = client.post("/api/notes", json={"type": "paper", "title": TITLE}).json()["id"]
    root = tmp_path / "Research"
    root.mkdir()
    (root / "kajita.pdf").write_bytes(make_pdf(TITLE, "Waves near the wall."))
    (root / "draft.pdf").write_bytes(make_pdf("Collisionless shocks in laboratory plasma jets", "Shock text."))
    job = client.post("/api/linked-folders", json={"path": str(root), "names": {"": ""}}).json()["job"]
    for _ in range(200):
        if job["state"] not in ("running", "paused"):
            break
        time.sleep(0.05)
        job = client.get(f"/api/linked-folders/jobs/{job['id']}").json()
    assert job["state"] == "done", job
    links = {p.name: nid for nid, p in server.store.links.linked_ids().items()}
    assert links["kajita.pdf"] == a

    c = client.post("/api/notes", json={"type": "paper", "title": "Shocks, a paper added from its DOI",
                                        "doi": "10.1000/shocks"}).json()["id"]
    b = links["draft.pdf"]
    r = client.post(f"/api/notes/{c}/take-pdf", json={"from": b})
    assert r.status_code == 200, r.text
    assert server.store.pdf_path(c) == root / "draft.pdf" and (root / "draft.pdf").exists()
    links = {p.name: nid for nid, p in server.store.links.linked_ids().items()}
    assert links["draft.pdf"] == c
    fid = server.store.links.folders()[0]["id"]
    assert "draft.pdf" not in server.store.links.ignored(fid)
    job = client.post(f"/api/linked-folders/{fid}/check").json()["job"]
    for _ in range(200):
        if job["state"] not in ("running", "paused"):
            break
        time.sleep(0.05)
        job = client.get(f"/api/linked-folders/jobs/{job['id']}").json()
    assert job.get("added", 0) == 0, job  # not added again as a new paper


def test_take_pdf_moves_side_tables(client):
    """Idea cards, chats, open-question evidence, AI connections and open counts of the PDF's paper follow it."""
    import json
    from app import cards, chats, connect_api, questions, server
    a, b, other, third, hl, topic = _setup(client)
    card = cards.card_store().add(b, {"kind": "claim", "text": "Waves grow near the wall."})
    chats.save(server.store, f"paper-{b}", [{"role": "user", "content": "What does it find?"}], title="About it")
    q = client.post("/api/notes", json={"type": "question", "title": "Why do sheath waves grow?"}).json()["id"]
    ev = {"items": [{"id": "e1", "key": f"paper:{b}", "kind": "paper", "ref": {"note": b}, "verdict": "supports"},
                    {"id": "e2", "key": f"passage:{b}:1::abc", "kind": "passage", "ref": {"note": b, "page": 1},
                     "verdict": "new"}]}
    questions._save(server.store, q, ev)
    lonely = client.post("/api/notes", json={"type": "paper", "title": "Sheath waves in simulations"}).json()["id"]
    with connect_api._cache_lock:
        cache = connect_api._load_cache()
        cache[connect_api._key(b, lonely)] = {"v": 1, "why": "same waves"}
        connect_api._save_cache(cache)
    client.post(f"/api/notes/{b}/opened")
    client.post(f"/api/notes/{a}/opened")

    assert client.post(f"/api/notes/{a}/take-pdf", json={"from": b}).status_code == 200
    assert [c["id"] for c in cards.card_store().cards(a)] == [card["id"]]
    assert chats.load(server.store, f"paper-{a}")["title"] == "About it"
    assert not (chats._dir(server.store) / f"paper-{b}.json").exists()
    items = json.loads(questions._path(server.store, q).read_text(encoding="utf-8"))["items"]
    assert {i["key"] for i in items} == {f"paper:{a}", f"passage:{a}:1::abc"}
    assert all(i["ref"]["note"] == a for i in items)
    ai = connect_api.ai_links({n["id"]: n for n in server.store.all()})
    assert [(x["a"], x["b"]) for x in ai] == [tuple(sorted((a, lonely)))]
    assert server.store.load_json("engagement.json").get(a) == 2

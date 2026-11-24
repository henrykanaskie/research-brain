"""A paper's title and authors checked against its first page by the AI when they look wrong (app/details.py):
an affiliation, a name or "Manuscript" as the title, a title cut off, no authors. Only what is really printed on the
page is used, a good title is never touched, and a private paper is never sent."""
import base64
import json

from app import details
from test_api import client, make_pdf  # noqa: F401  (fixture)


def P(title, authors=("Jane Doe",)):
    return {"type": "paper", "title": title, "authors": list(authors)}


def test_what_looks_wrong():
    assert details.suspect(P("*Plasma Propulsion Laboratory, Department of Aeronautics, Imperial College London")) == \
        ["the title looks like an affiliation"]
    assert details.suspect(P("Kazuma Emoto1", ["Kazuma Emoto"])) == ["the title looks like a name"]
    assert details.suspect(P("Manuscript")) == ["no real title"]
    assert details.suspect(P("Sheath Analysis of Conducting Channel Walls in Anode-Layer Hall Thruster with")) == \
        ["the title looks cut off"]
    assert details.suspect(P("Attention Is All You Need", [])) == ["no authors"]
    for good in ("Attention Is All You Need", "Machine Learning for Plasma Physics",
                 r"Cross-field chaotic transport of electrons by $\vec E \times \vec B$ drift in Hall thrusters"):
        assert details.suspect(P(good)) == [], good
    assert details.suspect({"type": "note", "title": "Manuscript", "authors": []}) == []  # papers only


def test_only_what_is_printed_on_the_page_is_kept():
    page = ("Sheath Analysis of Conducting Channel Walls in Anode-Layer Hall Thruster with Acceleration Zone Shifted "
            "Downstream\nRei KAWASHIMA*\n*Department of Electrical Engineering, Shibaura Institute of Technology\n"
            "doi: 10.2322/tastj.16.1  2018")
    got = details.on_page({"title": "Sheath Analysis of Conducting Channel Walls in Anode-Layer Hall Thruster with "
                                    "Acceleration Zone Shifted Downstream",
                           "authors": ["Rei Kawashima*", "Made Up Person"], "year": 2018, "doi": "10.2322/tastj.16.1"}, page)
    assert got == {"title": "Sheath Analysis of Conducting Channel Walls in Anode-Layer Hall Thruster with Acceleration "
                            "Zone Shifted Downstream", "authors": ["Rei Kawashima"], "year": 2018, "doi": "10.2322/tastj.16.1"}
    # invented: a title not on the page, a DOI not on the page, a year not on it
    assert details.on_page({"title": "Quantum gravity and the sheath of black holes", "doi": "10.9999/fake.1",
                            "year": 1999}, page) == {}
    # a title that is itself broken isn't taken
    assert details.on_page({"title": "Department of Electrical Engineering, Shibaura Institute of Technology"}, page) == {}


def _ask_for(answer: dict) -> str:
    return "FAKE-ANSWER:" + base64.urlsafe_b64encode(json.dumps(answer).encode()).decode()


def test_check_fixes_a_paper_from_its_first_page(client):
    from app import server
    store = server.store
    title = "Electron impact excitation of krypton metastable levels"
    answer = {"title": title, "authors": ["Oleg Zatsarinny", "Klaus Bartschat"], "year": 2010, "doi": None}
    body = f"{title}\nOleg Zatsarinny and Klaus Bartschat\nDrake University 2010\n{_ask_for(answer)}"
    r = client.post("/api/import/pdf?quick=1", files={"file": ("k.pdf", make_pdf("Department of Physics, Drake University", body), "application/pdf")}).json()
    nid = r["id"]
    n = store.get(nid)
    n["title"], n["authors"] = "Department of Physics, Drake University", []
    store.save(n)
    assert details.suspect(store.get(nid))
    out = client.post(f"/api/notes/{nid}/check-details").json()
    assert out["changed"] and out["fields"]["title"] == title, out
    n = store.get(nid)
    assert n["title"] == title and n["authors"] == ["Oleg Zatsarinny", "Klaus Bartschat"] and n["year"] == 2010
    assert client.post(f"/api/notes/{nid}/check-details").json() == {"changed": False, "reasons": [], "fields": {}}

    # the AI saying something that isn't on the page changes nothing
    made_up = {"title": "A completely different paper about tokamak divertors", "authors": ["Nobody Here"]}
    body2 = f"Hall thruster\nsome text\n{_ask_for(made_up)}"
    nid2 = client.post("/api/import/pdf?quick=1", files={"file": ("h.pdf", make_pdf("Hall thruster", body2), "application/pdf")}).json()["id"]
    n2 = store.get(nid2)
    n2["title"], n2["authors"] = "Hall thruster", []
    store.save(n2)
    out = client.post(f"/api/notes/{nid2}/check-details").json()
    assert not out["changed"] and store.get(nid2)["title"] == "Hall thruster"

    # a private paper is never sent
    client.put(f"/api/notes/{nid2}", json={"private": True})
    sent = []
    real = details.ctx.ask
    details.ctx.ask = lambda *a, **k: sent.append(a) or real(*a, **k)
    try:
        out = client.post(f"/api/notes/{nid2}/check-details").json()
    finally:
        details.ctx.ask = real
    assert out["skipped"] == "private" and sent == []

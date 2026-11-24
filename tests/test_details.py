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


def test_more_broken_titles_from_a_real_library():
    """Titles seen in a real library: a publisher's code, a cover sheet, a conference line, the authors' names."""
    for bad, why in [("PII: S0010-4655(00)00231-9", "the title isn't the paper's title"),
                     ("REPORT DOCUMENTATION PAGE Form Approved", "the title isn't the paper's title"),
                     ("Download details:", "the title isn't the paper's title"),
                     ("Presented at the 36th International Electric Propulsion Conference", "the title isn't the paper's title"),
                     ("A Crowe and J W McConkey", "the title looks like a name"),
                     ("Smith, J. and Lee, K.", "the title looks like a name")]:
        assert details.suspect(P(bad)) == [why], bad
    for good in ("Fast Optical Emission Spectroscopy for Studying Plasma Behavior", "A Model of Hall Thruster Erosion",
                 "Electron-impact excitation of argon: a B-spline R-matrix approach", "Collisional-Radiative Models of "
                 "Neutral and Singly-Ionized Krypton", "J and K mixing in the 5p configuration",
                 "'Dissociative attachment' in N2", "CO2 laser heating"):
        assert details.suspect(P(good)) == [], good
    assert details.split_authors("A. Smith; B. Lee") == ["A. Smith", "B. Lee"]
    assert details.split_authors("Oleg Zatsarinny, Klaus Bartschat and Jane Doe") == ["Oleg Zatsarinny", "Klaus Bartschat", "Jane Doe"]
    assert details.split_authors("Smith, A. B.") == ["A. B. Smith"]
    assert details.split_authors("") == []
    assert details.misprinted_doi("10.1088/00223727/42/28/185203") == "10.1088/0022-3727/42/28/185203"
    assert details.misprinted_doi("10.1103/PhysRevA.81.052705") == ""


def _pdf_with_info(title_on_page: str, body: str, author: str, meta_title: str = "") -> bytes:
    import io
    from reportlab.lib.pagesizes import letter
    from reportlab.pdfgen import canvas
    buf = io.BytesIO()
    c = canvas.Canvas(buf, pagesize=letter)
    c.setTitle(meta_title)
    c.setAuthor(author)
    c.setFont("Helvetica", 10)
    y = 720
    for line in [title_on_page] + body.split("\n"):
        c.drawString(72, y, line)
        y -= 14
    c.showPage()
    c.save()
    return buf.getvalue()


def test_the_pdfs_own_authors_fill_in_without_an_ai_and_the_library_is_checked_once(client, monkeypatch):
    """A paper with no authors gets the authors its PDF declares, when they're printed on its first pages, with no AI.
    Papers already in the library are checked too, once each: what was checked is remembered by title and authors."""
    from app import server
    store, dm = server.store, server.details_mod
    title = "Electron impact excitation of krypton metastable levels"
    body = "Oleg Zatsarinny and Klaus Bartschat\nDepartment of Physics, Drake University\n2010 some text about krypton"
    nid = client.post("/api/import/pdf?quick=1", files={"file": ("k.pdf", _pdf_with_info(
        title, body, "Oleg Zatsarinny; Klaus Bartschat; Somebody Notonpage", meta_title=title), "application/pdf")}).json()["id"]
    n = store.get(nid)
    n["authors"] = []
    store.save(n)
    asked = []
    monkeypatch.setattr(dm.ctx, "ask", lambda *a, **k: asked.append(a) or "{}")
    out = dm.check(nid, use_ai=False)
    assert out["fields"] == {"authors": ["Oleg Zatsarinny", "Klaus Bartschat"]} and asked == [], out
    assert store.get(nid)["authors"] == ["Oleg Zatsarinny", "Klaus Bartschat"]

    # the library: a paper still wrong is queued once; with no AI there it waits (not remembered) and goes when one is
    n = store.get(nid)
    n["authors"], n["title"] = [], "PII: S0010-4655(00)00231-9"
    store.save(n)
    queued = []
    monkeypatch.setattr(dm, "later", lambda i, delay=None: queued.append(i))
    assert dm.check_library()["queued"] == 1 and queued == [nid]
    monkeypatch.setattr(dm.ctx, "ready", lambda: False)
    monkeypatch.setattr(dm.ctx, "refresh", None)
    n = store.get(nid)
    n["title"] = "PII: S0010-4655(00)00231-9"  # the PDF's title fixes it otherwise: keep this one broken
    store.save(n)
    monkeypatch.setattr(dm, "from_pdf", lambda i, page: {})
    dm.check_quietly(nid)
    assert dm.suspect(store.get(nid)) and asked == []
    queued.clear()
    assert dm.check_library()["queued"] == 1  # not remembered: it still needs the AI
    # with an AI: asked (it says nothing usable here), then remembered, and not queued again until it changes
    monkeypatch.setattr(dm.ctx, "ready", lambda: True)
    dm.check_quietly(nid)
    assert len(asked) == 1
    queued.clear()
    assert dm.check_library()["queued"] == 0 and queued == []
    n = store.get(nid)
    n["title"] = "Download details:"
    store.save(n)
    assert dm.check_library()["queued"] == 1


def test_a_title_cut_short_is_completed_and_a_misprinted_doi_is_looked_up_as_it_should_read(client, monkeypatch):
    from app import server
    store, dm = server.store, server.details_mod
    full = "Fast Optical Emission Spectroscopy for Studying Plasma Behavior in Hall Thrusters"
    answer = {"title": full, "authors": ["Jane Doe"]}
    body = f"{full}\nJane Doe\nsome text\n{_ask_for(answer)}"
    nid = client.post("/api/import/pdf?quick=1", files={"file": ("f.pdf", make_pdf("x", body), "application/pdf")}).json()["id"]
    n = store.get(nid)
    n["title"], n["authors"] = "Fast Optical Emission Spectroscopy for Studying", []
    store.save(n)
    monkeypatch.setattr(dm, "from_pdf", lambda i, page: {})
    out = dm.check(nid)
    assert out["fields"]["title"] == full and store.get(nid)["authors"] == ["Jane Doe"], out

    tried = []

    def refresh(i):
        d = store.get(i)["doi"]
        tried.append(d)
        if d == "10.1088/0022-3727/42/28/185203":
            m = store.get(i)
            m["title"], m["authors"] = "Electron-impact excitation of krypton", ["Oleg Zatsarinny"]
            store.save(m)
            return {"found": True}
        return {"found": False}
    monkeypatch.setattr(dm.ctx, "refresh", refresh)
    n = store.get(nid)
    n["title"], n["authors"], n["doi"] = "REPORT DOCUMENTATION PAGE Form Approved", [], "10.1088/00223727/42/28/185203"
    store.save(n)
    out = dm.check(nid, use_ai=False)
    assert tried == ["10.1088/00223727/42/28/185203", "10.1088/0022-3727/42/28/185203"]
    assert out["fields"]["doi"] == "10.1088/0022-3727/42/28/185203"
    assert store.get(nid)["authors"] == ["Oleg Zatsarinny"]

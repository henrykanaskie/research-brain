"""Citations that point at their sentence (app/locate.py), whether a paper is free to read (app/access.py),
OpenAlex as the fallback for Related papers, and the optional free lookup keys (app/apikeys.py)."""
from test_api import client, PAPERS  # noqa: F401  (fixture)

TEXT = """[[page 1]]
Hall thrusters are used for satellite propulsion. They ionise xenon gas in an annular channel.
[[page 2]]
The breathing mode oscillates at 10-30 kHz. Its amplitude grows with discharge voltage, as shown in Fig. 3.
Other azimuthal modes also exist.
[[page 3]]
We measured anomalous electron transport using laser-induced fluorescence. The Bohm coefficient was 1/16.
"""


def test_locate_finds_the_sentence_and_corrects_an_off_by_one_page():
    from app.locate import locate
    r = locate(TEXT, 2, "The breathing-mode oscillation frequency lies between 10 and 30 kHz.")
    assert r["page"] == 2 and r["quote"] == "The breathing mode oscillates at 10-30 kHz." and not r["moved"]
    r = locate(TEXT, 2, "Anomalous transport was measured with laser-induced fluorescence (Bohm coefficient 1/16).")
    assert r["page"] == 3 and r["moved"] and r["quote"].startswith("We measured anomalous electron transport")
    r = locate(TEXT, 2, "Quantum gravity is unrelated.")
    assert r["quote"] == "" and r["page"] == 2  # no confident match: open the cited page, highlight nothing
    assert locate(TEXT, 2, "The amplitude grows, as shown in Fig. 3, with voltage")["quote"].startswith("Its amplitude grows")


def test_locate_endpoint(client):
    from app import server
    n = client.post("/api/notes", json={"type": "paper", "title": "Hall thruster notes"}).json()
    server.store.text_path(n["id"]).write_text(TEXT, encoding="utf-8")
    out = client.post("/api/locate", json={"items": [
        {"id": n["id"], "page": 2, "claim": "It oscillates at 10-30 kHz"}, {"id": "missing", "page": 1, "claim": "x"}]}).json()
    assert out["items"][0]["quote"] == "The breathing mode oscillates at 10-30 kHz." and out["items"][0]["pdf"] == n["id"]
    assert out["items"][1] is None


def test_access_states_and_cache(client, monkeypatch):
    from app import access, server
    calls = []

    def fake(doi):
        calls.append(doi)
        return {"10.1/closed": {"is_oa": False, "oa_status": "closed", "oa_url": ""},
                "10.1/green": {"is_oa": True, "oa_status": "green", "oa_url": "https://repo.example.edu/p.pdf"}}.get(doi)
    monkeypatch.setattr(access, "_openalex", fake)
    arx = client.post("/api/notes", json={"type": "paper", "title": "On arXiv", "arxiv": "1706.03762"}).json()
    shut = client.post("/api/notes", json={"type": "paper", "title": "Paywalled", "doi": "10.1/closed"}).json()
    green = client.post("/api/notes", json={"type": "paper", "title": "Repository copy", "doi": "10.1/green"}).json()
    a = client.get(f"/api/notes/{arx['id']}/access").json()
    assert a["state"] == "open" and a["url"] == "https://arxiv.org/pdf/1706.03762"
    s = client.get(f"/api/notes/{shut['id']}/access").json()
    assert s["state"] == "closed" and "subscription" in s["label"]
    g = client.get(f"/api/notes/{green['id']}/access").json()
    assert g["state"] == "open" and g["url"] == "https://repo.example.edu/p.pdf"
    client.get(f"/api/notes/{green['id']}/access")
    assert calls.count("10.1/green") == 1  # cached
    assert (server.store.root / "state" / "access.json").exists()


def test_related_papers_fall_back_to_openalex_when_semantic_scholar_is_busy(client, monkeypatch):
    from app import openalex, scholar, server

    def busy(pid):
        raise scholar.RateLimited("Semantic Scholar is rate-limiting requests right now.")
    monkeypatch.setattr(server.scholar, "discover", busy)
    monkeypatch.setattr(server.openalex, "discover", lambda doi="", arxiv="", openalex="": {
        "references": [{"s2_id": "", "openalex": "W1", "title": "Ref", "year": 2001, "authors": [], "url": "",
                        "doi": "10.9/ref", "arxiv": "", "citationCount": 4, "access": "closed", "oa_pdf": ""}],
        "citations": [], "recommended": []})
    hall = client.post("/api/import/lookup", json={"query": "10.1063/1.4945558"}).json()
    d = client.get(f"/api/notes/{hall['id']}/discover").json()
    assert d["source"] == "openalex" and "busy" in d["note"] and d["references"][0]["title"] == "Ref"
    # and when OpenAlex is unreachable too, the error says both
    def down(**kw):
        raise openalex.OpenAlexError("Couldn't reach OpenAlex")
    monkeypatch.setattr(server.openalex, "discover", down)
    r = client.get(f"/api/notes/{hall['id']}/discover")
    assert r.status_code == 503 and "OpenAlex" in r.json()["detail"]


def test_openalex_work_parsing(monkeypatch):
    from app import openalex
    monkeypatch.setenv("RB_METADATA", "1")
    pages = {
        "/works/doi:10.1/x": {"id": "https://openalex.org/W9", "referenced_works": ["https://openalex.org/W1"], "related_works": []},
    }

    def fake(path, params=None, timeout=20):
        if path in pages:
            return pages[path]
        f = params.get("filter", "")
        if f.startswith("openalex:"):
            return {"results": [{"id": "https://openalex.org/W1", "display_name": "Cited one", "publication_year": 1999,
                                 "authorships": [{"author": {"display_name": "A. B"}}], "doi": "https://doi.org/10.48550/arXiv.2101.00001",
                                 "cited_by_count": 7, "open_access": {"is_oa": True, "oa_status": "green", "oa_url": "https://arxiv.org/abs/2101.00001"}}]}
        return {"results": []}
    monkeypatch.setattr(openalex, "_get", fake)
    res = openalex.discover(doi="10.1/x")
    r = res["references"][0]
    assert r["title"] == "Cited one" and r["arxiv"] == "2101.00001" and r["access"] == "open" and r["openalex"] == "W1"


def test_lookup_keys_are_saved_masked_and_validated(client):
    st = client.get("/api/lookup-keys").json()
    assert set(st) == {"s2", "openalex"} and not st["s2"]["set"]
    assert client.put("/api/lookup-keys", json={"service": "s2", "key": "not a key!"}).status_code == 400
    out = client.put("/api/lookup-keys", json={"service": "s2", "key": "abcDEF1234567890"}).json()
    assert out["s2"]["set"] and out["s2"]["hint"] == "…7890" and "abcDEF" not in str(out)
    from app import apikeys
    assert apikeys.get("s2") == "abcDEF1234567890"
    assert not client.put("/api/lookup-keys", json={"service": "s2", "key": ""}).json()["s2"]["set"]

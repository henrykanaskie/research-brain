"""Question reach (private / library / scout) and the scout that searches published papers (app/scout.py)."""
from __future__ import annotations

import datetime as dt

import pytest

from test_questions import FX, add_question, evidence, library, matcher, server, settle  # noqa: F401


def paper(title, abstract, doi="", arxiv="", year=2025):
    return {"title": title, "abstract": abstract, "year": year, "date": f"{year}-03-01", "authors": ["A. Researcher"],
            "doi": doi, "arxiv": arxiv, "venue": "J. Test", "url": f"https://doi.org/{doi}" if doi else "", "access": "closed",
            "found_via": "Fake"}


def fake_sources(found, calls):
    def search(query, since):
        calls.append((query, since))
        return list(found)
    return {"fake": search}


def test_query_is_the_question_not_the_answer():
    from app.scout import query_for
    q = {"title": "Does the breathing mode frequency track $T_e$ in Hall thrusters?",
         "body": "Using krypton CRM line ratios. Electron temperature from CRM; see [[some-note]] https://x.y",
         "answer": "SECRET answer text"}
    out = query_for(q)
    assert "breathing" in out and "krypton" in out.lower() and "SECRET" not in out and "some-note" not in out
    assert "$" not in out and "http" not in out and len(out) <= 240


def test_reach_defaults_private_and_privacy_wins(server):
    srv, c = server
    from app import scout
    q = add_question(c)
    assert evidence(c, q["id"])["question"]["reach"] == "private"
    r = c.put(f"/api/questions/{q['id']}/reach", json={"reach": "scout"})
    assert r.status_code == 200 and r.json()["ai"]["available"]
    assert evidence(c, q["id"])["question"]["reach"] == "scout"
    # the lock in the header (privacy API) still wins: a private question never scouts
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": True})
    assert scout.reach(srv.store.get(q["id"])) == "private"
    assert c.post(f"/api/questions/{q['id']}/scout").status_code == 400
    assert c.put(f"/api/questions/{q['id']}/reach", json={"reach": "everyone"}).status_code == 400


def test_scout_adds_close_new_papers_and_skips_the_library(server):
    srv, c = server
    from app import questions as Q
    ids = library(srv, c)
    lib_title = srv.store.get(ids["breathing"])["title"]
    q = add_question(c)
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "scout"})
    title, body = FX["questions"]["breathing_crm"]["title"], FX["questions"]["breathing_crm"]["body"]
    found = [
        paper("Breathing mode oscillations and electron temperature from krypton line ratios",
              f"{title} {body} We measure breathing mode frequency against electron temperature.", doi="10.1/close"),
        paper(lib_title, "already in the library", doi="10.1/dup"),
        paper("Sourdough fermentation kinetics", "Yeast and lactobacilli in bread dough at room temperature.", doi="10.1/bread"),
    ]
    calls = []
    r = Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))
    titles = [i["title"] for i in r["added"]]
    assert titles == [found[0]["title"]] and calls[0][1] < dt.date.today() - dt.timedelta(days=365)
    ev = evidence(c, q["id"])
    ext = [i for i in ev["items"] if i["kind"] == "external"]
    assert len(ext) == 1 and ext[0]["source"]["external"] and ext[0]["href"] == "https://doi.org/10.1/close"
    assert ev["scout"]["last"] and ev["scout"]["query"]
    # a second scout looks only at what's new (from the last search, with a week's overlap) and adds nothing twice
    r2 = Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))
    assert r2["added"] == [] and calls[1][1] >= dt.date.today() - dt.timedelta(days=8)
    # dismissed stays dismissed
    c.put(f"/api/questions/{q['id']}/evidence/{ext[0]['id']}", json={"verdict": "dismissed"})
    data = Q.load(srv.store, q["id"])
    data["scout"]["searched_to"] = ""
    Q._save(srv.store, q["id"], data)
    assert Q.scout_scan(matcher(srv).ctx, q["id"], sources=fake_sources(found, calls))["added"] == []


def test_private_and_library_questions_never_leave_the_computer(server, monkeypatch):
    srv, c = server
    from app import questions as Q, scout
    monkeypatch.setenv("RB_METADATA", "1")
    calls = []
    monkeypatch.setattr(scout, "SOURCES", fake_sources([], calls))
    library(srv, c)
    p = add_question(c)
    lib = add_question(c, "breathing_crm")
    c.put(f"/api/questions/{lib['id']}/reach", json={"reach": "library"})
    matcher(srv).run_all(force={p["id"], lib["id"]})
    assert calls == []
    assert Q.scout_scan(matcher(srv).ctx, p["id"])["error"].startswith("This question doesn't scout")
    # scout is searched by the background matcher once, then not again for a day
    c.put(f"/api/questions/{lib['id']}/reach", json={"reach": "scout"})
    settle(srv)
    matcher(srv).run_all()
    matcher(srv).run_all()
    assert len(calls) == 1


def test_scout_errors_are_reported_and_searched_to_kept(server):
    srv, c = server
    from app import questions as Q
    q = add_question(c)
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "scout"})

    def down(query, since):
        raise ConnectionError("offline")
    r = Q.scout_scan(matcher(srv).ctx, q["id"], sources={"openalex": down})
    assert "openalex: ConnectionError" in r["error"]
    assert Q.load(srv.store, q["id"])["scout"]["searched_to"] == ""  # the next try looks back the full window


def test_openalex_and_arxiv_parsing(monkeypatch):
    from app import scout

    class R:
        def __init__(self, js=None, content=b""):
            self._js, self.content, self.status_code = js, content, 200
        def raise_for_status(self):
            pass
        def json(self):
            return self._js
    oa = {"results": [{"id": "https://openalex.org/W1", "display_name": "Breathing mode", "publication_year": 2025,
                       "publication_date": "2025-02-01", "authorships": [{"author": {"display_name": "B. C"}}],
                       "doi": "https://doi.org/10.1/x", "abstract_inverted_index": {"Hall": [0], "thrusters": [1], "oscillate": [2]},
                       "primary_location": {"source": {"display_name": "Phys. Plasmas"}}, "open_access": {"oa_status": "closed"}}]}
    atom = b"""<feed xmlns="http://www.w3.org/2005/Atom"><entry><id>http://arxiv.org/abs/2501.01234v2</id>
      <published>2025-01-05T00:00:00Z</published><title>Krypton  CRM</title><summary>Line ratios.</summary>
      <author><name>D. E</name></author></entry>
      <entry><id>http://arxiv.org/abs/1901.00001v1</id><published>2019-01-01T00:00:00Z</published><title>Old</title><summary>x</summary></entry></feed>"""
    monkeypatch.setattr(scout.requests, "get", lambda url, **kw: R(js=oa) if "openalex" in url else R(content=atom))
    a = scout.search_openalex("breathing mode", dt.date(2024, 1, 1))[0]
    assert a["abstract"] == "Hall thrusters oscillate" and a["doi"] == "10.1/x" and a["access"] == "closed" and a["venue"] == "Phys. Plasmas"
    x = scout.search_arxiv("krypton crm", dt.date(2024, 1, 1))
    assert len(x) == 1 and x[0]["arxiv"] == "2501.01234" and x[0]["title"] == "Krypton CRM" and x[0]["access"] == "open"

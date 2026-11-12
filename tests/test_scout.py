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


# ---------------------------------------------------------------- agent search (reach "agent")
def test_agent_search_proposes_papers_with_reasons(server):
    srv, c = server
    from app import agents, questions as Q
    from test_questions import Recorder
    ids = library(srv, c)
    lib_title = srv.store.get(ids["breathing"])["title"]
    q = add_question(c)
    c.put(f"/api/notes/{q['id']}", json={"answer": "SECRET: my unpublished derivation"})
    r = c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    assert r.status_code == 200 and r.json()["ai"]["available"]
    found = [paper("Breathing-mode frequency scaling with electron temperature", "We relate f_b to T_e via line ratios.", doi="10.1/a"),
             paper(lib_title, "already in the library", doi="10.1/lib"),
             paper("Krypton collisional-radiative modeling", "A Kr CRM for Hall thrusters.", arxiv="2501.00001")]
    calls = []
    cited = [paper("A cited classic on ionization oscillations", "Predator-prey model of the breathing mode.", doi="10.1/c")]
    fb = Recorder(replies=[
        '{"action": "search", "queries": ["breathing mode frequency electron temperature", "krypton CRM Hall thruster"]}',
        '{"action": "read", "paper": "P1"}',
        '{"action": "propose", "paper": "P1", "stance": "supports", "why": "Directly fits $f_b \\\\propto T_e$."}',
        '{"action": "propose", "paper": "P3", "stance": "related", "why": "not read yet"}',
        '{"action": "citations", "paper": "P1", "direction": "references"}',
        '{"action": "read", "paper": "P4"}',
        '{"action": "propose", "paper": "P4", "stance": "related", "why": "The classic model."}',
    ] + ['{"action": "done", "note": "Searched two phrasings and one reference list."}'])
    out = Q.agent_scan(matcher(srv).ctx, q["id"], backend=fb, sources=fake_sources(found, calls),
                       cites=lambda p, d: cited)
    assert out["error"] == ""
    titles = [i["title"] for i in out["added"]]
    assert titles == [found[0]["title"], cited[0]["title"]]  # P3 was refused: never read
    first = out["added"][0]
    assert first["ai"] == {"stance": "supports", "why": "Directly fits $f_b \\propto T_e$."} and first["verdict"] == "new"
    sent = "\n".join(fb.prompts)
    assert "SECRET" not in sent and q["title"] in sent
    assert "read P3's abstract before proposing it" in sent  # the refusal went back to the agent
    ev = evidence(c, q["id"])
    assert ev["agent"]["added"] == 2 and ev["agent"]["tokens"] > 0 and ev["agent"]["turns"] == 7  # 5 + 2 at Balanced
    row = next(i for i in ev["items"] if i["kind"] == "external" and i.get("ai"))
    assert row["source"]["found_via"] == "AI search"
    # the next run is told what was already found, and dedupes it anyway
    fb.replies = ['{"action": "search", "queries": ["breathing mode"]}', '{"action": "read", "paper": "P1"}',
                  '{"action": "propose", "paper": "P1", "stance": "supports", "why": "again"}', '{"action": "done"}']
    again = Q.agent_scan(matcher(srv).ctx, q["id"], backend=fb, sources=fake_sources(found, calls), cites=lambda p, d: [])
    second = fb.prompts[7]
    assert again["added"] == [] and "ALREADY FOUND" in second and found[0]["title"] in second.split("ALREADY FOUND")[1]


def test_agent_search_needs_agent_reach_and_stops_on_turns(server):
    srv, c = server
    from app import questions as Q, agent_scout
    from test_questions import Recorder
    q = add_question(c)
    assert "Agent search is off" in Q.agent_scan(matcher(srv).ctx, q["id"], backend=Recorder())["error"]
    assert c.post(f"/api/questions/{q['id']}/agent-search").status_code == 400
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    # the lock in the header still wins
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": True})
    assert "Agent search is off" in Q.agent_scan(matcher(srv).ctx, q["id"], backend=Recorder())["error"]
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": False})
    c.put(f"/api/questions/{q['id']}/reach", json={"reach": "agent"})
    fb = Recorder(replies=['{"action": "search", "queries": ["x"]}'] * 50)
    r = agent_scout.run(fb, {"title": "t", "body": ""}, since=dt.date(2020, 1, 1), sources=fake_sources([], []), max_turns=3)
    assert r["turns"] == 3 and r["stopped"] == "max_turns" and len(fb.prompts) == 3
    assert "last" in fb.prompts[-1]  # the final turn says so
    # a failing agent is recorded on the question (and retried after a day, not a week)
    class Broken(Recorder):
        def ask_stream(self, prompt, *, system="", timeout=180):
            raise RuntimeError("signed out")
            yield ""
    out = Q.agent_scan(matcher(srv).ctx, q["id"], backend=Broken(), sources=fake_sources([], []))
    assert "signed out" in out["error"]
    data = Q.load(srv.store, q["id"])
    assert data["agent"]["error"] and Q.agent_due(data) is False

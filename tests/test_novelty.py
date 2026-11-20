"""Novelty check (app/novelty.py, app/novelty_api.py): search, citations, local bands, the AI's reading, privacy."""
from __future__ import annotations

import json
import time

from test_questions import Recorder, add_question, library, server  # noqa: F401
from test_scout import fake_sources, paper

IDEA_T = "Sparse attention for protein contact map prediction"
IDEA_B = "Use sparse attention transformers to predict protein residue contact maps without multiple sequence alignments."

CLOSE = paper("Sparse attention transformers predict protein contact maps",
              "We use sparse attention transformers to predict protein residue contact maps without multiple sequence alignments.",
              doi="10.1/close", year=2024)
PART = paper("Protein contact map prediction with convolutional networks",
             "Residue contact maps of proteins are predicted from multiple sequence alignments with deep convolutional networks.",
             doi="10.1/part", year=2019)
FAR = paper("Shopping list habits of graduate students", "Groceries, coffee and the weekly budget.", doi="10.1/far", year=2020)
COCITED = paper("Coevolution signals in residue contacts", "Classic statistical coupling analysis.", doi="10.1/cocited", year=2011)
FOLLOW = paper("Sparse attention contact maps for protein design",
               "Building on sparse attention, protein residue contact maps guide design.", doi="10.1/follow", year=2025)


def cites_fake(calls):
    def cites(p, direction):
        calls.append((p["title"], direction))
        if direction == "references":
            return [COCITED]
        return [FOLLOW] if p["doi"] == "10.1/close" else []
    return cites


def test_check_finds_close_work_follows_citations_and_compares_runs(server):
    srv, c = server
    from app import novelty
    q = add_question(c)
    calls, ccalls = [], []
    kw = dict(note_id=q["id"], title=IDEA_T, idea=IDEA_B,
              parts=[{"name": "Problem", "text": "protein residue contact map prediction"},
                     {"name": "Method", "text": "sparse attention transformers"}],
              queries=["sparse attention protein contact", "protein contact map", ""], online=True,
              sources=fake_sources([CLOSE, PART, FAR], calls), cites=cites_fake(ccalls), wait=True)
    r = novelty.start(srv.store, **kw)
    assert [x[0] for x in calls] == ["sparse attention protein contact", "protein contact map"]  # empty query dropped
    run = novelty.get(srv.store, r["id"])
    assert run["status"] == "done", run
    by = {p["title"]: p for p in run["papers"]}
    assert by[CLOSE["title"]]["band"] == "close" and min(by[CLOSE["title"]]["parts"]) >= 1
    assert by[PART["title"]]["band"] in ("partial", "background")
    assert FAR["title"] not in by
    # citations: the reference both seeds share is co-cited; the paper citing the close one is found too
    assert ccalls and {d for _, d in ccalls} == {"references", "cited_by"}
    assert by[COCITED["title"]]["cited_by_seeds"] >= 2 and by[COCITED["title"]]["via"] == ["citations"]
    assert FOLLOW["title"] in by and by[FOLLOW["title"]]["cites_seeds"] == 1
    assert run["counts"]["close"] >= 1 and run["counts"]["examined"] >= 5 and run["counts"]["compared"] is False
    assert run["years"].get("2024") == 1
    # a second check of the same note marks what wasn't there before
    newer = paper("Sparse attention protein contact maps at scale",
                  "Sparse attention transformers predict protein residue contact maps without alignments, at scale.",
                  doi="10.1/new", year=2026)
    r2 = novelty.start(srv.store, **{**kw, "sources": fake_sources([CLOSE, PART, newer], [])})
    run2 = novelty.get(srv.store, r2["id"])
    by2 = {p["title"]: p for p in run2["papers"]}
    assert by2[newer["title"]]["new"] is True, (by2[newer["title"]], run2["counts"])
    assert by2[CLOSE["title"]]["new"] is False
    assert run2["counts"]["compared"] is True and run2["counts"]["new"] >= 1
    assert [x["id"] for x in novelty.runs_for(srv.store, q["id"])] == [r2["id"], r["id"]]


def test_ai_reads_the_closest_and_its_labels_win(server):
    srv, c = server
    from app import novelty
    q = add_question(c)
    asked = []

    def ask(instr, ctx):
        asked.append((instr, ctx))
        handles = {line.split(". ", 1)[1].split(" (")[0]: line.split(".")[0]
                   for line in ctx.splitlines() if line[:1] == "P" and line[1:2].isdigit()}
        return json.dumps({"papers": [{"paper": handles[PART["title"]], "overlap": "close", "parts": [1],
                                       "diff": "Same maps, from alignments."}],
                           "uncovered": "None does it without alignments.", "verdict": "Partly done."})
    r = novelty.start(srv.store, note_id=q["id"], title=IDEA_T, idea=IDEA_B,
                      parts=[{"name": "Problem", "text": "protein contact maps"}, {"name": "Method", "text": "sparse attention"}],
                      queries=["protein contact"], online=True, ask=ask,
                      sources=fake_sources([CLOSE, PART], []), cites=lambda p, d: [], wait=True)
    run = novelty.get(srv.store, r["id"])
    assert len(asked) == 1 and IDEA_B in asked[0][1] and CLOSE["abstract"][:40] in asked[0][1]
    part = next(p for p in run["papers"] if p["title"] == PART["title"])
    assert part["band"] == "close" and part["judged"] and part["why"] == "Same maps, from alignments." and part["parts"][0] == 2
    assert run["uncovered"] == "None does it without alignments." and run["verdict"] == "Partly done."
    assert run["chars_sent"] > 0


def test_plan_parses_the_agent_and_falls_back():
    from app import novelty
    p = novelty.plan(IDEA_T, IDEA_B, lambda i, c: 'Sure: {"parts": [{"name": "Problem", "text": "contact maps"}, '
                                                    '{"name": "Method", "text": "sparse attention"}], '
                                                    '"queries": ["sparse attention contact map", "contact map", "contact map"]}')
    assert p["ai"] and [x["name"] for x in p["parts"]] == ["Problem", "Method"] and p["queries"] == ["sparse attention contact map", "contact map"]
    lp = novelty.plan(IDEA_T, IDEA_B, lambda i, c: "no json here")
    assert lp["ai"] is False and lp["parts"][0]["name"] == "Idea" and "Sparse" in lp["queries"][0]


def test_api_private_note_checks_only_the_library(server, monkeypatch):
    srv, c = server
    from app import novelty_api
    monkeypatch.setattr(novelty_api.metadata, "enabled", lambda: True)
    def boom(q, since):
        raise AssertionError("a private note's check searched a paper index")
    monkeypatch.setattr(novelty_api.ctx, "sources", {"boom": boom})
    monkeypatch.setattr(novelty_api.ctx, "cites", lambda p, d: (_ for _ in ()).throw(AssertionError("citations fetched")))
    library(srv, c)
    q = add_question(c)  # questions are private by default
    pl = c.post("/api/novelty/plan", json={"note_id": q["id"]}).json()
    assert pl["online"] is False and "private" in pl["online_why"] and pl["ai"] is False and pl["planned_by_ai"] is False
    r = c.post("/api/novelty", json={"note_id": q["id"], "parts": pl["parts"], "queries": ["should not go"]})
    assert r.status_code == 202, r.text
    rid = r.json()["run"]["id"]
    assert r.json()["run"]["queries"] == []
    run = _wait(c, rid)
    assert run["status"] == "done" and run["online"] is False
    assert all(p["via"] == ["library"] and p["library_id"] for p in run["papers"])
    assert c.get(f"/api/novelty?note_id={q['id']}").json()["runs"][0]["id"] == rid
    assert c.delete(f"/api/novelty/runs/{rid}").status_code == 200
    assert c.get(f"/api/novelty/runs/{rid}").status_code == 404


def test_api_shareable_note_searches_and_uses_the_agent(server, monkeypatch):
    srv, c = server
    from app import agents, novelty_api
    monkeypatch.setattr(novelty_api.metadata, "enabled", lambda: True)
    calls = []
    monkeypatch.setattr(novelty_api.ctx, "sources", fake_sources([CLOSE, PART], calls))
    monkeypatch.setattr(novelty_api.ctx, "cites", lambda p, d: [])
    q = add_question(c)
    c.post(f"/api/notes/{q['id']}/privacy", json={"private": False})
    fb = Recorder(replies=['{"parts": [{"name": "Problem", "text": "protein contact maps"}], "queries": ["protein contact map"]}',
                           '{"papers": [], "uncovered": "Nothing found does it.", "verdict": "Looks open."}'])
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        pl = c.post("/api/novelty/plan", json={"note_id": q["id"], "text": IDEA_B}).json()
        assert pl["online"] and pl["ai"] and pl["planned_by_ai"] and pl["queries"] == ["protein contact map"]
        assert pl["from_selection"] and pl["idea"] == IDEA_B and IDEA_B in fb.prompts[0]
        r = c.post("/api/novelty", json={"note_id": q["id"], "text": IDEA_B, "parts": pl["parts"], "queries": ["edited words"]})
        run = _wait(c, r.json()["run"]["id"])
        assert run["status"] == "done" and [x[0] for x in calls] == ["edited words"]
        assert run["uncovered"] == "Nothing found does it." and len(fb.prompts) == 2
        assert c.post("/api/novelty/plan", json={"note_id": q["id"], "text": "x"}).status_code in (200, 400)
    finally:
        agents.unregister(fb.id)


def _wait(c, rid, s=20):
    t0 = time.monotonic()
    while time.monotonic() - t0 < s:
        run = c.get(f"/api/novelty/runs/{rid}").json()
        if run["status"] != "running":
            return run
        time.sleep(0.05)
    raise AssertionError("the novelty check didn't finish")


def test_api_typed_idea_from_ask(server, monkeypatch):
    """Novelty mode in Ask: no note, the idea is what was typed; the run is kept with the conversation."""
    srv, c = server
    from app import agents, novelty_api
    monkeypatch.setattr(novelty_api.metadata, "enabled", lambda: True)
    calls = []
    monkeypatch.setattr(novelty_api.ctx, "sources", fake_sources([CLOSE, PART], calls))
    monkeypatch.setattr(novelty_api.ctx, "cites", lambda p, d: [])
    fb = Recorder(replies=['{"parts": [{"name": "Problem", "text": "protein contact maps"}, {"name": "Method", "text": "sparse attention"}],'
                           ' "queries": ["sparse attention contact map"]}',
                           '{"papers": [], "uncovered": "Nothing without alignments.", "verdict": "Partly done."}'])
    agents.register(fb)
    agents.set_active(fb.id)
    try:
        assert c.post("/api/novelty/plan", json={"text": "short"}).status_code == 400
        pl = c.post("/api/novelty/plan", json={"text": IDEA_B}).json()
        assert pl["typed"] and pl["online"] and pl["ai"] and pl["planned_by_ai"] and pl["idea"] == IDEA_B and pl["title"] == ""
        r = c.post("/api/novelty", json={"text": IDEA_B, "parts": pl["parts"], "queries": pl["queries"]})
        assert r.status_code == 202, r.text
        run = _wait(c, r.json()["run"]["id"])
        assert run["status"] == "done" and run["note_id"] == "" and run["verdict"] == "Partly done."
        assert [x[0] for x in calls] == ["sparse attention contact map"]
        assert any(p["title"] == CLOSE["title"] for p in run["papers"])
        # the chat keeps the run id with the message (and nothing else unknown)
        c.put("/api/chats/library", json={"messages": [{"role": "user", "content": IDEA_B},
                                                      {"role": "assistant", "content": "Novelty check: 1 very close", "novelty": run["id"]},
                                                      {"role": "assistant", "content": "x", "novelty": "../../etc"}], "title": "t"})
        msgs = c.get("/api/chats/library").json()["messages"]
        assert msgs[1].get("novelty") == run["id"] and "novelty" not in msgs[2]
    finally:
        agents.unregister(fb.id)


def test_private_library_papers_are_compared_but_never_sent_to_the_ai(server):
    srv, c = server
    from app import novelty
    secret = c.post("/api/notes", json={"type": "paper", "title": "Sparse attention for protein contact maps, our draft",
                                        "abstract": "SECRET sparse attention protein residue contact maps without alignments."}).json()
    c.post(f"/api/notes/{secret['id']}/privacy", json={"private": True})
    c.post("/api/notes", json={"type": "paper", "title": CLOSE["title"], "abstract": "PUBLIC " + CLOSE["abstract"]})
    asked = []
    r = novelty.start(srv.store, note_id="", title="", idea=IDEA_B, parts=[{"name": "Idea", "text": IDEA_B}], queries=[],
                      online=False, ask=lambda i, ctx: asked.append(ctx) or "{}", wait=True)
    run = novelty.get(srv.store, r["id"])
    assert any(p["library_id"] == secret["id"] for p in run["papers"])
    assert asked and "PUBLIC" in asked[0] and all("SECRET" not in x for x in asked)

"""Idea cards (app/cards.py, app/cards_api.py, app/units.py): extraction parsing and the quote check that drops
made-up cards, dedup and the per-paper cap, privacy of what is sent, the review endpoints, unit normalisation
and conflict detection, cross-paper links, search and CSV, the agent tool and the Research action.
No network: a scripted fake agent stands in for the AI, and embeddings are TF-IDF.
"""
from __future__ import annotations

import csv
import importlib
import io
import json
import sys
import time
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.agents.base import Backend as _Backend, Detection as _Detection  # noqa: E402

PAPER_A = """[[page 1]]
Abstract
We measured the near-field plume of a 1.5 kW Hall thruster with a Langmuir probe and optical emission spectroscopy.

[[page 2]]
3. Results
The electron temperature was 20 eV at the channel exit for the 300 V operating condition.
The plasma potential decreased by 40 V across the acceleration region, consistent with magnetic shielding.

[[page 3]]
4. Conclusions
Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude.

References
[1] Someone, Some Journal (2001).
"""

PAPER_B = """[[page 1]]
Abstract
A Langmuir probe survey of a Hall thruster plume at 300 V.

[[page 4]]
Results
At the exit plane the electron temperature was 35 ± 5 eV for the 300 V operating condition.
Magnetic shielding lowered the erosion of channel walls by about two orders of magnitude.
"""

PAPER_C = """[[page 2]]
Results
We find an electron temperature of 2.3 × 10^5 K at the channel exit at 300 V, from the line ratios of a
collisional-radiative model.
"""


class FakeAgent(_Backend):
    """Replies from `reply(prompt)`; records every prompt it is sent."""

    def __init__(self, reply, id="fakecards", kind="cli", available=True):
        self.id, self.name, self.kind, self.home, self.cost_note = id, f"Fake {id}", kind, "https://example.invalid", ""
        self.reply = reply
        self.prompts: list[str] = []
        self._available = available

    def detect(self):
        return _Detection(self._available, version="1", path="/fake", signed_in=True)

    def ask_stream(self, prompt, *, system="", timeout=180):
        self.prompts.append(prompt)
        out = self.reply(prompt) if callable(self.reply) else self.reply
        yield out


def cards_json(*cards):
    return json.dumps({"cards": list(cards)})


@pytest.fixture()
def server(tmp_path, monkeypatch):
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "vault"))
    monkeypatch.setenv("RB_EMBEDDER", "tfidf")
    monkeypatch.setenv("RB_MEMORY_WATCH", "0")
    monkeypatch.setenv("RB_WATCH", "0")
    monkeypatch.setenv("RB_AUTO_EXPORT", "0")
    monkeypatch.delenv("RB_FAKE_CLAUDE", raising=False)
    monkeypatch.delenv("RB_AGENT", raising=False)
    for m in [m for m in sys.modules if m.startswith("app")]:
        del sys.modules[m]
    srv = importlib.import_module("app.server")
    from fastapi.testclient import TestClient
    added = []

    def use(agent):
        from app import agents
        agents.register(agent)
        added.append(agent.id)
        monkeypatch.setenv("RB_AGENT", agent.id)
        agents.forget_detection()
        return agent
    with TestClient(srv.app) as c:
        yield srv, c, use
    from app import agents
    for i in added:
        agents.unregister(i)


def paper(srv, c, title, text, **fields):
    nid = c.post("/api/notes", json={"type": "paper", "title": title, **fields}).json()["id"]
    srv.store.text_path(nid).write_text(text, encoding="utf-8")
    return nid


def wait_job(c, jid, timeout=20):
    t0 = time.time()
    while time.time() - t0 < timeout:
        j = c.get(f"/api/cards/jobs/{jid}").json()
        if j["state"] != "running":
            return j
        time.sleep(0.05)
    raise AssertionError("extraction didn't finish")


def extract(c, pid):
    r = c.post(f"/api/papers/{pid}/cards/extract")
    assert r.status_code == 202, r.text
    return wait_job(c, r.json()["job_id"])


# ====================================================================== parsing and the quote guard (no server)
def test_parse_cards_reply_variants():
    from app.cards import parse_cards_reply
    one = {"kind": "claim", "text": "x", "quote": "y"}
    assert parse_cards_reply(json.dumps({"cards": [one]})) == [one]
    assert parse_cards_reply("Here you go:\n```json\n" + json.dumps({"cards": [one]}) + "\n```\nDone.") == [one]
    assert parse_cards_reply("Sure. " + json.dumps([one])) == [one]
    assert parse_cards_reply('{"cards": [{"kind": "claim", "text": "two\nlines", "quote": "q"}]}')[0]["text"] == "two lines"
    assert parse_cards_reply('{"cards": []}') == []
    assert parse_cards_reply("I can't help with that.") is None
    assert parse_cards_reply("") is None


def test_quote_locator_exact_loose_fuzzy_and_page():
    from app.cards import PaperText
    pt = PaperText("[[page 3]]\nThe elec-\ntron tem­perature was 20 eV at the exit.\n\n[[page 4]]\nThe ﬁeld lines "
                   "are concave toward the anode, which focuses the ion beam.")
    hit = pt.locate("The electron temperature was 20 eV at the exit.")
    assert hit and hit["page"] == 3 and hit["how"] == "exact"
    assert hit["quote"] == "The electron temperature was 20 eV at the exit."  # line-end hyphen undone
    hit = pt.locate("the field lines are concave toward the anode -- which focuses the ion beam")
    assert hit and hit["page"] == 4 and hit["how"] == "loose" and hit["quote"].startswith("The field lines")
    assert pt.locate("The ion beam is focused because the field lines are concave toward the cathode") is None
    assert pt.locate("Tokamak disruptions were predicted with a neural network.") is None
    assert pt.locate("short") is None


def test_value_must_be_in_quote():
    from app.cards import value_in_quote
    assert value_in_quote({"value": 20.0}, "was 20 eV")
    assert value_in_quote({"value": 2.3e5}, "of 2.3 × 10^5 K")
    assert value_in_quote({"value": 25.0, "uncertainty": 5.0}, "between 20 and 30 eV")
    assert not value_in_quote({"value": 35.0}, "was 20 eV")


def test_extraction_drops_hallucinated_dedups_and_caps(tmp_path, monkeypatch):
    from app import cards
    cs = cards.CardStore(tmp_path)
    note = {"id": "hall-2020", "abstract": ""}
    good_q = "The electron temperature was 20 eV at the channel exit for the 300 V operating condition."
    reply = cards_json(
        {"kind": "quantity", "quote": good_q, "quantity": {"name": "electron temperature", "symbol": "T_e",
                                                          "value": 20, "unit": "eV", "conditions": "300 V, channel exit"}},
        {"kind": "quantity", "quote": good_q, "quantity": {"name": "electron temperature", "value": 25, "unit": "eV"}},
        {"kind": "claim", "text": "Shielding cuts wall erosion a hundredfold.",
         "quote": "Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude."},
        {"kind": "claim", "text": "Shielding cuts wall erosion a hundredfold",
         "quote": "Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude."},
        {"kind": "claim", "text": "Made up.", "quote": "The thruster achieved a specific impulse of 9000 s in orbit."},
        {"kind": "telepathy", "text": "bad kind", "quote": "We measured the near-field plume"},
        {"kind": "method", "text": "Langmuir probe and OES in the plume.",
         "quote": "with a Langmuir probe and optical emission spectroscopy"})
    res = cards.extract(cs, note, PAPER_A, None, ask=lambda i, c: reply)
    kinds = sorted(c["kind"] for c in res["added"])
    assert kinds == ["claim", "method", "quantity"], kinds
    assert res["dropped"] == 3 and res["duplicates"] >= 1
    q = next(c for c in res["added"] if c["kind"] == "quantity")
    assert q["locator"] == {"quote": good_q, "verified": True, "page": 2}  # PDF text: pages, no sections
    assert q["status"] == "suggested" and q["origin"] == "ai" and q["id"].startswith("c_") and len(q["id"]) == 10
    assert abs(q["quantity"]["canonical_unit_value"] - 232090.4) < 1 and q["quantity"]["canonical_unit"] == "K"
    stored = json.loads((tmp_path / "cards" / "hall-2020.json").read_text(encoding="utf-8"))
    assert [c["id"] for c in stored["cards"]] == [c["id"] for c in res["added"]] and stored["extractions"]
    # running again finds only duplicates
    res2 = cards.extract(cs, note, PAPER_A, None, ask=lambda i, c: reply)
    assert res2["added"] == [] and res2["duplicates"] >= 3
    # the cap: at most MAX_AI_CARDS AI cards per paper, over all extractions
    monkeypatch.setattr(cards, "MAX_AI_CARDS", 4)
    many = cards_json(*[{"kind": "claim", "text": f"Claim number {i} about page {i}.",
                         "quote": s} for i, s in enumerate([
                             "The plasma potential decreased by 40 V across the acceleration region",
                             "consistent with magnetic shielding", "We measured the near-field plume of a 1.5 kW Hall thruster"])])
    res3 = cards.extract(cs, note, PAPER_A, None, ask=lambda i, c: many)
    assert len(res3["added"]) == 1 and len(cs.cards("hall-2020")) == 4


def test_extraction_windows_prioritise_results_and_skip_references(monkeypatch):
    from app import cards
    monkeypatch.setattr(cards, "WINDOW_CHARS", 300)
    intro = "[[page 1]]\nIntroduction\n" + "Background words about history. " * 12
    body = intro + "\n\n[[page 2]]\n3. Results\n" + "The measured value was 12 eV. " * 8 + \
        "\n\n[[page 3]]\nReferences\n[1] A. Author, Journal (1999)." + " Cited text." * 30
    pt = cards.PaperText(cards.cut_references(body))
    assert "Cited text" not in "".join(s[2] for s in pt.segments)
    wins = cards.build_windows(pt)
    order = cards.pick_windows(wins, 2)
    assert "Results" in order[0]["text"]


def test_parse_failure_retries_once(tmp_path):
    from app import cards
    seen = []

    def ask(i, c):
        seen.append(i)
        return "Not JSON at all" if len(seen) == 1 else cards_json()
    res = cards.extract(cards.CardStore(tmp_path), {"id": "p", "abstract": ""}, PAPER_C, None, ask=ask)
    assert len(seen) == 2 and "valid JSON" in seen[1] and res["errors"] == []


# ====================================================================== units and conflicts
def test_units_normalise_plasma_quantities():
    from app import units
    a = units.canonical(20, "eV", "electron temperature", "T_e")
    b = units.canonical(2.3e5, "K", "electron temperature", "T_e")
    assert a["unit"] == b["unit"] == "K" and abs(a["value"] - 232090.36) < 0.1
    assert abs(units.canonical(1e11, "cm-3", "electron density")["value"] - 1e17) < 1e9
    assert units.canonical(2, "10^17 m^-3")["value"] == pytest.approx(2e17)
    assert units.canonical(5, "mTorr")["value"] == pytest.approx(0.6666, rel=1e-3)
    assert units.canonical(200, "G")["unit"] == "T"
    assert units.canonical(80, "mN")["value"] == pytest.approx(0.08)
    assert units.canonical(10, "sccm")["unit"] == "mol/s"
    assert units.canonical(25, "°C", uncertainty=1) == {"value": 298.15, "uncertainty": pytest.approx(1.0),
                                                        "unit": "K", "dim": "[temperature]"}
    assert units.canonical(3, "furlongs per fortnight squiggle") is None
    assert units.parse_value("20 ± 2") == (20.0, 2.0) and units.parse_value("20–30") == (25.0, 5.0)
    assert units.parse_number("2.3 x 10^{5}") == pytest.approx(2.3e5)


def test_conflict_rules():
    from app.cards import quantity_conflict
    te = lambda v, u, unc=None, cond="300 V, channel exit": {"name": "electron temperature", "symbol": "T_e", "value": v,
                                                              "unit": u, "uncertainty": unc, "conditions": cond}
    assert quantity_conflict(te(20, "eV"), te(2.3e5, "K")) == (True, False)          # agree (19.8 eV)
    assert quantity_conflict(te(20, "eV"), te(35, "eV", 5)) == (True, True)          # beyond combined uncertainty
    assert quantity_conflict(te(20, "eV", 10), te(28, "eV")) == (True, False)        # within it
    assert quantity_conflict(te(20, "eV"), te(30, "eV")) == (True, True)             # >30% with no uncertainties
    assert quantity_conflict(te(20, "eV"), te(24, "eV")) == (True, False)
    assert quantity_conflict(te(20, "eV", cond="300 V"), te(35, "eV", cond="600 V")) == (True, False)  # not comparable
    assert quantity_conflict(te(20, "eV"), {"name": "ion temperature", "symbol": "T_i", "value": 35, "unit": "eV"}) == (False, False)
    assert quantity_conflict(te(20, "eV"), {"name": "electron temperature", "value": 3, "unit": "m"}) == (False, False)
    assert quantity_conflict({"name": "Te", "symbol": "kT_e", "value": 2, "unit": "eV"}, te(2, "eV")) == (True, False)


# ====================================================================== the server
def test_extract_endpoint_privacy_and_log(server):
    srv, c, use = server
    pid = paper(srv, c, "Plume of a Hall thruster", PAPER_A, summary="MY-PRIVATE-SUMMARY wording here",
                body="MY-OWN-NOTES about the thruster")
    note = c.post("/api/notes", json={"type": "note", "title": "My idea", "body": "SECRET-IDEA text"}).json()
    agent = use(FakeAgent(lambda p: cards_json({
        "kind": "claim", "text": "Shielding cuts erosion.",
        "quote": "Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude."})))
    j = extract(c, pid)
    assert j["state"] == "done" and j["added"] == 1, j
    sent = "\n".join(agent.prompts)
    assert "Magnetic shielding" in sent and "PAPER TEXT" in sent
    for secret in ("MY-PRIVATE-SUMMARY", "MY-OWN-NOTES", "SECRET-IDEA", note["id"], "Someone, Some Journal"):
        assert secret not in sent, secret
    log = [json.loads(x) for x in (srv.store.root / "state" / "ai_log.jsonl").read_text(encoding="utf-8").splitlines()]
    assert any(e["purpose"] == "card-extraction" and pid in e["items"] for e in log), log
    # a private paper is never sent
    agent.prompts.clear()
    pid2 = paper(srv, c, "Private paper", PAPER_B)
    from app import privacy
    from app.store import set_private
    n = srv.store.get(pid2)
    set_private(n, True)
    srv.store.save(n)
    privacy.refresh()
    assert privacy.is_private(srv.store.get(pid2))
    r = c.post(f"/api/papers/{pid2}/cards/extract")
    assert r.status_code == 403 and "private" in r.json()["detail"]
    assert agent.prompts == []


def test_extract_needs_agent_and_text(server):
    srv, c, use = server
    use(FakeAgent("{}", id="offline", available=False))
    pid = paper(srv, c, "Some paper", PAPER_A)
    r = c.post(f"/api/papers/{pid}/cards/extract")
    assert r.status_code == 503 and "by hand" in r.json()["detail"]
    empty = c.post("/api/notes", json={"type": "paper", "title": "No text"}).json()["id"]
    assert c.post(f"/api/papers/{empty}/cards/extract").status_code == 400
    note = c.post("/api/notes", json={"type": "note", "title": "A note"}).json()["id"]
    assert c.post(f"/api/papers/{note}/cards/extract").status_code == 400
    assert c.post("/api/papers/nope/cards/extract").status_code == 404
    got = c.get(f"/api/papers/{pid}/cards").json()
    assert got["cards"] == [] and got["agent"]["available"] is False


def test_review_endpoints(server):
    srv, c, use = server
    pid = paper(srv, c, "Plume of a Hall thruster", PAPER_A)
    use(FakeAgent(lambda p: cards_json(
        {"kind": "claim", "text": "Shielding cuts erosion.",
         "quote": "Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude."},
        {"kind": "quantity", "quote": "The electron temperature was 20 eV at the channel exit",
         "quantity": {"name": "electron temperature", "symbol": "T_e", "value": "20", "unit": "eV",
                      "conditions": "300 V"}},
        {"kind": "method", "text": "Langmuir probe", "quote": "with a Langmuir probe and optical emission spectroscopy"})))
    extract(c, pid)
    got = c.get(f"/api/papers/{pid}/cards").json()
    assert got["counts"]["suggested"] == 3
    claim, qty, meth = (next(x for x in got["cards"] if x["kind"] == k) for k in ("claim", "quantity", "method"))
    assert qty["where"] == "p. 2" and qty["value_text"] == "20" and "K" in qty["canonical_text"]
    assert claim["where"] == "p. 3" and claim["href"] == ""  # no PDF, no URL
    # confirm, edit, reject
    r = c.patch(f"/api/cards/{claim['id']}", json={"status": "confirmed"})
    assert r.status_code == 200 and r.json()["status"] == "confirmed"
    r = c.patch(f"/api/cards/{qty['id']}", json={"quantity": {**qty["quantity"], "value": 21}})
    assert r.json()["status"] == "edited" and r.json()["quantity"]["value"] == 21.0
    r = c.patch(f"/api/cards/{meth['id']}", json={"status": "rejected"})
    assert r.json()["status"] == "rejected"
    assert c.patch(f"/api/cards/{meth['id']}", json={"status": "maybe"}).status_code == 400
    assert c.patch("/api/cards/c_00000000", json={"status": "confirmed"}).status_code == 404
    assert c.patch("/api/cards/../../etc", json={"status": "confirmed"}).status_code in (404, 405)
    # add by hand: yours, confirmed, private by the setting (standalone notes are private by default)
    r = c.post(f"/api/papers/{pid}/cards", json={"kind": "claim", "text": "My reading: the sheath matters most.",
                                                   "locator": {"quote": "consistent with magnetic shielding"}})
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine["origin"] == "user" and mine["status"] == "confirmed" and mine["private"] is True
    assert mine["locator"]["verified"] is True and mine["locator"]["page"] == 2
    assert c.post(f"/api/papers/{pid}/cards", json={"kind": "quantity", "text": "x"}).status_code == 400
    assert c.post(f"/api/papers/{pid}/cards", json={"kind": "nope", "text": "x"}).status_code == 400
    # confirm all + delete
    r = c.patch(f"/api/cards/{claim['id']}", json={"status": "suggested"})
    assert c.post(f"/api/papers/{pid}/cards/confirm-all", json={}).json()["confirmed"] == 1
    assert c.delete(f"/api/cards/{mine['id']}").json() == {"ok": True}
    got = c.get(f"/api/papers/{pid}/cards").json()
    assert got["counts"] == {"suggested": 0, "confirmed": 1, "edited": 1, "rejected": 1}
    # the file is plain JSON, one per paper; deleting the paper moves it to the trash
    f = srv.store.root / "cards" / f"{pid}.json"
    assert json.loads(f.read_text(encoding="utf-8"))["paper"] == pid
    c.delete(f"/api/notes/{pid}")
    assert not f.exists() and list(srv.store.trash_dir.glob(f"{pid}-cards-*.json"))


def _three_papers(srv, c, use):
    a = paper(srv, c, "Langmuir probe plume survey A", PAPER_A)
    b = paper(srv, c, "Exit plane probe survey B", PAPER_B)
    cc = paper(srv, c, "CR model line ratios C", PAPER_C)

    def reply(prompt):
        if "35 ± 5 eV" in prompt:
            return cards_json(
                {"kind": "quantity", "quote": "At the exit plane the electron temperature was 35 ± 5 eV for the 300 V operating condition.",
                 "quantity": {"name": "electron temperature", "symbol": "T_e", "value": 35, "uncertainty": 5,
                              "unit": "eV", "conditions": "300 V operating condition, channel exit"}},
                {"kind": "claim", "text": "Magnetic shielding reduces channel wall erosion by two orders of magnitude.",
                 "quote": "Magnetic shielding lowered the erosion of channel walls by about two orders of magnitude."})
        if "2.3 × 10^5 K" in prompt:
            return cards_json({"kind": "quantity", "quote": "We find an electron temperature of 2.3 × 10^5 K at the channel exit at 300 V",
                               "quantity": {"name": "electron temperature", "symbol": "T_e", "value": "2.3 × 10^5",
                                            "unit": "K", "conditions": "300 V, channel exit"}})
        return cards_json(
            {"kind": "quantity", "quote": "The electron temperature was 20 eV at the channel exit for the 300 V operating condition.",
             "quantity": {"name": "electron temperature", "symbol": "T_e", "value": 20, "unit": "eV",
                          "conditions": "300 V operating condition, channel exit"}},
            {"kind": "claim", "text": "Magnetic shielding reduces channel wall erosion by two orders of magnitude.",
             "quote": "Magnetic shielding reduced the erosion of the channel walls by two orders of magnitude."})
    use(FakeAgent(reply))
    for pid in (a, b, cc):
        assert extract(c, pid)["state"] == "done"
    return a, b, cc


def test_cross_paper_links_conflicts_and_graph(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    links = c.get("/api/cards/links").json()["links"]
    q = {(l["a_paper"], l["b_paper"]): l for l in links if l["kind"] == "quantity"}
    pair = lambda x, y: q.get((x, y)) or q.get((y, x))
    assert pair(a, b)["conflict"] is True            # 20 eV vs 35 ± 5 eV
    assert pair(a, cc)["conflict"] is False          # 20 eV vs 2.3e5 K (19.8 eV)
    assert pair(b, cc)["conflict"] is True
    claims = [l for l in links if l["kind"] == "claim"]
    assert any({l["a_paper"], l["b_paper"]} == {a, b} for l in claims)
    assert (srv.store.root / ".index" / "card_links.json").exists()
    got = c.get(f"/api/papers/{a}/cards").json()
    tq = next(x for x in got["cards"] if x["kind"] == "quantity")
    assert tq["conflicts"] == 1 and tq["links"][0]["conflict"] and tq["links"][0]["paper"] == b
    assert tq["links"][0]["where"] == "p. 4"
    g = c.get("/api/graph").json()
    counts = {n["id"]: n.get("cards") for n in g["nodes"]}
    assert counts[a] == 2 and counts[b] == 2 and counts[cc] == 1
    assert {"a_paper", "b_paper", "kind", "conflict"} <= set(g["card_links"][0])
    assert any(l["conflict"] for l in g["card_links"])
    # rejecting a card removes its links (derived data follows the files)
    c.patch(f"/api/cards/{tq['id']}", json={"status": "rejected"})
    g = c.get("/api/graph").json()
    assert not any(a in (l["a_paper"], l["b_paper"]) and l["kind"] == "quantity" for l in g["card_links"])


def test_cards_search_table_and_csv(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    r = c.get("/api/cards", params={"kind": "quantity", "quantity": "electron temperature"}).json()
    assert r["total"] == 3 and {x["paper"] for x in r["cards"]} == {a, b, cc}
    assert r["quantities"][0] == {"name": "electron temperature", "count": 3}
    assert all(x["citekey"] for x in r["cards"])
    assert c.get("/api/cards", params={"kind": "quantity", "quantity": "T_e"}).json()["total"] == 3
    assert c.get("/api/cards", params={"kind": "quantity", "quantity": "ion density"}).json()["total"] == 0
    r = c.get("/api/cards", params={"q": "line ratios collisional"}).json()
    assert r["cards"] and r["cards"][0]["paper"] == cc
    r = c.get("/api/cards", params={"q": "shielding erosion", "kind": "claim"}).json()
    assert {x["paper"] for x in r["cards"]} == {a, b}
    assert c.get("/api/cards", params={"status": "known"}).json()["total"] == 0
    assert c.get("/api/cards", params={"kind": "bogus"}).status_code == 400
    # CSV
    r = c.get("/api/cards/export.csv", params={"kind": "quantity", "quantity": "electron temperature"})
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    assert "attachment" in r.headers["content-disposition"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    assert len(rows) == 3 and {row["canonical_unit"] for row in rows} == {"K"}
    assert {row["unit"] for row in rows} == {"eV", "K"} and any(row["conflicts"] == "2" for row in rows)
    # formula injection is neutralised
    c.post(f"/api/papers/{a}/cards", json={"kind": "claim", "text": "=HYPERLINK(\"http://x\")"})
    rows = list(csv.DictReader(io.StringIO(c.get("/api/cards/export.csv", params={"kind": "claim"}).text)))
    assert any(row["text"].startswith("'=") for row in rows)


def test_agent_tool_and_research_action(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    from app import agent as agent_mod
    from app.agents import research
    run = agent_mod.open_run("q")
    try:
        assert "cards_search" not in srv.agent_tools.available()       # nothing confirmed yet
        got = c.get("/api/cards", params={"kind": "quantity"}).json()["cards"]
        by_paper = {x["paper"]: x for x in got}
        c.patch(f"/api/cards/{by_paper[a]['id']}", json={"status": "confirmed"})
        c.patch(f"/api/cards/{by_paper[b]['id']}", json={"status": "confirmed", "private": True})
        assert "cards_search" in srv.agent_tools.available()
        out = srv.agent_tools.call("cards_search", {"query": "electron temperature", "kind": "quantity"}, run)
        assert f"[[{a}]] p. 2" in out and "2.32" in out and "Quote:" in out
        assert b not in out                    # private card
        assert cc not in out                   # suggested only
        with pytest.raises(ValueError):
            agent_mod.validate_args("cards_search", {"query": "x", "kind": 5, "bogus": 1})
        name, args = research.to_tool({"action": "cards", "query": "electron temperature", "kind": "quantity"})
        assert (name, args) == ("cards_search", {"query": "electron temperature", "kind": "quantity"})
        # a card from a private paper is never shown either
        c.patch(f"/api/cards/{by_paper[b]['id']}", json={"private": False})
        from app import privacy
        from app.store import set_private
        n = srv.store.get(b)
        set_private(n, True)
        srv.store.save(n)
        privacy.refresh()
        out = srv.agent_tools.call("cards_search", {"query": "electron temperature"}, run)
        assert b not in out and a in out
    finally:
        agent_mod.close_run(run)


def test_research_loop_uses_cards_action(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    got = c.get("/api/cards", params={"kind": "quantity"}).json()["cards"]
    for x in got:
        c.patch(f"/api/cards/{x['id']}", json={"status": "confirmed"})
    script = ['{"action": "cards", "query": "electron temperature", "kind": "quantity"}',
              '{"action": "answer", "text": "Three values [[%s]] p. 2."}' % a]
    seen = []

    def reply(prompt):
        seen.append(prompt)
        return script.pop(0) if script else '{"action": "answer", "text": "done"}'
    use(FakeAgent(reply, id="fakeresearch"))
    r = c.post("/api/research/stream", json={"question": "every measured electron temperature, with sources"})
    assert r.status_code == 200
    assert "Searching idea cards" in r.text
    assert any("2.3" in p and "Quote:" in p for p in seen[1:])


def test_chat_context_includes_confirmed_cards_only(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    got = c.get("/api/cards", params={"kind": "quantity"}).json()["cards"]
    target = next(x for x in got if x["paper"] == cc)
    c.patch(f"/api/cards/{target['id']}", json={"status": "confirmed"})
    agent = use(FakeAgent("An answer.", id="fakechat"))
    r = c.post("/api/chat", json={"question": "electron temperature at the channel exit", "history": []})
    assert r.status_code == 200
    sent = agent.prompts[-1]
    assert "CONFIRMED IDEA CARDS" in sent and "2.3" in sent
    assert sent.count("(quantity, confirmed by the user)") == 1


def test_module_api_for_other_features(server):
    srv, c, use = server
    a, b, cc = _three_papers(srv, c, use)
    from app import cards
    assert cards.for_paper(a) == []
    assert len(cards.for_paper(a, confirmed_only=False)) == 2
    first = cards.for_paper(a, confirmed_only=False)[0]
    c.patch(f"/api/cards/{first['id']}", json={"status": "confirmed"})
    assert [x["id"] for x in cards.for_paper(a)] == [first["id"]]
    hits = cards.search("electron temperature", k=5)
    assert all(h["status"] in ("confirmed", "edited") for h in hits) and "score" in (hits[0] if hits else {"score": 0})


def test_vault_zip_includes_cards(server, tmp_path):
    srv, c, use = server
    pid = paper(srv, c, "Zip paper", PAPER_A)
    c.post(f"/api/papers/{pid}/cards", json={"kind": "claim", "text": "Kept in the zip."})
    from app import vaultzip
    assert "cards" in vaultzip.TOP_DIRS
    r = c.get("/api/vault/export", params={"pdfs": "false"})
    assert r.status_code == 200
    import zipfile
    names = zipfile.ZipFile(io.BytesIO(r.content)).namelist()
    assert any(n.endswith(f"cards/{pid}.json") for n in names), names

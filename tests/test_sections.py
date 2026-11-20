"""Sections of the map (app/server.py _clusters): a paper you put in a section (its topic) is IN that section,
however similar it is to papers elsewhere."""
from __future__ import annotations

from test_cards import server, paper  # noqa: F401  (fixture)


def test_topic_members_share_one_section(server):  # noqa: F811
    srv, c, use = server
    plasma = [paper(srv, c, f"Hall thruster discharge oscillations {w}", "x", abstract=f"Hall thruster plasma discharge {w} ionization breathing mode oscillations anomalous electron transport xenon propellant")
              for w in ("alpha", "beta", "gamma", "delta")]
    stars = [paper(srv, c, f"Cool dwarf stellar atmospheres {w}", "x", abstract=f"Stellar spectra of cool dwarf stars {w} atmospheres metallicity abundances exoplanet hosts photometry")
             for w in ("one", "two", "three", "four", "five", "six", "seven")]
    g = c.get("/api/graph").json()
    cl = {n["id"]: n["cluster"] for n in g["nodes"]}
    assert cl[stars[0]] != cl[plasma[0]] and cl[stars[0]] >= 0  # two sections by similarity
    # put a star paper in the plasma section (its topic): it moves there, against its similarity
    t = c.post("/api/topics", json={"name": "Hall thrusters", "members": plasma[:2] + [stars[0]]}).json()
    assert t.get("slug")
    g = c.get("/api/graph").json()
    cl = {n["id"]: n["cluster"] for n in g["nodes"]}
    assert cl[stars[0]] == cl[plasma[0]] and all(cl[p] == cl[plasma[0]] for p in plasma)
    assert cl[stars[1]] != cl[plasma[0]]

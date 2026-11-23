"""A paper you add goes into the region it clearly belongs to, or you're asked which (app/placing.py)."""
from __future__ import annotations

import json
import urllib.request

import pytest

from rb_testserver import run_server

PLASMA = ["Hall thruster plasma sheath erosion of boron nitride walls", "Breathing mode oscillations in Hall thruster discharge plasma",
          "Xenon ion density in the Hall thruster plume and sheath", "Anomalous electron transport in Hall thruster plasma discharge"]
ROBOTS = ["Grasp planning for a robot manipulator with tactile sensor feedback", "Trajectory optimisation for robot arm kinematics and actuator limits",
          "Robot manipulator control with force sensor feedback for grasping", "Learning grasp poses for a robot gripper from camera images"]


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    vault = tmp_path_factory.mktemp("vault")
    with run_server(vault) as srv:
        yield srv.base


def api(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        return json.load(r)


def paper(base, title, abstract):
    return api(base, "POST", "/api/notes", {"type": "paper", "title": title, "abstract": abstract})["id"]


def test_new_papers_go_into_their_region_or_you_are_asked(server):
    plasma = [paper(server, t, t + ". Plasma discharge, ions, electrons, sheath, thruster.") for t in PLASMA]
    robots = [paper(server, t, t + ". Robot, manipulator, grasp, actuator, control.") for t in ROBOTS]
    api(server, "POST", "/api/topics", {"name": "Hall thrusters", "members": plasma})
    api(server, "POST", "/api/topics", {"name": "Robot grasping", "members": robots})
    # the first look: what's already in the library counts as looked at, and nothing is moved
    old = paper(server, "Sheath erosion in Hall thrusters", "Hall thruster plasma sheath erosion ions.")
    assert api(server, "POST", "/api/regions/place-new") == {"placed": [], "ask": []}

    new = paper(server, "Plasma sheath of a Hall thruster discharge with xenon ions",
                "Hall thruster plasma: electrons, ions and the sheath near the discharge channel walls; thruster erosion.")
    other = paper(server, "Medieval church architecture in northern Italy", "Romanesque churches, frescoes, bell towers and cloisters.")
    r = api(server, "POST", "/api/regions/place-new")
    got = {p["id"]: p for p in r["placed"]}
    assert new in got and got[new]["name"] == "Hall thrusters", r
    assert new in api(server, "GET", "/api/topics/hall-thrusters")["members"]
    assert other not in got and all(a["id"] != other for a in r["ask"]), r  # like none of them: left alone
    # each paper is looked at once
    again = api(server, "POST", "/api/regions/place-new")
    assert new not in {p["id"] for p in again["placed"]} and other not in {a["id"] for a in again["ask"]}
    # one that's already in a region (a folder of papers, Find papers for a region) is left there
    filed = paper(server, "Ion sheath in a Hall thruster plasma discharge", "Hall thruster plasma ions sheath electrons.")
    api(server, "POST", "/api/topics/robot-grasping/notes", {"ids": [filed]})
    r = api(server, "POST", "/api/regions/place-new")
    assert old not in api(server, "GET", "/api/topics/hall-thrusters")["members"]  # never sorted behind your back
    assert filed not in {p["id"] for p in r["placed"]}
    assert filed not in api(server, "GET", "/api/topics/hall-thrusters")["members"]


def test_close_to_two_regions_asks():
    from app.placing import decide
    go, options = decide([{"slug": "a", "name": "A", "z": 1.2}, {"slug": "b", "name": "B", "z": 0.9}, {"slug": "c", "name": "C", "z": 0.1}])
    assert go is None and [o["slug"] for o in options] == ["a", "b"]
    go, options = decide([{"slug": "a", "name": "A", "z": 1.6}, {"slug": "b", "name": "B", "z": 0.3}])
    assert go["slug"] == "a" and not options
    assert decide([{"slug": "a", "name": "A", "z": 0.2}]) == (None, [])
    assert decide([]) == (None, [])

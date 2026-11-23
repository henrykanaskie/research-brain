"""A paper you add: the app says which region it went into (Undo, Move), or asks which (static/placing.js)."""
from __future__ import annotations

from playwright.sync_api import expect

from test_e2e import api, clear_vault, open_app


def test_a_new_paper_goes_into_its_region_and_says_so(ctx, server):
    clear_vault(server)
    def paper(t, a):
        return api(server, "POST", "/api/notes", {"type": "paper", "title": t, "abstract": a})["id"]
    plasma = [paper(t, t + ". Plasma discharge, ions, electrons, sheath, thruster.") for t in (
        "Hall thruster plasma sheath erosion of boron nitride walls", "Breathing mode oscillations in Hall thruster discharge plasma",
        "Xenon ion density in the Hall thruster plume and sheath")]
    robots = [paper(t, t + ". Robot, manipulator, grasp, actuator, control.") for t in (
        "Grasp planning for a robot manipulator with tactile sensor feedback", "Trajectory optimisation for robot arm kinematics and actuator limits",
        "Robot manipulator control with force sensor feedback for grasping")]
    api(server, "POST", "/api/topics", {"name": "Hall thrusters", "members": plasma})
    api(server, "POST", "/api/topics", {"name": "Robot grasping", "members": robots})
    api(server, "POST", "/api/regions/place-new")  # (what's already here is never sorted)
    page = open_app(ctx, server)
    page.wait_for_function("() => window.__rb.S.nodes.size >= 6")
    new = paper("Plasma sheath of a Hall thruster discharge with xenon ions",
                "Hall thruster plasma: electrons, ions and the sheath near the discharge channel walls; thruster erosion.")
    page.evaluate("() => window.__rb.refreshGraph()")  # (the app's own add does this)
    card = page.locator(".place-card", has_text="Put")
    expect(card).to_contain_text("Hall thrusters", timeout=10000)
    assert new in api(server, "GET", "/api/topics/hall-thrusters")["members"]
    card.get_by_role("button", name="Undo").click()
    page.wait_for_function(f"() => fetch('/api/topics/hall-thrusters').then(r => r.json()).then(t => !t.members.includes('{new}'))")
    # one it can't tell: asked, with its likely regions
    both = paper("Robot arm for handling Hall thruster plasma parts", "Robot manipulator grasp actuator control, Hall thruster plasma sheath ions discharge.")
    page.evaluate("() => window.__rb.refreshGraph()")
    ask = page.locator(".place-card", has_text="Which region")
    expect(ask).to_be_visible(timeout=10000)
    ask.get_by_role("button", name="Robot grasping").click()
    expect(page.locator(".place-card", has_text="Put in Robot grasping")).to_be_visible()
    assert both in api(server, "GET", "/api/topics/robot-grasping")["members"]
    assert not page.errors

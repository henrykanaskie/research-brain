"""Browser test for reading status, topics and the reading queue: park a paper with a reason, create a
topic from the panel, add papers, open the queue, accept a recommendation, show the topic on the graph.

Semantic Scholar is mocked inside the server process (a small wrapper module patches app.scholar
before serving), so recommendations are deterministic and nothing touches the network.
Run:  python -m pytest -q tests/e2e/test_topics_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
"""
from __future__ import annotations

import json
import os
import urllib.request
from pathlib import Path

import pytest

from rb_testserver import run_server

ROOT = Path(__file__).resolve().parents[2]

try:
    from playwright.sync_api import Error as PWError
    from playwright.sync_api import expect, sync_playwright
except ImportError:  # pragma: no cover
    pytest.skip("playwright is not installed", allow_module_level=True)

MOCK = '''
import sys
sys.path.insert(0, {root!r})
from app import server as srv

def rec(s2, title, abstract):
    return {{"s2_id": s2, "title": title, "year": 2022, "authors": ["J. Boeuf"], "abstract": abstract,
            "doi": "", "arxiv": "", "venue": "", "url": "", "citationCount": 1}}
RECS = [rec("R1", "Wall erosion in magnetically shielded Hall thrusters", "channel wall erosion magnetic shielding"),
        rec("R2", "Kinetic simulation of the near-wall sheath", "sheath kinetic simulation")]
srv.scholar.references = lambda key, citing=False, max_items=1000: []
srv.scholar.recommend_for_papers = lambda pos, neg=None, limit=60: [r for r in RECS if r["s2_id"] not in (neg or [])]
srv.scholar.s2_paper = lambda pid: next((dict(r) for r in RECS if r["s2_id"] == pid), None)
srv.scholar.lookup = lambda q: None
srv.citations.MIN_INTERVAL = 0
app = srv.app
'''


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("topics_e2e")
    (tmp / "rb_mock_s2.py").write_text(MOCK.format(root=str(ROOT)))
    env = {"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0", "PYTHONPATH": f"{tmp}{os.pathsep}{ROOT}"}
    with run_server(tmp / "vault", env=env, app="rb_mock_s2:app", cwd=tmp, log_path=tmp / "server.log") as srv:
        yield {"base": srv.base, "vault": tmp / "vault"}


@pytest.fixture(scope="module")
def browser():
    with sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except PWError as e:
            exe = next((p for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium")
                        if p and Path(p).exists()), None)
            if not exe:
                pytest.skip(f"no browser for Playwright ({str(e).splitlines()[0]})")
            b = pw.chromium.launch(executable_path=exe)
        yield b
        b.close()


def test_park_topic_queue_accept_filter(server, browser):
    base = server["base"]
    ids = []
    for title, abstract, status, s2 in [
        ("Erosion of boron nitride channel walls", "channel wall erosion boron nitride", "read", "P1"),
        ("Magnetic shielding of a Hall thruster", "magnetic shielding wall erosion", "read", "P2"),
        ("Profilometry of thruster erosion", "erosion profilometry channel wall", "to-read", ""),
        ("Disruption prediction in tokamaks", "tokamak disruption neural network", "to-read", ""),
    ]:
        n = _call(base, "POST", "/api/notes", {"type": "paper", "title": title, "abstract": abstract})
        _call(base, "PUT", f"/api/notes/{n['id']}", {"status": status})
        if s2:
            p = server["vault"] / "notes" / f"{n['id']}.md"
            p.write_text(p.read_text().replace("s2_id: ''", f"s2_id: {s2}"))
        ids.append(n["id"])
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)  # see test_e2e.ctx
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 4")

    # park for later with a reason (L focuses "Saved because")
    page.evaluate(f"window.__rb.openNote('{ids[2]}')")
    page.wait_for_selector("#saved-because")
    page.evaluate("document.activeElement && document.activeElement.blur()")
    page.keyboard.press("l")
    expect(page.locator("#saved-because")).to_be_focused()
    page.keyboard.type("Need the erosion rates for the wall model")
    page.keyboard.press("Enter")
    expect(page.locator("#status-row button.on")).to_have_text("Later")
    page.wait_for_function(f"fetch('/api/notes/{ids[2]}').then(r => r.json()).then(n => n.saved_because.startsWith('Need'))")

    # create a topic from the panel's type-ahead, then add the other erosion papers
    page.locator("#topic-add").click()
    page.keyboard.type("Wall erosion")
    page.keyboard.press("Enter")
    expect(page.locator("#topic-row .tp-chip")).to_have_count(1)
    for nid in ids[:2]:
        page.evaluate(f"window.__rb.openNote('{nid}')")
        page.wait_for_function(f"window.__rb.S.cur && window.__rb.S.cur.id === '{nid}'")
        page.locator("#topic-add").click()
        page.keyboard.type("wall")
        page.wait_for_selector(".tp-sugg button")
        page.keyboard.press("Enter")
        expect(page.locator("#topic-row .tp-chip")).to_have_count(1)
    if shots:
        page.screenshot(path=f"{shots}/panel.png")

    # open the topic and its queue (recommendations computed on demand first)
    _call(base, "POST", "/api/topics/wall-erosion/queue/refresh?wait=1")
    page.evaluate("window.__rb.topics.open('wall-erosion')")
    page.wait_for_selector(".td .q-item.rec")
    assert page.locator(".td .q-item").count() <= 5
    if shots:
        page.screenshot(path=f"{shots}/queue.png")
    rec = page.locator(".td .q-item.rec").first
    title = rec.locator(".q-title").inner_text()
    rec.get_by_role("button", name="Accept").click()
    page.wait_for_function("t => [...document.querySelectorAll('.td .q-item:not(.rec) .q-title')].some(e => e.textContent === t)",
                           arg=title)
    d = _call(base, "GET", "/api/topics/wall-erosion")
    assert any(i["title"] == title for g in d["groups"] if g["status"] == "to-read" for i in g["items"])

    # show the topic on the graph: the tokamak paper (not a member) is faded
    page.get_by_role("button", name="Show on graph").click()
    page.wait_for_function("window.__rb.views.filter.topic === 'wall-erosion'")
    faded = page.evaluate("[...window.__rb.S.nodes.values()].filter(n => !window.__rb.S.nodeFilter(n)).map(n => n.id)")
    assert faded == [ids[3]]
    if shots:
        page.screenshot(path=f"{shots}/graph.png")
    assert not errors, errors
    page.close()

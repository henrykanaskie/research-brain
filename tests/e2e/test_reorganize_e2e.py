"""Browser test for Reorganize: papers filed by folder (each folder mixing two subjects) are regrouped by what
they're about, previewed and renamed before anything changes, linked by the AI connection analysis, and put back
with Undo; then the same into sections you name.

The AI agent is replaced inside the server process (a small wrapper module patches app.server._ask): it groups by
a keyword in the title and keeps the first candidate pair it's shown.
Run:  python -m pytest -q tests/e2e/test_reorganize_e2e.py   (RB_E2E_SHOTS=<dir> saves screenshots)
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
import json, re, sys
sys.path.insert(0, {root!r})
from app import server as srv

def fake_ask(instruction, context, purpose="ask", sources=None):
    papers = re.findall(r"^\\[\\[([a-z0-9-]+)\\]\\] (.*)$", context, re.M)
    if purpose == "connection analysis":
        pair = re.search(r"^([a-z0-9-]+) -- ([a-z0-9-]+)$", context, re.M)
        return json.dumps({{"links": [{{"a": pair.group(1), "b": pair.group(2), "why": "same subject"}}] if pair else []}})
    sheath = [i for i, t in papers if "heath" in t]
    rest = [i for i, t in papers if "heath" not in t]
    if "SECTIONS:" in context:
        return json.dumps({{"topics": [{{"name": "Plasma physics", "ids": sheath}}], "none": rest}})
    if "never make a new one" in instruction:  # only into existing topics: the sheath papers go to the first one
        first = re.search(r"^- (.+)$", context, re.M).group(1)
        return json.dumps({{"topics": [{{"name": first, "ids": sheath}}], "none": rest}})
    return json.dumps({{"topics": [{{"name": "Plasma sheaths", "ids": sheath}}, {{"name": "Language models", "ids": rest}}]}})

srv._ask = fake_ask
srv.scholar.lookup = lambda q: None
app = srv.app
'''


def _call(base, method, path, body=None):
    req = urllib.request.Request(base + path, method=method, data=None if body is None else json.dumps(body).encode(),
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("reorganize_e2e")
    (tmp / "rb_mock_ai.py").write_text(MOCK.format(root=str(ROOT)))
    env = {"RB_WATCH": "0", "RB_CITES_ON_IMPORT": "0", "RB_EMBEDDER": "tfidf", "PYTHONPATH": f"{tmp}{os.pathsep}{ROOT}"}
    with run_server(tmp / "vault", env=env, app="rb_mock_ai:app", cwd=tmp, log_path=tmp / "server.log") as srv:
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


def test_reorganize_by_content_then_into_your_sections(server, browser):
    base = server["base"]
    ids = [_call(base, "POST", "/api/notes", {"type": "paper", "title": t, "abstract": a})["id"] for t, a in [
        ("Sheath formation at a floating wall", "ion sheath presheath Bohm criterion wall potential"),
        ("Transformers for language", "attention tokens language model transformer"),
        ("Secondary electron emission and the sheath", "sheath wall secondary electron emission Bohm"),
        ("Scaling laws for language models", "language model scaling compute transformer tokens")]]
    _call(base, "POST", "/api/topics", {"name": "Folder A", "members": ids[:2]})
    _call(base, "POST", "/api/topics", {"name": "Folder B", "members": ids[2:]})
    shots = os.environ.get("RB_E2E_SHOTS")
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(15_000)
    page.goto(base + "/")
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 4")

    sent = []
    page.on("request", lambda r: sent.append(json.loads(r.post_data)) if r.url.endswith("/reorganize/plan") else None)
    page.locator("#btn-reorganize").click()
    card = page.locator(".bk-card[aria-label='Reorganize topics']")
    expect(card).to_be_visible()
    expect(card.locator(".bk-item")).to_have_count(3)  # two topics and "Papers with no topic"
    if shots:
        page.screenshot(path=f"{shots}/reorganize-1.png")
    card.locator(".bk-count").fill("2")
    card.get_by_role("button", name="Suggest topics").click()
    expect(card.locator(".bk-group")).to_have_count(2)
    assert sent[-1]["count"] == 2
    expect(card).to_contain_text("Left empty, so they go to the trash: Folder A, Folder B")
    card.locator(".bk-group .bk-name").first.fill("Sheath physics")
    card.locator(".bk-group details summary").first.click()
    if shots:
        page.screenshot(path=f"{shots}/reorganize-2.png")
    # nothing changed yet
    assert {t["name"] for t in _call(base, "GET", "/api/topics")} == {"Folder A", "Folder B"}
    card.get_by_role("button", name="Reorganize").click()
    expect(card).to_contain_text("Filed 4 papers into 2 topics")
    expect(card).to_contain_text("Found 1 connection")
    if shots:
        page.screenshot(path=f"{shots}/reorganize-3.png")
    tops = {t["name"]: t["count"] for t in _call(base, "GET", "/api/topics")}
    assert tops == {"Sheath physics": 2, "Language models": 2}
    # an AI connection on the map, not a link of yours (no red line you didn't draw)
    assert sum(len(_call(base, "GET", f"/api/notes/{i}")["links"]) for i in ids) == 0
    assert sum(1 for l in _call(base, "GET", "/api/graph")["links"] if l["kind"] == "ai") == 1

    card.get_by_role("button", name="Undo", exact=True).click()
    expect(card).to_contain_text("back the way they were")
    assert {t["name"] for t in _call(base, "GET", "/api/topics")} == {"Folder A", "Folder B"}
    card.get_by_role("button", name="Done").click()
    expect(card).to_have_count(0)

    # into your own sections, from one topic's "Split up…"
    page.evaluate("window.__rbBulk.reorganize({topics: [" + ",".join(
        f"'{t['slug']}'" for t in _call(base, "GET", "/api/topics")) + "]})")
    expect(card).to_be_visible()
    card.get_by_text("Use my own sections").click()
    card.locator("textarea.bk-sections").fill("Plasma physics\nOptics")
    card.locator("input[type=checkbox]").last.uncheck()  # no connection analysis this time
    card.get_by_role("button", name="Suggest topics").click()
    expect(card.locator(".bk-group")).to_have_count(2)
    expect(card).to_contain_text("2 papers fit none of these and will have no topic")
    card.get_by_role("button", name="Reorganize").click()
    expect(card).to_contain_text("Filed 4 papers into 2 topics")
    assert {t["name"]: t["count"] for t in _call(base, "GET", "/api/topics")} == {"Plasma physics": 2, "Optics": 0}
    assert not errors, errors


def test_imported_folders_sorted_by_content_or_only_into_existing_topics(server, browser, tmp_path):
    """A linked folder of PDFs in two subfolders, sorted by the AI into new topics and linked (not one topic per
    folder); then a copied-in flat folder sorted only into the existing topics (Plasma physics, Optics, from the
    test before): no new topic, one paper into the first topic, one that fits none."""
    import base64
    from test_api import make_pdf
    base = server["base"]
    root = tmp_path / "Research"
    for rel, title in [("A/one.pdf", "Sheath edge kinetics"), ("A/two.pdf", "Attention is what language needs"),
                       ("B/three.pdf", "Sheath heat transmission"), ("B/four.pdf", "Language model distillation")]:
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(make_pdf(title, f"{title}. " * 20))
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    errors = []
    page.on("pageerror", lambda e: errors.append(str(e)))
    page.set_default_timeout(20_000)
    page.goto(base + "/")
    page.wait_for_function("!!(window.__rbBulk && window.__rbBulk.linkDialog)")
    page.evaluate(f"window.__rbBulk.linkDialog({json.dumps(str(root))})")
    card = page.locator(".bk-card[aria-label='Add “Research”']")
    expect(card).to_be_visible()
    expect(card.locator(".bk-link")).to_be_visible()  # two folders: a topic for each, by default
    expect(card.locator(".bk-count")).to_be_hidden()
    card.get_by_text("Let the AI sort them into topics").click()
    expect(card.locator(".bk-link")).to_be_hidden()
    expect(card.get_by_text("Only into my existing topics (2)")).to_be_visible()  # Plasma physics, Optics
    card.locator(".bk-count").fill("2")
    card.get_by_role("button", name="Add 4 PDFs").click()
    expect(card).to_contain_text("Sorted into 2 topics", timeout=60_000)
    expect(card).to_contain_text("Found 1 connection", timeout=60_000)
    if os.environ.get("RB_E2E_SHOTS"):
        page.screenshot(path=f"{os.environ['RB_E2E_SHOTS']}/linked-ai.png")
    names = {t["name"] for t in _call(base, "GET", "/api/topics")}
    assert {"Plasma sheaths", "Language models"} <= names and not {"A", "B"} & names
    expect(page.locator(".toast.error")).to_have_count(0)
    assert not errors, errors
    page.close()

    # a copied-in flat folder, only into my existing topics
    pdfs = {"Inbox/sheath-probe.pdf": make_pdf("Sheath around a Langmuir probe", "probe sheath " * 30),
            "Inbox/optics.pdf": make_pdf("Adaptive optics for telescopes", "mirror wavefront " * 30)}
    page = browser.new_page(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    page.set_default_timeout(20_000)
    page.goto(base + "/")
    page.wait_for_function("!!(window.__rbBulk && window.__rbBulk.folderDialog)")
    page.evaluate("""(files) => window.__rbBulk.folderDialog(Object.entries(files).map(([rel, b64]) => {
        const bytes = Uint8Array.from(atob(b64), (c) => c.charCodeAt(0));
        const f = new File([bytes], rel.split('/').pop(), { type: 'application/pdf' });
        Object.defineProperty(f, 'webkitRelativePath', { value: rel });
        return f; }))""", {k: base64.b64encode(v).decode() for k, v in pdfs.items()})
    card = page.locator(".bk-card[aria-label='Import “Inbox”']")
    expect(card).to_be_visible()
    only = card.get_by_text("Only into my existing topics")
    expect(only).to_be_visible()  # a flat folder: the AI sorts it, and you have topics
    before = {t["name"]: t["count"] for t in _call(base, "GET", "/api/topics")}
    only.click()
    expect(card.locator(".bk-count")).to_be_disabled()
    card.locator("input[type=checkbox]").last.uncheck()  # no connections
    card.get_by_role("button", name="Import 2").click()
    expect(card).to_contain_text("1 paper fit none of your topics", timeout=60_000)
    after = {t["name"]: t["count"] for t in _call(base, "GET", "/api/topics")}
    assert set(after) == set(before)  # no new topic
    first = sorted(before)[0]
    assert after[first] == before[first] + 1

"""Through your library (app/libraryget.py): a paper without a PDF has the button; without a library link it says
where to add one, with one it opens the paper's DOI through it and the app starts watching Downloads.

Run:  python -m pytest -q tests/e2e/test_library_e2e.py
"""
from __future__ import annotations

import pytest

pytest.importorskip("playwright.sync_api")
from playwright.sync_api import expect  # noqa: E402

from test_e2e import api, clear_vault, open_app  # noqa: E402


def test_through_your_library(ctx, server):
    clear_vault(server)
    api(server, "PUT", "/api/settings", {"library_proxy": ""})
    n = api(server, "POST", "/api/notes", {"type": "paper", "title": "Tutorial: Physics and modeling of Hall thrusters",
                                           "doi": "10.1063/1.4972269"})
    page = open_app(ctx, server)
    page.wait_for_function("window.__rb && window.__rb.S.nodes.size >= 1")
    page.evaluate(f"window.__rb.openNote('{n['id']}')")
    btn = page.locator("[data-act=library]")
    expect(btn).to_have_text("Through your library")
    btn.click()
    expect(page.locator(".toast")).to_contain_text("sign-in link")
    api(server, "PUT", "/api/settings", {"library_proxy": "https://oregonstate.idm.oclc.org/login?url="})
    page.reload()
    page.wait_for_function("window.__rb && window.__rb.S.libraryProxy")
    page.evaluate(f"window.__rb.openNote('{n['id']}')")
    a = page.locator("a[data-act=library]")
    expect(a).to_have_attribute("href", "https://oregonstate.idm.oclc.org/login?url=https://doi.org/10.1063/1.4972269")
    a.evaluate("(el) => el.addEventListener('click', (e) => e.preventDefault())")  # no real page (no network in tests)
    a.click()
    page.wait_for_function(f"fetch('/api/notes/{n['id']}/library-access').then(r => r.json()).then(j => j.watching)")
    api(server, "PUT", "/api/settings", {"library_proxy": ""})

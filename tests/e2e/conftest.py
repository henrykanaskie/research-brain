"""Browser test fixtures shared by the Playwright tests in this folder (test_e2e.py, test_shortcuts.py,
test_csp_e2e.py): a real server (fake Claude, TF-IDF embeddings, temp vault), a Chromium, and a fresh
browser context per test that blocks every request not made to the test server.

A test file that defines its own `server` or `browser` fixture uses that one instead. Each test file gets its
own server (module scope), so a file's tests share one library and clean it with clear_vault (test_e2e.py)
where they need it empty.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from rb_testserver import run_server

TIMEOUT = 15_000  # ms, the default wait for every Playwright action (test_e2e.TIMEOUT is the same)


@pytest.fixture(scope="module")
def server(tmp_path_factory):
    vault = tmp_path_factory.mktemp("vault")
    with run_server(vault, log_path=vault.parent / "server.log") as srv:
        yield {"base": srv.base, "vault": vault}


def _chrome():
    for p in (os.environ.get("RB_E2E_CHROME"), "/usr/bin/google-chrome", "/usr/bin/chromium",
              "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"):
        if p and Path(p).exists():
            return p
    return None


@pytest.fixture(scope="module")
def browser():
    pw_api = pytest.importorskip("playwright.sync_api", reason="playwright is not installed")
    with pw_api.sync_playwright() as pw:
        kw = {"headless": not os.environ.get("RB_E2E_HEADED")}
        try:
            b = pw.chromium.launch(**kw)  # Playwright's own Chromium (CI)
        except pw_api.Error as e1:
            exe = _chrome()
            if not exe:
                pytest.skip(f"no browser for Playwright: run `python -m playwright install chromium` ({str(e1).splitlines()[0]})")
            b = pw.chromium.launch(executable_path=exe, **kw)
        yield b
        b.close()


@pytest.fixture()
def ctx(browser, server):
    # bypass_csp: Playwright evaluates wait_for_function("...") strings with eval, which the app's CSP
    # forbids. tests/e2e/test_csp_e2e.py checks the app itself without the bypass.
    c = browser.new_context(viewport={"width": 1400, "height": 900}, bypass_csp=True)
    c.set_default_timeout(TIMEOUT)
    external = []

    def route(r):
        url = r.request.url
        if url.startswith(server["base"]) or url.startswith(("data:", "blob:")):
            r.continue_()
        else:
            external.append(url)
            r.abort()
    c.route("**/*", route)
    c.external = external
    yield c
    c.close()
    assert not external, f"the app tried to reach the network: {external}"

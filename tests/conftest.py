"""Shared test setup: the test-server helper is importable from every test directory, and no test can hang
forever. Each test (setup and teardown included) gets RB_TEST_TIMEOUT seconds (default 240) through
pytest-timeout's thread method, which dumps every stack and ends the run; without the plugin a faulthandler
watchdog does the same. Use @pytest.mark.timeout(N) to change one test's limit.
"""
from __future__ import annotations

import faulthandler
import os
import sys
import tempfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent))  # tests/rb_testserver.py, also for tests/e2e
# No background open-access PDF downloads after imports (app/acquire.py) unless a test turns them on and
# mocks the downloader; inherited by servers started with rb_testserver.run_server.
os.environ.setdefault("RB_ACQUIRE", "0")
# Never read or write the real per-user data folder (settings.json, profiles, storage.log) from tests.
os.environ.setdefault("RB_DATA_DIR", tempfile.mkdtemp(prefix="rb-test-data-"))
os.environ.setdefault("RB_LEGACY_VAULT", os.path.join(os.environ["RB_DATA_DIR"], "no-legacy-vault"))

TIMEOUT = float(os.environ.get("RB_TEST_TIMEOUT", "240"))
try:
    import pytest_timeout  # noqa: F401
    HAVE_PLUGIN = True
except ImportError:  # pragma: no cover
    HAVE_PLUGIN = False


def pytest_configure(config):
    config.addinivalue_line("markers", "timeout(seconds): per-test time limit (pytest-timeout)")


def pytest_collection_modifyitems(config, items):
    if not HAVE_PLUGIN or TIMEOUT <= 0 or config.getoption("timeout", None):
        return  # an explicit --timeout on the command line wins
    for item in items:
        if not item.get_closest_marker("timeout"):
            item.add_marker(pytest.mark.timeout(TIMEOUT, method="thread"))


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_protocol(item, nextitem):
    if HAVE_PLUGIN or TIMEOUT <= 0:
        yield
        return
    faulthandler.dump_traceback_later(TIMEOUT, exit=True)  # pragma: no cover
    try:
        yield
    finally:
        faulthandler.cancel_dump_traceback_later()

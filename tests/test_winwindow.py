"""research brain's own Windows window (app/winwindow.py): when it's used, what the page's messages do, the
fallback to the Chrome/Edge window, and on Windows a real WebView2 window that opens, loads and closes."""
import json
import os
import subprocess
import sys
import textwrap
import time
import types
from pathlib import Path

import pytest

from rb_testserver import ROOT, run_server


def test_not_used_off_windows_or_when_switched_off(monkeypatch):
    from app import winwindow
    monkeypatch.setenv("RB_APP_WINDOW", "1")
    if os.name != "nt":
        assert winwindow.available() is False
    monkeypatch.setenv("RB_APP_WINDOW", "browser")
    assert winwindow.available() is False
    monkeypatch.setenv("RB_APP_WINDOW", "0")
    assert winwindow.available() is False


def test_messages_from_the_page(monkeypatch):
    from app import winwindow
    opened, destroyed = [], []
    monkeypatch.setattr(winwindow.webbrowser, "open", opened.append)
    page = winwindow._Page()
    page.win = types.SimpleNamespace(destroy=lambda: destroyed.append(1))
    winwindow._on_message(page, {"open": "https://doi.org/10.1/x"})
    winwindow._on_message(page, {"open": "file:///C:/Windows/system32/calc.exe"})  # never anything but the web
    winwindow._on_message(page, {"unsaved": True})
    assert opened == ["https://doi.org/10.1/x"] and page.unsaved and not page.may_close
    winwindow._on_message(page, {"close": True})
    time.sleep(0.2)
    assert page.may_close and destroyed == [1]
    winwindow._on_message(page, "not a dict")  # ignored


def test_only_its_own_page_gets_the_microphone():
    from app import winwindow
    assert winwindow._local("http://127.0.0.1:8765/?app=win")
    assert winwindow._local("http://localhost:8765/")
    assert not winwindow._local("https://example.org/")
    assert not winwindow._local("http://127.0.0.1.evil.example/")


def test_open_falls_back_to_the_chrome_window(monkeypatch):
    """The window can't come up (no WebView2, pythonnet won't load): the Chrome/Edge window, as before."""
    from app import launcher, winwindow
    opened = []
    monkeypatch.setattr(winwindow, "available", lambda: True)
    monkeypatch.setattr(winwindow, "run", lambda url, start: (start(), None)[1])
    monkeypatch.setattr(launcher, "_start_server", lambda a, url: True)
    monkeypatch.setattr(launcher, "open_window", lambda url: opened.append(url) or "app")
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert opened == ["http://127.0.0.1:8799/?app=1"]


def test_open_uses_the_window(monkeypatch):
    from app import launcher, winwindow
    seen = []
    monkeypatch.setattr(winwindow, "available", lambda: True)
    monkeypatch.setattr(winwindow, "run", lambda url, start: seen.append((url, start())) or 0)
    monkeypatch.setattr(launcher, "_start_server", lambda a, url: True)
    monkeypatch.setattr(launcher, "open_window", lambda url: pytest.fail("no Chrome window"))
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert seen == [("http://127.0.0.1:8799", True)]


@pytest.mark.skipif(os.name != "nt", reason="a real WebView2 window: Windows only")
def test_a_real_window_opens_loads_and_closes(tmp_path):
    from app import winwindow
    if not winwindow._webview2_installed():
        pytest.skip("no WebView2 runtime on this machine")
    import ctypes
    vault = tmp_path / "vault"
    vault.mkdir()
    data = tmp_path / "rb-data"
    with run_server(vault) as srv:
        script = textwrap.dedent(f"""
            import sys
            sys.path.insert(0, {str(ROOT)!r})
            from app import winwindow
            sys.exit(winwindow.run({srv.base!r}, lambda: True) or 0)
        """)
        env = {**os.environ, "RB_DATA_DIR": str(data), "RB_APP_WINDOW": "1"}
        err = tmp_path / "window.log"
        with open(err, "w", encoding="utf-8") as out:
            proc = subprocess.Popen([sys.executable, "-c", script], env=env, stdout=out, stderr=subprocess.STDOUT)
        try:
            hwnd = 0
            for _ in range(120):
                hwnd = ctypes.windll.user32.FindWindowW(None, winwindow.APP_NAME)
                if hwnd and (data / "window.json").exists():
                    break
                assert proc.poll() is None, err.read_text(encoding="utf-8", errors="replace")
                time.sleep(0.5)
            assert hwnd, "no window"
            assert json.loads((data / "window.json").read_text())["pid"] == proc.pid
            time.sleep(6)  # it loads the app, and stays up
            assert proc.poll() is None, err.read_text(encoding="utf-8", errors="replace")
            ctypes.windll.user32.PostMessageW(hwnd, 0x0010, 0, 0)  # WM_CLOSE: the close button
            assert proc.wait(30) == 0
        finally:
            if proc.poll() is None:
                proc.kill()
        log = err.read_text(encoding="utf-8", errors="replace")
        assert "window setup" not in log and "no window of its own" not in log and "window message" not in log, log
        assert not (data / "window.json").exists()


def test_the_window_fits_the_screen():
    from app import winwindow
    assert winwindow._fit((1920, 1040)) == ((1400, 900), (900, 600))
    assert winwindow._fit((1024, 728)) == ((984, 688), (900, 600))   # a small screen: its buttons stay on it
    assert winwindow._fit((800, 560)) == ((760, 520), (760, 520))
    assert winwindow._fit(None) is not None

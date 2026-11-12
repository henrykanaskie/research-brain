"""Opening research brain like an app, only when asked (app/launcher.py), and quitting when its windows close."""
import os
import socket
import subprocess
import sys
import time
import types
import urllib.request

import pytest


def test_open_reuses_a_running_server(monkeypatch):
    from app import launcher
    opened, started = [], []
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: True)
    monkeypatch.setattr(launcher, "open_window", lambda url: opened.append(url) or "app")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert opened == ["http://127.0.0.1:8799"] and started == []


def test_open_starts_a_server_that_quits_when_closed(monkeypatch, tmp_path):
    from app import launcher
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path))
    calls = {"status": 0}

    def status(url, timeout=1.0):
        calls["status"] += 1
        return calls["status"] > 3  # up after a moment
    started = []
    monkeypatch.setattr(launcher, "_status_ok", status)
    monkeypatch.setattr(launcher, "open_window", lambda url: "app")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: started.append((args, k)))
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="lab")) == 0
    args, kw = started[0]
    assert "serve" in args and "--quit-when-closed" in args and "--no-open" in args and args[-2:] == ["--profile", "lab"]
    assert kw.get("start_new_session") or kw.get("creationflags")  # detached from the launcher


def test_app_window_prefers_a_chromium_browser(monkeypatch):
    from app import launcher
    runs = []
    monkeypatch.setattr(launcher, "_chromium", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: runs.append(args))
    assert launcher.open_window("http://127.0.0.1:1") == "app" and runs[0] == ["/usr/bin/chromium", "--app=http://127.0.0.1:1"]
    monkeypatch.setenv("RB_APP_WINDOW", "0")
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: runs.append(["browser", u]))
    assert launcher.open_window("http://127.0.0.1:1") == "browser"


def test_install_app_entries(monkeypatch, tmp_path):
    from app import launcher
    monkeypatch.setattr(launcher, "_command", lambda: "/opt/rb/bin/research-brain")
    # Linux: an app-menu entry that runs `open`, no terminal
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "share"))
    out = launcher._linux_desktop(False)
    d = (tmp_path / "share" / "applications" / "research-brain.desktop").read_text(encoding="utf-8")
    assert 'Exec="/opt/rb/bin/research-brain" open' in d and "Terminal=false" in d and "menu" in out
    launcher._linux_desktop(True)
    assert not (tmp_path / "share" / "applications" / "research-brain.desktop").exists()
    # macOS: an app bundle made here (no download, so nothing to quarantine) that shows errors instead of doing nothing
    monkeypatch.setenv("HOME", str(tmp_path / "home"))
    monkeypatch.setenv("USERPROFILE", str(tmp_path / "home"))
    monkeypatch.setenv("RB_MAC_APP_DIR", str(tmp_path / "Apps"))
    monkeypatch.setattr(launcher.shutil, "which", lambda n: None)  # no sips/iconutil/codesign here
    out = launcher._mac_app(False)
    app = tmp_path / "Apps" / "research brain.app" / "Contents"
    plist = (app / "Info.plist").read_text(encoding="utf-8")
    assert "<key>CFBundleExecutable</key><string>research-brain</string>" in plist and "LSUIElement" not in plist
    exe = app / "MacOS" / "research-brain"
    script = exe.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and 'RB="/opt/rb/bin/research-brain"' in script
    assert '"$RB" open >>"$LOG"' in script and "display alert" in script and str(tmp_path / "Apps") in out
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
        # the command it points at is missing: it says so (the dialog is switched off for the test)
        assert subprocess.run(["sh", str(exe)], capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin", RB_NO_ALERT="1"), timeout=20).returncode == 1
    assert "Removed" in launcher._mac_app(True) and not (tmp_path / "Apps" / "research brain.app").exists()


def _free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.mark.skipif(sys.platform == "win32", reason="signal-based shutdown is checked on POSIX")
def test_server_stops_after_its_windows_close(tmp_path):
    port = _free_port()
    env = dict(os.environ, RB_IDLE_EXIT="3", RB_VAULT=str(tmp_path / "v"), RB_DATA_DIR=str(tmp_path / "d"),
               RB_METADATA="0", RB_EMBEDDER="tfidf", RB_FAKE_CLAUDE="1")
    p = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.server:app", "--port", str(port), "--log-level", "warning"],
                         env=env, cwd=os.path.dirname(os.path.dirname(__file__)), stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    try:
        up = False
        for _ in range(120):
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port}/api/heartbeat", timeout=1).close()
                up = True
                break
            except OSError:
                time.sleep(0.25)
        assert up
        # a window pinging keeps it alive past the idle time
        for _ in range(5):
            time.sleep(1)
            urllib.request.urlopen(f"http://127.0.0.1:{port}/api/heartbeat", timeout=2).close()
        assert p.poll() is None
        # then nothing: it stops by itself
        p.wait(timeout=30)
        assert p.returncode is not None
    finally:
        if p.poll() is None:
            p.kill()

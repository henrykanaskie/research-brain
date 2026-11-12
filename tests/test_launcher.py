"""Opening research brain like an app, only when asked (app/launcher.py), and quitting when its windows close."""
import os
import socket
import subprocess
import sys
import time
import types
import urllib.request

import pytest

from test_api import client  # noqa: F401  (fixture)


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
    # lsregister is found by its full path, so without this every run registered a test copy (same bundle id)
    # with Launch Services, and Launchpad could open that instead of the real app
    monkeypatch.setattr(launcher, "_mac_register", lambda app: None)
    out = launcher._mac_app(False)
    app = tmp_path / "Apps" / "research brain.app" / "Contents"
    plist = (app / "Info.plist").read_text(encoding="utf-8")
    assert "<key>CFBundleExecutable</key><string>research-brain</string>" in plist and "<key>LSUIElement</key><true/>" in plist
    exe = app / "MacOS" / "research-brain"
    script = exe.read_text(encoding="utf-8")
    assert script.startswith("#!/bin/sh\n") and 'RB="/opt/rb/bin/research-brain"' in script
    assert '"$RB" open >>"$LOG" 2>&1 ||' in script and script.rstrip().endswith("exit 0") and "display alert" in script and str(tmp_path / "Apps") in out
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
        # the command it points at is missing: it says so (the dialog is switched off for the test)
        assert subprocess.run(["sh", str(exe)], capture_output=True, env=dict(os.environ, PATH="/usr/bin:/bin", RB_NO_ALERT="1"), timeout=20).returncode == 1
    # with RB_MAC_APP_DIR set, --remove touches only that folder: it used to delete the real
    # /Applications/research brain.app on every test run
    outside = tmp_path / "home" / "Applications" / "research brain.app"
    outside.mkdir(parents=True)
    assert "Removed" in launcher._mac_app(True) and not (tmp_path / "Apps" / "research brain.app").exists()
    assert outside.exists()


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


def test_quit_endpoint_stops_the_server(client, monkeypatch):  # noqa: F811
    import signal
    import time as _t
    hits = []
    monkeypatch.setattr(signal, "raise_signal", lambda s: hits.append(s))
    assert client.post("/api/quit", json={}).json()["stopping"]
    for _ in range(40):
        if hits:
            break
        _t.sleep(0.05)
    assert hits == [signal.SIGINT]
    # not as a form post (what another website could send without asking)
    assert client.post("/api/quit", data={"x": "1"}).status_code in (400, 403, 415, 422)


def test_mac_applet_source_starts_in_background_and_quits_with_the_server():
    from app import launcher
    src = launcher._mac_applet_source('/opt/my "rb"/research-brain', 8765)
    assert 'property rbCmd : "/opt/my \\"rb\\"/research-brain"' in src
    assert "on reopen" in src and "on idle" in src and "continue quit" in src
    assert "/api/quit" in src and "http://127.0.0.1:8765" in src and "</dev/null >/dev/null 2>&1 &" in src


@pytest.mark.skipif(sys.platform != "darwin", reason="osacompile is part of macOS")
def test_mac_applet_really_compiles(tmp_path, monkeypatch):
    from app import launcher
    monkeypatch.setenv("RB_MAC_APP_DIR", str(tmp_path))
    monkeypatch.setattr(launcher, "_command", lambda: "/opt/rb/bin/research-brain")
    monkeypatch.setattr(launcher, "_mac_register", lambda app: None)  # don't register a test app with Spotlight
    out = launcher._mac_app(False)
    app = tmp_path / "research brain.app"
    assert "Keep in Dock" in out and (app / "Contents" / "Resources" / "Scripts" / "main.scpt").is_file()
    plist = subprocess.run(["plutil", "-convert", "xml1", "-o", "-", str(app / "Contents" / "Info.plist")],
                           capture_output=True, text=True).stdout
    assert "local.research-brain.launcher" in plist and "LSUIElement" not in plist
    assert "OSAAppletStayOpen" in plist  # stays open: the Dock icon lives as long as research brain runs
    src = subprocess.run(["osadecompile", str(app / "Contents" / "Resources" / "Scripts" / "main.scpt")],
                         capture_output=True, text=True).stdout
    assert "/opt/rb/bin/research-brain" in src


def test_package_never_imports_itself_as_app():
    """Installed, app/ is the research_brain package, so `from app import x` inside it only works from a
    checkout: the server the Mac app starts died on import and the app never opened."""
    import ast
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    bad = []
    for f in root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = [n.name for n in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [f"{f.relative_to(root)}:{node.lineno}" for n in names if n == "app" or n.startswith("app.")]
    assert bad == []


def test_server_imports_under_its_installed_name(tmp_path):
    """What `research-brain open` runs: uvicorn research_brain.server:app, with no `app` package around."""
    import shutil
    from pathlib import Path
    repo = Path(__file__).resolve().parent.parent
    pkg = tmp_path / "research_brain"
    shutil.copytree(repo / "app", pkg, ignore=shutil.ignore_patterns("__pycache__"))
    for extra in ("static", "extension", "scripts"):  # what the wheel adds (pyproject.toml: force-include)
        shutil.copytree(repo / extra, pkg / extra)
    code = ("import sys; sys.path.insert(0, sys.argv[1]); sys.modules['app'] = None; "
            "import research_brain.server, research_brain.cli")
    r = subprocess.run([sys.executable, "-c", code, str(tmp_path)], cwd=str(tmp_path), capture_output=True,
                       text=True, timeout=120, env=dict(os.environ, RB_DATA_DIR=str(tmp_path / "data")))
    assert r.returncode == 0, r.stderr[-2000:]

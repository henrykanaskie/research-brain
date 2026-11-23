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


@pytest.fixture(autouse=True)
def _browser_window(monkeypatch):
    """Tests use the browser path unless they ask for the native Mac window (app/macwindow.py): never pop
    up a real window."""
    monkeypatch.setenv("RB_APP_WINDOW", "browser")


class _Running:
    """A started server process that hasn't stopped (Popen's stand-in)."""
    def poll(self):
        return None


class _Stopped:
    """A started server process that stopped at once (what serve did under pythonw before 1.0.55)."""
    def poll(self):
        return 1


def test_open_when_the_server_stops_while_starting(monkeypatch, tmp_path):
    """The server stopped while starting: say so at once instead of waiting minutes for an answer that can't
    come, unless another research brain holds the port and is still starting there: that one is waited for."""
    from app import launcher
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: _Stopped())
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    asked = []
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: asked.append(1) is not None)
    monkeypatch.setattr(launcher, "_port_busy", lambda port: False)
    assert launcher._start_server(types.SimpleNamespace(port=8799, profile=""), "http://127.0.0.1:8799") is False
    assert len(asked) < 5
    answers = iter([False] * 6 + [True])
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: next(answers, True))
    monkeypatch.setattr(launcher, "_port_busy", lambda port: True)
    assert launcher._start_server(types.SimpleNamespace(port=8799, profile=""), "http://127.0.0.1:8799") is True


def test_serve_hands_its_output_to_the_server_on_windows(monkeypatch):
    """Opened from the Start menu (pythonw), the server gets serve's output (app.log) explicitly, or it has
    none and stops while starting, saying nothing."""
    from app import cli
    kw = cli._quiet_child()
    assert kw["creationflags"] == 0x08000000 and kw["stdin"] == cli.subprocess.DEVNULL
    assert kw["stdout"] == sys.stdout.fileno() or kw["stdout"] == cli.subprocess.DEVNULL
    monkeypatch.setattr(cli.sys, "stdout", None)
    monkeypatch.setattr(cli.sys, "stderr", None)
    kw = cli._quiet_child()
    assert kw["stdout"] == kw["stderr"] == cli.subprocess.DEVNULL


def test_open_reuses_a_running_server_or_starts_one_that_quits_when_closed(monkeypatch, tmp_path):
    """cmd_open opens a window on a server already running; otherwise it starts a detached `serve
    --quit-when-closed --no-open` (with the profile) and waits for it; it starts one only after a stopping
    server has let go of the port; nothing answering means not running; and that server is told to quit
    by itself once idle."""
    from app import launcher
    opened, started = [], []
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: True)
    monkeypatch.setattr(launcher, "open_window", lambda url: opened.append(url) or "app")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda *a, **k: started.append(a))
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert opened == ["http://127.0.0.1:8799/?app=1"] and started == []

    # nothing running: a server is started
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path))
    calls = {"status": 0}

    def status(url, timeout=1.0):
        calls["status"] += 1
        return calls["status"] > 3  # up after a moment
    started = []
    monkeypatch.setattr(launcher, "_status_ok", status)
    monkeypatch.setattr(launcher, "open_window", lambda url: "app")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: started.append((args, k)) or _Running())
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="lab")) == 0
    args, kw = started[0]
    assert "serve" in args and "--quit-when-closed" in args and "--no-open" in args and args[-2:] == ["--profile", "lab"]
    assert kw.get("start_new_session") or kw.get("creationflags")  # detached from the launcher

    # a server still stopping holds the port: wait for it
    busy = iter([True, True, True, False])
    monkeypatch.setattr(launcher, "_port_busy", lambda port: next(busy, False))
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    order = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: order.append("started") or _Running())
    launcher._start_server(types.SimpleNamespace(port=8799, profile=""), "http://127.0.0.1:8799")
    assert order == ["started"] and next(busy, "done") == "done"  # started only after the port was free

    # the real status check says "not running" when nothing answers on the port
    monkeypatch.undo()
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        free = s.getsockname()[1]
    assert launcher._status_ok(f"http://127.0.0.1:{free}", timeout=0.5) is False
    # and "running" when /api/status answers
    import http.server
    import threading

    class Status(http.server.BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200 if self.path == "/api/status" else 404)
            self.end_headers()
            self.wfile.write(b"{}")

        def log_message(self, *a):
            pass
    httpd = http.server.HTTPServer(("127.0.0.1", 0), Status)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        assert launcher._status_ok(f"http://127.0.0.1:{httpd.server_port}") is True
    finally:
        httpd.shutdown()
        httpd.server_close()

    # the server started that way quits on its own once its windows are gone (RB_IDLE_EXIT, unless already set)
    from app import cli
    children = []

    class Child:
        def __init__(self, args, env=None, **kw):
            children.append(env)

        def wait(self, *a):
            return 0

        def poll(self):
            return 0
    monkeypatch.delenv("RB_IDLE_EXIT", raising=False)
    monkeypatch.setenv("RB_VAULT", str(tmp_path / "serve-vault"))  # cmd_serve sets it; restored afterwards
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "serve-data"))
    monkeypatch.setattr(cli.subprocess, "Popen", Child)
    monkeypatch.setattr(cli, "_port_in_use", lambda host, port: False)
    monkeypatch.setattr(cli.signal, "signal", lambda *a: None)
    monkeypatch.setattr(cli, "_claude_hint", lambda: None)
    a = types.SimpleNamespace(host="127.0.0.1", port=free, vault=str(tmp_path / "serve-vault"), profile="",
                              no_open=True, quit_when_closed=True, log_level="warning")
    assert cli.cmd_serve(a) == 0
    assert children and children[0]["RB_IDLE_EXIT"] == "180"


def test_the_window_is_the_native_mac_one_a_chromium_app_or_the_browser(monkeypatch):
    """RB_APP_WINDOW=browser (the fixture) switches the native window off; open_window prefers a Chromium app
    window and falls back to the browser with RB_APP_WINDOW=0; cmd_open uses the native Mac window when it can."""
    from app import launcher, macwindow
    assert not macwindow.available()  # RB_APP_WINDOW=browser (the fixture) switches it off
    runs = []
    monkeypatch.setattr(launcher, "_chromium", lambda: "/usr/bin/chromium")
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda args, **k: runs.append(args))
    assert launcher.open_window("http://127.0.0.1:1") == "app" and runs[0] == ["/usr/bin/chromium", "--app=http://127.0.0.1:1"]
    monkeypatch.setenv("RB_APP_WINDOW", "0")
    monkeypatch.setattr(launcher.webbrowser, "open", lambda u: runs.append(["browser", u]))
    assert launcher.open_window("http://127.0.0.1:1") == "browser"
    monkeypatch.setenv("RB_APP_WINDOW", "browser")
    monkeypatch.setattr(macwindow, "available", lambda: True)
    ran = []
    monkeypatch.setattr(macwindow, "run", lambda url, start: ran.append((url, start)) or 0)
    monkeypatch.setattr(launcher, "open_window", lambda url: pytest.fail("no Chrome window on a Mac"))
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert ran[0][0] == "http://127.0.0.1:8799" and callable(ran[0][1])


@pytest.mark.skipif(sys.platform == "win32", reason="pretends to be Linux (os.name), which pathlib can't do on Windows")
def test_a_failed_start_says_so_without_a_terminal(monkeypatch, tmp_path):
    """From the Start menu or app menu there's no terminal: a failed start shows a dialog instead of nothing."""
    from app import launcher
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path))
    monkeypatch.delenv("RB_NO_ALERT", raising=False)
    monkeypatch.setattr(launcher, "_status_ok", lambda url, timeout=1.0: False)
    monkeypatch.setattr(launcher.time, "sleep", lambda s: None)
    monkeypatch.setattr(launcher.sys, "platform", "linux")
    monkeypatch.setattr(launcher.os, "name", "posix")
    monkeypatch.setattr(launcher.sys, "stderr", None)  # what pythonw and a menu launch give you
    monkeypatch.setattr(launcher.shutil, "which", lambda n: "/usr/bin/zenity" if n == "zenity" else None)
    shown = []
    monkeypatch.setattr(launcher.subprocess, "Popen", lambda cmd, **k: shown.append(cmd) or _Running())
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 1
    dialog = shown[-1]
    assert dialog[:2] == ["zenity", "--error"] and "app.log" in dialog[-1]


def test_install_app_entries(monkeypatch, tmp_path):
    """With the native window the Mac app's executable becomes `research-brain open` (exec): one Dock icon, which
    is the window itself. Linux gets an app-menu entry; without the native window the Mac app bundle shows errors
    instead of doing nothing; removing touches only RB_MAC_APP_DIR."""
    from app import launcher, macwindow
    monkeypatch.setattr(launcher, "_command", lambda: "/opt/rb/bin/research-brain")
    # native window: the executable is the window
    real_available = macwindow.available
    monkeypatch.setenv("RB_MAC_APP_DIR", str(tmp_path / "Native"))
    monkeypatch.setattr(macwindow, "available", lambda: True)
    monkeypatch.setattr(launcher, "_mac_register", lambda app: None)
    assert "Keep in Dock" in launcher._mac_app(False)
    app = tmp_path / "Native" / "research brain.app" / "Contents"
    plist = (app / "Info.plist").read_text(encoding="utf-8")
    assert "<key>CFBundleExecutable</key><string>research-brain</string>" in plist
    assert "LSUIElement" not in plist and "local.research-brain.launcher" in plist
    exe = app / "MacOS" / "research-brain"
    script = exe.read_text(encoding="utf-8")
    if sys.platform == "darwin":  # as the processor it was installed for, whatever macOS takes a script for
        import platform
        assert f'exec /usr/bin/arch -{platform.machine()} {sys.executable} "$RB" open' in script
    assert "LSArchitecturePriority" in plist
    assert "updating.json" in script and script.index("pgrep -f update_run.py") < script.index('! -x "$RB"')  # clicked during an update
    assert 'exec "$RB" open >>"$LOG" 2>&1' in script and 'RB="/opt/rb/bin/research-brain"' in script
    if os.name != "nt":
        assert os.access(exe, os.X_OK)
        r = subprocess.run(["sh", str(exe)], capture_output=True, env=dict(os.environ, RB_NO_ALERT="1"), timeout=20)
        assert r.returncode == 1  # the command isn't there: says so, doesn't hang
    monkeypatch.setattr(macwindow, "available", real_available)  # RB_APP_WINDOW=browser: off again
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


def test_quit_stops_the_server_and_a_stopping_server_says_so(client, monkeypatch):  # noqa: F811
    """POST /api/quit (JSON only, never a form post another website could send) raises SIGINT. Quit stops the
    server in the background: until it's gone it answers 503, so an app opened again right away doesn't attach to it."""
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
    from app import server
    server.STOPPING[0] = True
    try:
        r = client.get("/api/status")
    finally:
        server.STOPPING[0] = False
    assert r.status_code == 503 and r.json()["stopping"]


def test_mac_applet_starts_in_background_quits_with_the_server_and_really_compiles(tmp_path, monkeypatch):
    """The AppleScript applet source (quoting, reopen/idle/quit handlers, /api/quit); on macOS, osacompile builds
    an applet that stays open with the brain icon (RB_APP_WINDOW=browser: the AppleScript app, used without pywebview)."""
    from app import launcher
    src = launcher._mac_applet_source('/opt/my "rb"/research-brain', 8765)
    assert 'property rbCmd : "/opt/my \\"rb\\"/research-brain"' in src
    assert "on reopen" in src and "on idle" in src and "continue quit" in src
    assert "/api/quit" in src and "http://127.0.0.1:8765" in src and "</dev/null >/dev/null 2>&1 &" in src
    if sys.platform != "darwin":
        pytest.skip("osacompile is part of macOS")
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
    # the brain icon, not the script icon osacompile puts in its asset catalog
    assert "CFBundleIconName" not in plist and not (app / "Contents" / "Resources" / "Assets.car").exists()
    src = subprocess.run(["osadecompile", str(app / "Contents" / "Resources" / "Scripts" / "main.scpt")],
                         capture_output=True, text=True).stdout
    assert "/opt/rb/bin/research-brain" in src


def test_the_package_imports_only_relatively_and_under_its_installed_name(tmp_path):
    """Installed, app/ is the research_brain package, so `from app import x` inside it only works from a
    checkout: the server the Mac app starts died on import and the app never opened. Also runs what
    `research-brain open` runs: research_brain.server, with no `app` package around."""
    import ast
    import shutil
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent / "app"
    bad = []
    for f in root.rglob("*.py"):
        for node in ast.walk(ast.parse(f.read_text(encoding="utf-8"))):
            names = [n.name for n in node.names] if isinstance(node, ast.Import) else \
                [node.module or ""] if isinstance(node, ast.ImportFrom) and node.level == 0 else []
            bad += [f"{f.relative_to(root)}:{node.lineno}" for n in names if n == "app" or n.startswith("app.")]
    assert bad == []
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


def test_windows_shortcut_has_the_brain_icon_and_no_console(monkeypatch, tmp_path):
    """The Start-menu entry: pythonw (no console window), `open`, and static/icons/icon.ico."""
    from app import launcher
    env = tmp_path / "it's here" / "Scripts"  # an apostrophe must not break the PowerShell string
    env.mkdir(parents=True)
    (env / "pythonw.exe").write_bytes(b"")
    monkeypatch.setattr(launcher.sys, "executable", str(env / "python.exe"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "AppData"))
    ran = []
    monkeypatch.setattr(launcher, "_unicode_shortcut", lambda *a: False)  # no pywin32: the PowerShell way
    monkeypatch.setattr(launcher.subprocess, "run", lambda cmd, **k: ran.append(cmd) or types.SimpleNamespace(returncode=0, stderr=""))
    assert "Start menu" in launcher._windows_shortcut(False)
    ps = ran[0][-1]
    assert "$s.TargetPath='" + str(env / "pythonw.exe").replace("'", "''") + "'" in ps
    assert f"$s.Arguments='-m {launcher.__package__}.cli open'" in ps
    assert "icon.ico'" in ps and (launcher.paths.app_root() / "static" / "icons" / "icon.ico").is_file()


def test_icons_are_a_real_windows_ico_and_a_rounded_mac_png():
    """icon.ico holds PNGs in the sizes Windows uses. The Dock wants a rounded square with a transparent margin
    (Apple's 824-in-1024 grid); the full-bleed web icon showed as a sharp square."""
    import struct
    import zlib
    from pathlib import Path
    data = (Path(__file__).resolve().parent.parent / "static" / "icons" / "icon.ico").read_bytes()
    reserved, kind, count = struct.unpack_from("<HHH", data)
    assert (reserved, kind) == (0, 1)
    sizes = set()
    for i in range(count):
        w, _h, _c, _r, _p, _b, size, off = struct.unpack_from("<BBBBHHII", data, 6 + 16 * i)
        assert data[off:off + 8] == b"\x89PNG\r\n\x1a\n" and off + size <= len(data)
        sizes.add(w or 256)
    assert {16, 32, 48, 256} <= sizes
    data = (Path(__file__).resolve().parent.parent / "static" / "icons" / "icon-mac.png").read_bytes()
    w, h, depth, color = struct.unpack(">IIBB", data[16:26])
    assert data[:8] == b"\x89PNG\r\n\x1a\n" and (w, h, depth, color) == (1024, 1024, 8, 6)  # 8-bit RGBA
    raw, pos = b"", 8
    while pos < len(data):
        n, kind = struct.unpack(">I4s", data[pos:pos + 8])
        if kind == b"IDAT":
            raw += data[pos + 8:pos + 8 + n]
        pos += 12 + n
    first = zlib.decompress(raw)[:1 + 4 * w]  # filter byte + the top row
    assert first[0] == 0 and first[1 + 3] == 0 and first[1 + 4 * 512 + 3] == 0  # the top edge is transparent


def test_window_messages_from_the_page_and_text_selection(monkeypatch):
    """The page's messages to the Mac window: keep-clear areas, opening only http(s) links, dictation on and off,
    and closing with unsaved edits only once the page says so. pywebview blocks text selection on the whole page
    unless the window asks for it."""
    from pathlib import Path
    from app import macwindow
    opened = []
    monkeypatch.setattr(macwindow.webbrowser, "open", lambda u: opened.append(u))
    h = macwindow._Header()
    macwindow._on_message(h, '{"keep_clear": [[1, 2, 3, 4], [10, 0, 5, 5]]}')
    assert h.keep_clear == [[1.0, 2.0, 3.0, 4.0], [10.0, 0.0, 5.0, 5.0]]
    macwindow._on_message(h, '{"open": "http://127.0.0.1:8765/viewer/x"}')
    macwindow._on_message(h, '{"open": "file:///etc/passwd"}')
    macwindow._on_message(h, "not json")
    assert opened == ["http://127.0.0.1:8765/viewer/x"]
    # the chat's mic: macOS dictation on and off
    dict_calls = []
    monkeypatch.setattr(macwindow, "_dictate", lambda on: dict_calls.append(on))
    macwindow._on_message(h, '{"dictate": true}')
    macwindow._on_message(h, '{"dictate": false}')
    assert dict_calls == [True, False]
    # unsaved edits: the window learns it, and closes only once the page's own dialog says so
    destroyed = []

    class Win:
        def destroy(self):
            destroyed.append(1)
    h.win = Win()
    macwindow._on_message(h, '{"unsaved": true}')
    assert h.unsaved and not h.may_close
    macwindow._on_message(h, '{"close": true}')
    import time
    for _ in range(50):
        if destroyed:
            break
        time.sleep(0.01)
    assert h.may_close and destroyed == [1]
    src = (Path(__file__).resolve().parent.parent / "app" / "macwindow.py").read_text()
    call = src[src.index("webview.create_window("):]
    call = call[:call.index(")\n")]
    assert "text_select=True" in call


def test_window_header_drags_except_on_its_buttons(monkeypatch):
    """A press on an empty part of research brain's header drags the window (a double-click zooms it);
    its buttons and fields, the window's own buttons and everything below the header work as usual."""
    from app import macwindow
    did = []
    monkeypatch.setattr(macwindow, "_drag", lambda w, e: did.append("drag"))
    monkeypatch.setattr(macwindow, "_zoom", lambda w: did.append("zoom"))
    S = types.SimpleNamespace

    class WebKitHost:
        pass

    class ThemeWidget:
        pass

    def window(full_screen=False):
        w = S(frame=lambda: S(size=S(height=800)), styleMask=lambda: (1 << 14) if full_screen else 0)
        w.contentView = lambda: S(superview=lambda: S(hitTest_=lambda p: ThemeWidget() if p.x < 70 and p.y > 780 else WebKitHost()))
        return w
    nsw = window()
    header = macwindow._Header()
    header.keep_clear = [[300, 10, 200, 32]]  # the search box

    def press(x, y_from_top, clicks=1, w=nsw):
        ev = S(window=lambda: w, locationInWindow=lambda: S(x=x, y=800 - y_from_top), clickCount=lambda: clicks)
        return macwindow._header_press(w, header, ev)
    assert press(150, 26) is None and did == ["drag"]          # empty header: drags
    assert press(150, 26, clicks=2) is None and did[-1] == "zoom"
    n = len(did)
    assert press(400, 26) is not None                          # the search box
    assert press(30, 12) is not None                           # the red button
    assert press(150, 300) is not None                         # the map
    assert press(150, 26, w=window(full_screen=True)) is not None
    assert len(did) == n

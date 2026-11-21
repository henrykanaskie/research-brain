"""Windows and Linux behaviour that can be checked on any OS: npm .cmd shims, unsafe batch arguments,
reserved file names, install folders, line endings and the backup command. Also research brain and the Windows
shell (app/winshell.py: its taskbar ID on the process and the Start menu shortcut, voice typing; off Windows
everything quietly says no) and its own Windows window (app/winwindow.py: when it's used, what the page's
messages do, the fallback to the Chrome/Edge window, and on Windows a real WebView2 window that opens, loads
and closes)."""
import json
import os
import subprocess
import sys
import textwrap
import time
import types

import pytest

from rb_testserver import ROOT, run_server


NPM_SHIM = r"""@ECHO off
GOTO start
:find_dp0
SET dp0=%~dp0
EXIT /b
:start
SETLOCAL
CALL :find_dp0

IF EXIST "%dp0%\node.exe" (
  SET "_prog=%dp0%\node.exe"
) ELSE (
  SET "_prog=node"
  SET PATHEXT=%PATHEXT:;.JS;=;%
)

endLocal & goto #_undefined_# 2>NUL || title %COMSPEC% & "%_prog%"  "%dp0%\node_modules\@anthropic-ai\claude-code\cli.js" %*
"""


@pytest.fixture()
def win(monkeypatch):
    from app import procs
    monkeypatch.setattr(procs, "WINDOWS", True)
    return procs


def test_commands_resolve_safely_on_windows(tmp_path, win, monkeypatch):
    """An npm .cmd shim runs node on its script directly (a node.exe beside the shim wins), so any prompt is
    passed as is; an unknown batch file takes harmless arguments but refuses quotes, &, %, | and line breaks;
    an .exe takes anything."""
    shim = tmp_path / "claude.cmd"
    shim.write_text(NPM_SHIM, encoding="utf-8")
    script = tmp_path / "node_modules" / "@anthropic-ai" / "claude-code" / "cli.js"
    script.parent.mkdir(parents=True)
    script.write_text("// cli", encoding="utf-8")
    monkeypatch.setattr(win.shutil, "which", lambda n: "/usr/bin/node" if n == "node" else None)
    prompt = 'Explain "this" & that %PATH% !x!\nsecond line'
    out = win.resolve([str(shim), "-p", prompt])
    assert out[0] == "/usr/bin/node" and out[1] == str(script.resolve()) and out[2:] == ["-p", prompt]
    (tmp_path / "node.exe").write_text("", encoding="utf-8")  # a node.exe beside the shim wins
    assert win.resolve([str(shim), "x"])[0] == str(tmp_path / "node.exe")

    bat = tmp_path / "tool.bat"
    bat.write_text("@echo off\r\nsomething.exe %*\r\n", encoding="utf-8")
    assert win.resolve([str(bat), "--version"]) == [str(bat), "--version"]  # harmless arguments are fine
    for bad in ('a "quoted" word', "a & b", "100%", "line\nbreak", "x | y"):
        with pytest.raises(win.UnsafeCommand):
            win.resolve([str(bat), "-p", bad])
    exe = tmp_path / "codex.exe"
    exe.write_text("", encoding="utf-8")
    assert win.resolve([str(exe), "-p", 'any "text" & more']) == [str(exe), "-p", 'any "text" & more']


def test_windows_names_and_install_folders(win, monkeypatch, tmp_path):
    """Windows executable names come .exe first, the install folders include npm's and the native .local/bin,
    and an agent installed as an npm .cmd shim is found by its Windows name."""
    from app.agents import base
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert win.exe_names("claude")[:2] == ["claude.exe", "claude.cmd"]
    dirs = [str(d) for d in win.install_dirs()]
    assert str(tmp_path / "Roaming" / "npm") in dirs
    assert any(d.endswith(".local/bin") or d.endswith(".local\\bin") for d in dirs)  # claude.exe's native install

    npm = tmp_path / "Roaming" / "npm"
    npm.mkdir(parents=True)
    (npm / "gemini.cmd").write_text(NPM_SHIM, encoding="utf-8")
    monkeypatch.setattr(base.shutil, "which", lambda n: None)
    assert base.find_executable(["gemini"]) == str(npm / "gemini.cmd")


def test_note_files_are_windows_safe(tmp_path):
    """Reserved Windows names (CON, NUL, COM1) get a suffix in their slug, and note files use LF line endings."""
    from app.store import Store, slugify
    assert slugify("CON") == "con-note" and slugify("nul") == "nul-note" and slugify("COM1") == "com1-note"
    assert slugify("Console output") == "console-output"
    s = Store(tmp_path / "vault")
    n = s.create({"type": "note", "title": "Line endings", "body": "one\ntwo"})
    raw = s.note_path(n["id"]).read_bytes()
    assert b"\r\n" not in raw and b"one\ntwo" in raw


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not installed")
def test_backup_snapshot(tmp_path):
    """`research-brain backup` commits the library once (PDFs ignored), and says so when nothing changed."""
    from app import backup
    lib = tmp_path / "lib"
    (lib / "pdfs").mkdir(parents=True)
    (lib / "a.md").write_text("first", encoding="utf-8")
    (lib / "pdfs" / "x.pdf").write_bytes(b"%PDF")
    assert backup.snapshot(lib).startswith("Saved snapshot")
    assert backup.snapshot(lib) == "No changes since the last snapshot."
    tracked = subprocess.run(["git", "ls-files"], cwd=lib, capture_output=True, text=True).stdout.split()
    assert "a.md" in tracked and ".gitignore" in tracked and not any(t.startswith("pdfs/") for t in tracked)


def test_kill_tree_stops_a_process():
    """kill_tree ends a child started with popen_kwargs."""
    from app import procs
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **procs.popen_kwargs())
    procs.kill_tree(p)
    p.wait(timeout=10)
    assert p.returncode is not None


# ---------------------------------------------------------------- the Windows shell (app/winshell.py)
def test_winshell_off_windows_and_the_start_menu_path(monkeypatch, tmp_path):
    """Off Windows every winshell call quietly says no; on any OS a missing shortcut is left alone, and the
    Start menu entry lives in APPDATA under the app's name."""
    from app import launcher, winshell
    if os.name != "nt":
        lnk = tmp_path / "x.lnk"
        lnk.write_bytes(b"")
        assert winshell.set_app_id() is False
        assert winshell.shortcut_app_id(lnk) is None
        assert winshell.set_shortcut_app_id(lnk) is False
        assert winshell.ensure_shortcut() is False
        assert winshell.set_window_relaunch(1, "x", "y") is False
        assert winshell.voice_typing() is False

    assert winshell.shortcut_app_id(tmp_path / "none.lnk") is None
    assert winshell.set_shortcut_app_id(tmp_path / "none.lnk") is False

    monkeypatch.setenv("APPDATA", str(tmp_path))
    p = launcher.windows_shortcut_path()
    assert p.parent == tmp_path / "Microsoft" / "Windows" / "Start Menu" / "Programs"
    assert p.name == f"{launcher.APP_NAME}.lnk"


def _make_lnk(path):
    q = lambda v: "'" + str(v).replace("'", "''") + "'"
    target = os.environ.get("COMSPEC") or r"C:\Windows\System32\cmd.exe"
    ps = (f"$s=(New-Object -ComObject WScript.Shell).CreateShortcut({q(path)});"
          f"$s.TargetPath={q(target)};$s.Save()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], check=True, capture_output=True)


@pytest.mark.skipif(os.name != "nt", reason="the Windows shell")
def test_shortcuts_and_the_process_carry_the_app_id(monkeypatch, tmp_path):
    """A shortcut gets the app's ID set on it; ensure_shortcut fixes an older Start menu entry and leaves a right
    one alone; and the process itself takes the app's ID."""
    from app import launcher, winshell
    lnk = tmp_path / "rb test's entry.lnk"
    _make_lnk(lnk)
    assert winshell.shortcut_app_id(lnk) is None
    assert winshell.set_shortcut_app_id(lnk) is True
    assert winshell.shortcut_app_id(lnk) == winshell.APP_ID

    monkeypatch.setenv("APPDATA", str(tmp_path))
    lnk = launcher.windows_shortcut_path()
    lnk.parent.mkdir(parents=True)
    _make_lnk(lnk)
    assert winshell.ensure_shortcut() is True
    assert winshell.shortcut_app_id(lnk) == winshell.APP_ID
    assert winshell.ensure_shortcut() is True  # already right: left as it is

    code = ("import ctypes,sys;from app import winshell;assert winshell.set_app_id();"
            "p=ctypes.c_wchar_p();ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(p));"
            "sys.stdout.write(p.value)")
    out = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=str(ROOT))
    assert out.returncode == 0, out.stderr
    assert out.stdout == winshell.APP_ID


# ---------------------------------------------------------------- the Windows window (app/winwindow.py)
def test_winwindow_rules(monkeypatch):
    """The window is not used off Windows or when RB_APP_WINDOW says browser or 0; only its own page (loopback)
    gets the microphone; and the window fits the screen, buttons included, centred."""
    from app import winwindow
    monkeypatch.setenv("RB_APP_WINDOW", "1")
    if os.name != "nt":
        assert winwindow.available() is False
    monkeypatch.setenv("RB_APP_WINDOW", "browser")
    assert winwindow.available() is False
    monkeypatch.setenv("RB_APP_WINDOW", "0")
    assert winwindow.available() is False

    assert winwindow._local("http://127.0.0.1:8765/?app=win")
    assert winwindow._local("http://localhost:8765/")
    assert not winwindow._local("https://example.org/")
    assert not winwindow._local("http://127.0.0.1.evil.example/")

    assert winwindow._fit((1920, 1040, 0, 0)) == ((1400, 900), (900, 600), (260, 70))
    # a small screen: the whole window, its buttons included, stays on it, centred
    assert winwindow._fit((1024, 728, 0, 0)) == ((984, 688), (900, 600), (20, 20))
    assert winwindow._fit((800, 560, 0, 0)) == ((760, 520), (760, 520), (20, 20))
    assert winwindow._fit((1280, 760, 0, 40))[2] == (20, 60)  # a taskbar at the top


def test_messages_from_the_page(monkeypatch):
    """The page's messages open web links only, track unsaved work, and close the window when asked."""
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


def test_open_uses_the_window_or_falls_back_to_chrome(monkeypatch):
    """`open` runs the app in its own window when it is available; when the window can't come up (no WebView2,
    pythonnet won't load) it opens the Chrome/Edge window, as before."""
    from app import launcher, winwindow
    seen = []
    monkeypatch.setattr(winwindow, "available", lambda: True)
    monkeypatch.setattr(launcher, "_start_server", lambda a, url: True)
    monkeypatch.setattr(winwindow, "run", lambda url, start: seen.append((url, start())) or 0)
    monkeypatch.setattr(launcher, "open_window", lambda url: pytest.fail("no Chrome window"))
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert seen == [("http://127.0.0.1:8799", True)]

    opened = []
    monkeypatch.setattr(winwindow, "run", lambda url, start: (start(), None)[1])
    monkeypatch.setattr(launcher, "open_window", lambda url: opened.append(url) or "app")
    assert launcher.cmd_open(types.SimpleNamespace(port=8799, profile="")) == 0
    assert opened == ["http://127.0.0.1:8799/?app=1"]


@pytest.mark.skipif(os.name != "nt", reason="a real WebView2 window: Windows only")
def test_a_real_window_opens_loads_and_closes(tmp_path):
    """A real WebView2 window comes up with the app's title, records its pid, stays up while it loads, closes
    cleanly on WM_CLOSE with nothing in its log, and removes its window.json."""
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

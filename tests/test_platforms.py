"""Windows and Linux behaviour that can be checked on any OS: npm .cmd shims, unsafe batch arguments,
reserved file names, install folders, line endings and the backup command."""
import subprocess
import sys

import pytest


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


def test_npm_shim_runs_node_directly(tmp_path, win, monkeypatch):
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


def test_unknown_batch_file_refuses_unsafe_arguments(tmp_path, win):
    bat = tmp_path / "tool.bat"
    bat.write_text("@echo off\r\nsomething.exe %*\r\n", encoding="utf-8")
    assert win.resolve([str(bat), "--version"]) == [str(bat), "--version"]  # harmless arguments are fine
    for bad in ('a "quoted" word', "a & b", "100%", "line\nbreak", "x | y"):
        with pytest.raises(win.UnsafeCommand):
            win.resolve([str(bat), "-p", bad])
    exe = tmp_path / "codex.exe"
    exe.write_text("", encoding="utf-8")
    assert win.resolve([str(exe), "-p", 'any "text" & more']) == [str(exe), "-p", 'any "text" & more']


def test_names_and_install_folders(win, monkeypatch, tmp_path):
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "Local"))
    assert win.exe_names("claude")[:2] == ["claude.exe", "claude.cmd"]
    dirs = [str(d) for d in win.install_dirs()]
    assert str(tmp_path / "Roaming" / "npm") in dirs
    assert any(d.endswith(".local/bin") or d.endswith(".local\\bin") for d in dirs)  # claude.exe's native install


def test_agent_found_by_its_windows_name(win, monkeypatch, tmp_path):
    from app.agents import base
    npm = tmp_path / "Roaming" / "npm"
    npm.mkdir(parents=True)
    (npm / "gemini.cmd").write_text(NPM_SHIM, encoding="utf-8")
    monkeypatch.setenv("APPDATA", str(tmp_path / "Roaming"))
    monkeypatch.setattr(base.shutil, "which", lambda n: None)
    assert base.find_executable(["gemini"]) == str(npm / "gemini.cmd")


def test_windows_reserved_note_names():
    from app.store import slugify
    assert slugify("CON") == "con-note" and slugify("nul") == "nul-note" and slugify("COM1") == "com1-note"
    assert slugify("Console output") == "console-output"


def test_library_files_use_lf_line_endings(tmp_path):
    from app.store import Store
    s = Store(tmp_path / "vault")
    n = s.create({"type": "note", "title": "Line endings", "body": "one\ntwo"})
    raw = s.note_path(n["id"]).read_bytes()
    assert b"\r\n" not in raw and b"one\ntwo" in raw


@pytest.mark.skipif(not __import__("shutil").which("git"), reason="git not installed")
def test_backup_snapshot(tmp_path):
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
    from app import procs
    p = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)"], **procs.popen_kwargs())
    procs.kill_tree(p)
    p.wait(timeout=10)
    assert p.returncode is not None

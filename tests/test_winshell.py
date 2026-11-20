"""research brain and the Windows shell (app/winshell.py): its taskbar ID on the process and the Start menu
shortcut, and voice typing; off Windows everything quietly says no."""
import os
import subprocess

import pytest

from app import winshell


@pytest.mark.skipif(os.name == "nt", reason="off Windows only")
def test_off_windows_nothing_happens(tmp_path):
    lnk = tmp_path / "x.lnk"
    lnk.write_bytes(b"")
    assert winshell.set_app_id() is False
    assert winshell.shortcut_app_id(lnk) is None
    assert winshell.set_shortcut_app_id(lnk) is False
    assert winshell.ensure_shortcut() is False
    assert winshell.set_window_relaunch(1, "x", "y") is False
    assert winshell.voice_typing() is False


def test_a_missing_shortcut_is_left_alone(tmp_path):
    assert winshell.shortcut_app_id(tmp_path / "none.lnk") is None
    assert winshell.set_shortcut_app_id(tmp_path / "none.lnk") is False


def test_the_start_menu_entry_lives_in_appdata(monkeypatch, tmp_path):
    from app import launcher
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
def test_the_shortcut_carries_the_app_id(tmp_path):
    lnk = tmp_path / "rb test's entry.lnk"
    _make_lnk(lnk)
    assert winshell.shortcut_app_id(lnk) is None
    assert winshell.set_shortcut_app_id(lnk) is True
    assert winshell.shortcut_app_id(lnk) == winshell.APP_ID


@pytest.mark.skipif(os.name != "nt", reason="the Windows shell")
def test_ensure_shortcut_fixes_an_older_entry(monkeypatch, tmp_path):
    from app import launcher
    monkeypatch.setenv("APPDATA", str(tmp_path))
    lnk = launcher.windows_shortcut_path()
    lnk.parent.mkdir(parents=True)
    _make_lnk(lnk)
    assert winshell.ensure_shortcut() is True
    assert winshell.shortcut_app_id(lnk) == winshell.APP_ID
    assert winshell.ensure_shortcut() is True  # already right: left as it is


@pytest.mark.skipif(os.name != "nt", reason="the Windows shell")
def test_the_process_gets_the_app_id():
    import subprocess as sp
    import sys
    code = ("import ctypes,sys;from app import winshell;assert winshell.set_app_id();"
            "p=ctypes.c_wchar_p();ctypes.windll.shell32.GetCurrentProcessExplicitAppUserModelID(ctypes.byref(p));"
            "sys.stdout.write(p.value)")
    out = sp.run([sys.executable, "-c", code], capture_output=True, text=True, cwd=os.path.dirname(os.path.dirname(__file__)))
    assert out.returncode == 0, out.stderr
    assert out.stdout == winshell.APP_ID

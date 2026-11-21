"""Release updates (app/updates.py, `research-brain update`)."""
import json
import os
import sys
from pathlib import Path

import pytest


from tests.test_api import client  # noqa: F401,E402


@pytest.fixture()
def upd(monkeypatch, tmp_path):
    from app import updates
    monkeypatch.setenv("RB_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("RB_METADATA", "1")
    monkeypatch.delenv("RB_UPDATE_CHECK", raising=False)
    monkeypatch.delenv("RB_UPDATE_SOURCE", raising=False)
    (tmp_path / "data").mkdir()
    return updates


def _folder(tmp_path, version="9.0.0"):
    lab = tmp_path / "lab"
    lab.mkdir(exist_ok=True)
    (lab / f"research_brain-{version}-py3-none-any.whl").write_bytes(b"PK")
    (lab / "constraints.txt").write_text("numpy==2.5.3\n", encoding="utf-8")
    (lab / "latest.json").write_text(json.dumps({"version": version, "wheel": f"research_brain-{version}-py3-none-any.whl",
                                                  "notes": "- Better citations"}), encoding="utf-8")
    return lab


def test_version_comparison():
    from app.updates import newer, parse
    assert parse("v1.2.3") == (1, 2, 3) and parse("0.10.0") == (0, 10, 0) and parse("nonsense") is None
    assert newer("0.10.0", "0.9.9") and not newer("0.7.0", "0.7.0") and not newer("0.6.9", "0.7.0")
    assert not newer("garbage", "0.7.0")


def test_lab_folder_update_source(upd, tmp_path):
    lab = _folder(tmp_path)
    assert upd.set_source(str(lab)) == str(lab.resolve())
    with pytest.raises(ValueError):
        upd.set_source(str(tmp_path / "nowhere"))
    info = upd.check(force=True)
    assert info["newer"] and info["latest"] == "9.0.0" and "Better citations" in info["notes"]
    # the next check within a day is answered from the cache
    (lab / "latest.json").write_text(json.dumps({"version": "9.9.9", "wheel": "x.whl"}), encoding="utf-8")
    assert upd.check()["latest"] == "9.0.0" and upd.check(force=True)["latest"] == "9.9.9"


class _Resp:
    def __init__(self, status, data=None, body=b""):
        self.status_code, self._data, self._body = status, data, body

    def json(self):
        return self._data

    def iter_content(self, n):
        yield self._body

    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_checks_are_off_offline_and_quiet_without_a_source(upd, monkeypatch):
    monkeypatch.setattr(upd.requests, "get", lambda *a, **k: _Resp(404))  # no release reachable
    monkeypatch.setattr(upd, "_github_token", lambda: "")  # and no token for the API: nothing more to ask
    out = upd.check(force=True)
    assert out["latest"] is None and not out["newer"] and out["source"] == "github"
    monkeypatch.setenv("RB_UPDATE_CHECK", "0")
    assert upd.check(force=True)["enabled"] is False


def test_install_command_uses_the_installer_that_installed_it(upd, tmp_path):
    lab = _folder(tmp_path)
    upd.set_source(str(lab))
    info = upd.fetch()
    cmd = upd.install_command(info, "uv")
    assert cmd[:6] == ["uv", "tool", "install", "--force", "--python", "3.12"]
    assert cmd[-1].endswith("research_brain-9.0.0-py3-none-any.whl") and str(lab / "constraints.txt") in cmd
    pip = upd.install_command(info, "venv")
    assert pip[:4] == [sys.executable, "-m", "pip", "install"] and pip[-1].endswith(".whl")
    assert upd.install_command(info, "checkout") is None
    # no wheel known (a GitHub release seen through the API): install the tag from git
    assert upd.install_spec({"version": "9.0.0", "tag": "v9.0.0"}).endswith("research-brain.git@v9.0.0")


def test_this_checkout_is_detected():
    from app.updates import how_installed
    assert how_installed() == "checkout"


def test_cli_update_check_and_source(upd, tmp_path, capsys):
    from app.cli import main
    lab = _folder(tmp_path)
    assert main(["update", "--source", str(lab)]) == 0
    assert "Update source:" in capsys.readouterr().out
    assert main(["update", "--check"]) == 0
    assert "Latest: 9.0.0" in capsys.readouterr().out
    assert main(["update"]) == 0  # from a checkout it says to git pull, and installs nothing
    assert "git pull" in capsys.readouterr().out




def test_the_public_github_release_needs_no_account(upd, monkeypatch):
    asked = []

    def get(url, **k):
        asked.append(url)
        return _Resp(200, {"version": "9.1.0", "wheel": "research_brain-9.1.0-py3-none-any.whl", "notes": "- 9.1.0\n- Faster"})
    monkeypatch.setattr(upd.requests, "get", get)
    monkeypatch.setattr(upd, "_github_token", lambda: pytest.fail("no token needed"))
    info = upd.fetch()
    assert asked == [upd.LATEST_URL]
    # files of that exact release, not "latest" (which could move on between the check and the install)
    assert info["wheel"] == "https://github.com/henrykanaskie/research-brain/releases/download/v9.1.0/research_brain-9.1.0-py3-none-any.whl"
    assert info["constraints"].endswith("/v9.1.0/constraints.txt") and info["url"].endswith("/tag/v9.1.0")
    assert upd.check(force=True)["newer"]


def test_prepare_downloads_into_the_update_folder(upd, tmp_path, monkeypatch):
    bodies = {"w": b"PK\x03\x04wheel", "c": b"numpy==2.5.3\n"}
    monkeypatch.setattr(upd.requests, "get", lambda url, **k: _Resp(200, body=bodies["c" if url.endswith(".txt") else "w"]))
    info = upd.prepare({"version": "9.1.0", "wheel": upd.release_file("9.1.0", "research_brain-9.1.0-py3-none-any.whl"),
                        "constraints": upd.release_file("9.1.0", "constraints.txt"), "source": "github"})
    d = upd.update_dir()
    assert info["wheel"] == str(d / "research_brain-9.1.0-py3-none-any.whl") and info["source"] == str(d)
    assert (d / "constraints.txt").read_text() == "numpy==2.5.3\n"
    assert str(d / "constraints.txt") in upd.install_command(info, "uv")  # the release's pins are used
    bodies["w"] = b"<html>not a wheel"
    with pytest.raises(upd.UpdateError, match="damaged"):
        upd.prepare({**info, "wheel": upd.release_file("9.1.0", "research_brain-9.1.0-py3-none-any.whl")})
    with pytest.raises(upd.UpdateError, match="Unexpected"):
        upd.prepare({**info, "wheel": "https://example.org/../../evil.sh"})


def test_folder_release_is_copied_with_its_pins(upd, tmp_path):
    lab = _folder(tmp_path)
    upd.set_source(str(lab))
    info = upd.prepare(upd.fetch())
    assert Path(info["wheel"]).parent == upd.update_dir() and (upd.update_dir() / "constraints.txt").is_file()


def test_the_update_script_waits_installs_and_starts_research_brain_again(upd, tmp_path):
    """Run the real script (sh): it waits for a process to end, runs the install, records how it went, starts
    the 'app' again (a tiny web server answering /api/heartbeat) and clears the marker once it answers."""
    if sys.platform == "win32":
        pytest.skip("the .cmd variant is checked by text below")
    import socket
    import subprocess
    import time
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    web = tmp_path / "web"
    (web / "api").mkdir(parents=True)
    (web / "api" / "heartbeat").write_text("{}")
    slow = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(1.5)"])
    done = tmp_path / "installed"
    install = [sys.executable, "-c", f"open({str(done)!r}, 'w').write('ok'); print('installing')"]
    relaunch = [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(web)]
    d = upd.update_dir()
    d.mkdir(parents=True)
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1.0.0", "to": "9.0.0", "started": "2999-01-01T00:00:00+00:00"})
    sh = d / "run-update.sh"
    sh.write_text(upd.script(install, pids=[slow.pid], port=port, relaunch=relaunch, cwd=str(tmp_path), windows=False))
    t0 = time.monotonic()
    proc = subprocess.Popen(["/bin/sh", str(sh)])
    try:
        time.sleep(0.6)
        assert not done.exists()  # still waiting for research brain to stop
        assert proc.wait(timeout=60) == 0
        assert done.read_text() == "ok" and time.monotonic() - t0 >= 1.2
        assert (d / "exit-code").read_text().strip() == "0" and "installing" in (d / "update.log").read_text()
        assert not (d / "updating.json").exists()  # cleared once the new one answered
    finally:
        slow.kill()
        subprocess.run(["pkill", "-f", f"http.server {port}"], check=False)


def test_windows_script_waits_for_each_process(upd):
    text = upd.script(["C:\\uv.exe", "tool", "install", "x.whl"], pids=[11, 22], port=8765, windows=True,
                      relaunch=["C:\\py\\python.exe", "-m", "research_brain.cli", "serve", "--no-open"], cwd="C:\\app")
    assert 'tasklist /FI "PID eq 11"' in text and 'tasklist /FI "PID eq 22"' in text
    assert "echo %ERRORLEVEL%" in text and 'start "" /B' in text and text.endswith("\r\n")
    assert "timeout /t" not in text  # timeout fails without a console window


def test_windows_script_reopens_the_window_after_the_update(upd):
    reopen = ["C:\\py\\pythonw.exe", "-m", "research_brain.cli", "open", "--port", "8765"]
    kw = dict(port=8765, windows=True, relaunch=["C:\\py\\python.exe", "-m", "research_brain.cli", "serve"], cwd="C:\\app")
    install = ["C:\\uv.exe", "tool", "install", "x.whl"]
    text = upd.script(install, pids=[11, 33], reopen=reopen, **kw)
    lines = text.split("\r\n")
    marker = next(i for i, l in enumerate(lines) if l.startswith("del /q "))
    assert 'tasklist /FI "PID eq 33"' in text
    assert lines[marker + 1].startswith('start "" /B ') and "research_brain.cli open --port 8765" in lines[marker + 1]
    assert "cli open" not in upd.script(install, pids=[11], **kw)  # no window: the same script as before


def test_window_pid_only_for_a_running_window(upd):
    f = upd.paths.user_data_dir() / "window.json"
    assert upd.window_pid() is None  # no window open
    f.write_text("not json", encoding="utf-8")
    assert upd.window_pid() is None
    import subprocess
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    f.write_text(json.dumps({"pid": p.pid, "port": 8765}), encoding="utf-8")
    assert upd.window_pid() is None  # it has ended
    live = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(30)"])
    try:
        f.write_text(json.dumps({"pid": live.pid, "port": 8765}), encoding="utf-8")
        assert upd.window_pid() == live.pid
    finally:
        live.kill()
        live.wait()


def test_start_install_waits_for_the_window_and_reopens_it(upd, monkeypatch):
    monkeypatch.setattr(upd, "how_installed", lambda: "venv")
    monkeypatch.setattr(upd.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(upd, "window_pid", lambda: 4321)
    monkeypatch.setattr(upd, "_windows", lambda: True)
    d = upd.update_dir()
    d.mkdir(parents=True)
    info = {"version": "9.0.0", "wheel": str(d / "w.whl"), "source": str(d)}
    upd.start_install(info, port=8765, pids=[123])
    text = (d / "run-update.cmd").read_text(encoding="utf-8")
    assert 'tasklist /FI "PID eq 4321"' in text and "cli open --port 8765" in text


def test_start_install_marks_it_running_and_reports_once(upd, monkeypatch):
    started = []
    monkeypatch.setattr(upd, "how_installed", lambda: "venv")
    monkeypatch.setattr(upd.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    d = upd.update_dir()
    d.mkdir(parents=True)
    (d / "research_brain-9.0.0-py3-none-any.whl").write_bytes(b"PK")
    info = {"version": "9.0.0", "wheel": str(d / "research_brain-9.0.0-py3-none-any.whl"), "source": str(d)}
    upd.start_install(info, port=8765, pids=[123])
    assert started and upd.in_progress()["to"] == "9.0.0" and upd.last_result() is None  # still running
    if os.name == "nt":
        assert 'tasklist /FI "PID eq 123"' in (d / "run-update.cmd").read_text()
    else:
        assert "kill -0 123" in (d / "run-update.sh").read_text()
    # research brain waits for it instead of starting the half-replaced copy
    assert upd.wait_while_updating(limit=0.6) is True
    (d / "updating.json").unlink()
    (d / "exit-code").write_text("0\n")
    r = upd.last_result()
    assert r["ok"] is False and r["to"] == "9.0.0" and "error" in r  # this is still the old version
    monkeypatch.setattr(upd, "__version__", "9.0.0")
    assert upd.last_result()["ok"] is True
    upd.acknowledge()
    assert upd.last_result() is None


def test_a_dead_update_doesnt_block_starting(upd):
    d = upd.update_dir()
    d.mkdir(parents=True)
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1", "to": "2", "started": "2001-01-01T00:00:00+00:00"})
    assert upd.in_progress() is None and upd.wait_while_updating(limit=5) is False


def test_update_api_from_a_checkout(client):
    r = client.get("/api/update")
    assert r.status_code == 200
    d = r.json()
    assert d["current"] and d["can_install"] is False and "git pull" in d["reason"] and d["system"]
    assert client.post("/api/update/install", json={}).status_code == 409
    assert client.post("/api/update/ack", json={}).json() == {"ok": True}


@pytest.mark.skipif(os.name == "nt", reason="runs the sh script")
def test_uv_install_works_from_a_folder_with_a_space(upd, tmp_path):
    """uv 0.12 splits a -c path at spaces ("File not found: .../Library/Application"), and the Mac data folder
    is ~/Library/Application Support: the update names constraints.txt relatively and runs from its folder."""
    import subprocess
    lab = tmp_path / "Shared Drive" / "lab"
    lab.mkdir(parents=True)
    (lab / "research_brain-9.0.0-py3-none-any.whl").write_bytes(b"PK")
    (lab / "constraints.txt").write_text("numpy==2.5.3\n", encoding="utf-8")
    info = {"version": "9.0.0", "wheel": str(lab / "research_brain-9.0.0-py3-none-any.whl"), "source": str(lab)}
    cmd = upd.install_command(info, "uv")
    assert not any(" " in c for c in cmd[cmd.index("-c") + 1:cmd.index("-c") + 2])
    assert upd.install_cwd(info) == str(lab)
    # a stand-in uv that, like uv 0.12, fails when the -c path has a space or doesn't exist from where it runs
    fake = tmp_path / "uv"
    fake.write_text('#!/bin/sh\nwhile [ $# -gt 0 ]; do if [ "$1" = -c ]; then shift; case "$1" in *" "*) '
                    'echo "error: File not found"; exit 2;; esac; [ -f "$1" ] || { echo "error: File not found"; exit 2; }; '
                    'fi; shift; done; echo installed\n')
    fake.chmod(0o755)
    upd.update_dir().mkdir(parents=True)
    sh = tmp_path / "run.sh"
    sh.write_text(upd.script([str(fake), *cmd[1:]], pids=[], port=1, relaunch=["true"], cwd=str(tmp_path),
                             windows=False, install_dir=upd.install_cwd(info)))
    (tmp_path / "curl").write_text("#!/bin/sh\nexit 0\n")  # research brain "answers" at once
    (tmp_path / "curl").chmod(0o755)
    env = {**os.environ, "PATH": f"{tmp_path}:{os.environ['PATH']}"}
    subprocess.run(["/bin/sh", str(sh)], timeout=30, check=True, env=env)
    assert (upd.update_dir() / "exit-code").read_text().strip() == "0"
    assert "installed" in (upd.update_dir() / "update.log").read_text()
    # no space: the full path, run from anywhere
    plain = _folder(tmp_path)
    upd.set_source(str(plain))
    assert upd.install_cwd(upd.fetch()) is None


def test_pipx_quotes_a_constraints_path_with_a_space(upd, tmp_path):
    import shlex
    lab = tmp_path / "Shared Drive"
    lab.mkdir()
    (lab / "constraints.txt").write_text("numpy==2.5.3\n", encoding="utf-8")
    info = {"version": "9.0.0", "wheel": str(lab / "research_brain-9.0.0-py3-none-any.whl"), "source": str(lab)}
    cmd = upd.install_command(info, "pipx")
    if sys.version_info >= (3, 12):
        args = cmd[cmd.index("--pip-args") + 1]
        assert shlex.split(args) == ["-c", str(lab / "constraints.txt")]  # how pipx splits --pip-args

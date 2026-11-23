"""Release updates (app/updates.py, `research-brain update`)."""
import json
import os
import sys
from pathlib import Path

import pytest

from test_api import client  # noqa: F401  (fixture)


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


def test_version_comparison_and_how_this_copy_was_installed():
    """Versions parse with or without a v and compare numerically (garbage is never newer); this copy is a checkout."""
    from app.updates import how_installed, newer, parse
    assert parse("v1.2.3") == (1, 2, 3) and parse("0.10.0") == (0, 10, 0) and parse("nonsense") is None
    assert newer("0.10.0", "0.9.9") and not newer("0.7.0", "0.7.0") and not newer("0.6.9", "0.7.0")
    assert not newer("garbage", "0.7.0")
    assert how_installed() == "checkout"


def test_a_lab_folder_release_is_checked_cached_installed_and_copied_with_its_pins(upd, tmp_path):
    """A lab folder as the update source: a missing folder is refused; the check finds the newer release and its
    notes and is kept for a while; the install command fits the installer (uv, venv, none from a checkout, a git tag
    without a wheel); and preparing copies the wheel and its constraints into the update folder."""
    lab = _folder(tmp_path)
    assert upd.set_source(str(lab)) == str(lab.resolve())
    with pytest.raises(ValueError):
        upd.set_source(str(tmp_path / "nowhere"))
    info = upd.check(force=True)
    assert info["newer"] and info["latest"] == "9.0.0" and "Better citations" in info["notes"]
    # the next check soon after is answered from what's kept
    (lab / "latest.json").write_text(json.dumps({"version": "9.9.9", "wheel": "x.whl"}), encoding="utf-8")
    assert upd.check()["latest"] == "9.0.0" and upd.check(force=True)["latest"] == "9.9.9"
    _folder(tmp_path)  # back to 9.0.0
    info = upd.fetch()
    cmd = upd.install_command(info, "uv")
    assert cmd[:6] == ["uv", "tool", "install", "--force", "--python", upd.uv_python()]
    # an Arm Windows PC gets the Intel/AMD Python, on an update as when it was installed
    for windows, arch, want in ((True, "ARM64", "cpython-3.12-windows-x86_64-none"), (True, "AMD64", "3.12"), (False, "ARM64", "3.12")):
        with pytest.MonkeyPatch.context() as m:
            m.setattr(upd, "_windows", lambda w=windows: w)
            m.setattr(upd, "_machine_arch", lambda a=arch: a)
            assert upd.uv_python() == want
    assert cmd[-1].endswith("research_brain-9.0.0-py3-none-any.whl") and str(lab / "constraints.txt") in cmd
    pip = upd.install_command(info, "venv")
    assert pip[:4] == [sys.executable, "-m", "pip", "install"] and pip[-1].endswith(".whl")
    assert upd.install_command(info, "checkout") is None
    # no wheel known (a GitHub release seen through the API): install the tag from git
    assert upd.install_spec({"version": "9.0.0", "tag": "v9.0.0"}).endswith("research-brain.git@v9.0.0")
    info = upd.prepare(upd.fetch())
    assert Path(info["wheel"]).parent == upd.update_dir() and (upd.update_dir() / "constraints.txt").is_file()


def test_it_checks_when_opened_and_hourly_and_a_failed_check_is_soon_tried_again(upd, tmp_path, monkeypatch):
    """Opening asks again after a minute, staying open after about an hour; a failed check keeps the release already
    heard of (and says it failed), is tried again in minutes, and a failed check kept by an older version for a day
    (or a time in the future) holds nothing up. Two checks at once ask once."""
    import datetime as dt
    import threading
    asked, answer = [], [{"version": "9.0.0"}]
    monkeypatch.setattr(upd, "fetch", lambda: asked.append(1) or answer[0])

    def age(**kw):
        k = json.loads(upd._cache_path().read_text(encoding="utf-8"))
        for f in ("checked", "tried"):
            if k.get(f):
                k[f] = (dt.datetime.now(dt.timezone.utc) - dt.timedelta(**kw)).isoformat()
        upd._cache_path().write_text(json.dumps(k), encoding="utf-8")

    def n(**kw):
        before = len(asked)
        out = upd.check(**kw)
        return len(asked) - before, out
    assert n()[0] == 1 and n()[0] == 0 and n(opened=True)[0] == 0  # reloading the page at once asks nothing
    age(minutes=2)
    assert n()[0] == 0 and n(opened=True)[0] == 1
    age(minutes=55)
    assert n()[0] == 1
    answer[0] = None  # no internet: the release already heard of is still offered
    age(minutes=2)
    asked_, out = n(opened=True)
    assert asked_ == 1 and out["failed"] and out["newer"] and out["latest"] == "9.0.0" and out["checked"]
    assert n()[0] == 0
    age(minutes=3)
    answer[0] = {"version": "9.0.1"}
    asked_, out = n()
    assert asked_ == 1 and out["latest"] == "9.0.1" and not out["failed"]
    age(minutes=-600)  # the clock was wrong
    assert n()[0] == 1
    for info in (None, {"version": "9.0.0"}):  # kept by a version before this: a failure an hour ago, a success a day ago
        when = dt.datetime.now(dt.timezone.utc) - (dt.timedelta(hours=1) if info is None else dt.timedelta(hours=23))
        upd._cache_path().write_text(json.dumps({"checked": when.isoformat(), "source": "github", "info": info}), encoding="utf-8")
        asked_, out = n()
        assert asked_ == 1 and out["latest"] == "9.0.1"
    gate, inside = threading.Event(), threading.Event()
    monkeypatch.setattr(upd, "fetch", lambda: (asked.append(1), inside.set(), gate.wait(10), {"version": "9.0.2"})[-1])
    age(minutes=55)
    before = len(asked)
    t = threading.Thread(target=upd.check)
    t.start()
    assert inside.wait(5) and upd.check()["latest"] == "9.0.1"  # not kept waiting
    gate.set()
    t.join(10)
    assert len(asked) == before + 1 and upd.check()["latest"] == "9.0.2"


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


def test_cli_update_check_and_source(upd, tmp_path, capsys):
    from app.cli import main
    lab = _folder(tmp_path)
    assert main(["update", "--source", str(lab)]) == 0
    assert "Update source:" in capsys.readouterr().out
    assert main(["update", "--check"]) == 0
    assert "Latest: 9.0.0" in capsys.readouterr().out
    assert main(["update"]) == 0  # from a checkout it says to git pull, and installs nothing
    assert "git pull" in capsys.readouterr().out


def test_github_release_checks_need_no_account_and_are_quiet_offline_or_off(upd, monkeypatch):
    """With no release reachable and no token the check is quiet; the public GitHub release needs no account and
    names the files of that exact release; RB_UPDATE_CHECK=0 turns checks off."""
    monkeypatch.setattr(upd.requests, "get", lambda *a, **k: _Resp(404))  # no release reachable
    monkeypatch.setattr(upd, "_github_token", lambda: "")  # and no token for the API: nothing more to ask
    out = upd.check(force=True)
    assert out["latest"] is None and not out["newer"] and out["source"] == "github"

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
    monkeypatch.setenv("RB_UPDATE_CHECK", "0")
    assert upd.check(force=True)["enabled"] is False


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


def _job(tmp_path, **kw):
    """A job for app/update_run.py whose 'research brain' is a tiny web server answering /api/heartbeat."""
    import socket
    with socket.socket() as k:
        k.bind(("127.0.0.1", 0))
        port = k.getsockname()[1]
    web = tmp_path / "web"
    (web / "api").mkdir(parents=True, exist_ok=True)
    (web / "api" / "heartbeat").write_text("{}")
    d = tmp_path / "update"
    d.mkdir(exist_ok=True)
    (d / "updating.json").write_text("{}")
    job = {"from": "1.0.0", "to": "9.0.0", "pids": [], "port": port, "cwd": str(tmp_path), "folder": "", "home": str(tmp_path),
           "marker": str(d / "updating.json"), "applog": str(tmp_path / "app.log"), "open": None, "previous": None,
           "install": [sys.executable, "-c", f"open({str(tmp_path / 'installed')!r}, 'a').write('new '); print('installing')"],
           "check": [sys.executable, "-c", "pass"],
           "serve": [sys.executable, "-m", "http.server", str(port), "--bind", "127.0.0.1", "--directory", str(web)], **kw}
    (d / "job.json").write_text(json.dumps(job))
    return d, job


def _run_job(d, limit=90):
    import subprocess
    from pathlib import Path
    import app.update_run as ur
    return subprocess.run([sys.executable, "-I", "-S", str(Path(ur.__file__)), str(d / "job.json")], timeout=limit)


def _stop(job):
    import subprocess
    import urllib.request
    if os.name == "nt":
        subprocess.run(["powershell", "-NoProfile", "-Command", f"Get-NetTCPConnection -LocalPort {job['port']} -State Listen -ErrorAction SilentlyContinue | % {{ Stop-Process -Id $_.OwningProcess -Force }}"], check=False)
    else:
        subprocess.run(["pkill", "-f", f"http.server {job['port']}"], check=False)


def test_the_updater_waits_installs_starts_and_clears_the_marker(tmp_path):
    """app/update_run.py (it runs anywhere): waits for research brain to end, installs, says how it went, starts it (its
    window when that was open) and clears the marker."""
    import subprocess
    import time
    slow = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(2)"])
    opened = tmp_path / "opened"
    d, job = _job(tmp_path, pids=[slow.pid])
    try:
        import threading
        threading.Thread(target=slow.wait, daemon=True).start()  # collected when it ends, as Windows does by itself
        t0 = time.monotonic()
        _run_job(d)
        assert time.monotonic() - t0 > 1.5 and (tmp_path / "installed").read_text() == "new "
        assert (d / "exit-code").read_text().strip() == "0" and not (d / "updating.json").exists()
        assert "installing" in (d / "update.log").read_text() and not opened.exists()
    finally:
        slow.kill()
        _stop(job)


def test_the_updater_ends_what_will_not_stop_and_puts_the_old_version_back(tmp_path):
    """A process that won't end doesn't hold the update up for ever; a new version that doesn't run, or an install that
    fails and leaves nothing that runs, is replaced by the old one; research brain is started and the marker cleared
    whatever happens."""
    import subprocess
    import app.update_run as ur
    stuck = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(300)"])
    said = []
    try:
        ur.stop_all({"pids": [stuck.pid], "folder": ""}, said.append, patience=1)
        assert stuck.wait(10) != 0 and any("ending" in x for x in said)
    finally:
        stuck.kill()
    ok = tmp_path / "works"
    back = [sys.executable, "-c", f"open({str(tmp_path / 'installed')!r}, 'a').write('old '); open({str(ok)!r}, 'w')"]
    check = [sys.executable, "-c", f"import os, sys; sys.exit(0 if os.path.exists({str(ok)!r}) else 1)"]
    d, job = _job(tmp_path, previous=[sys.executable, "-c", "import sys; sys.exit(1)"], previous_unpinned=back)
    with open(tmp_path / "log", "w", encoding="utf-8") as log:
        ur.put_back(job, log, said.append)
    assert ok.exists()  # the pinned versions couldn't be had: without them
    for install, want in ((None, "97"), ([sys.executable, "-c", "import sys; sys.exit(2)"], "2"), (["no-such-installer-rb"], "95")):
        for f in (ok, tmp_path / "installed"):
            f.unlink(missing_ok=True)
        d, job = _job(tmp_path, previous=back, check=check, **({"install": install} if install else {}))
        try:
            _run_job(d)
            assert (d / "exit-code").read_text().strip() == want and (tmp_path / "installed").read_text().endswith("old ")
            assert not (d / "updating.json").exists() and ur.answers(job, 10)
        finally:
            _stop(job)


@pytest.mark.skipif(os.name == "nt", reason="on Windows the window is opened again instead (tests/windows_update.py)")
def test_on_a_mac_a_version_that_stops_is_replaced_at_once_while_the_window_waits(tmp_path):
    """A new version whose server stops (or hangs) is ended and the old one put back; the marker the Mac's window waits
    for goes only once something answers; a server that ended but wasn't collected by the window counts as ended."""
    import subprocess
    import time
    import app.update_run as ur
    d, job = _job(tmp_path)
    good = tmp_path / "works"
    marker = d / "updating.json"
    serve = ("import os, sys\nif not os.path.exists(%r): open(%r, 'a').write(str(os.path.exists(%r))); sys.exit(1)\n"
             "os.execv(sys.executable, %r)") % (str(good), str(tmp_path / "waiting"), str(marker), job["serve"])
    ended = subprocess.Popen([sys.executable, "-c", "pass"])  # never collected
    d, job = _job(tmp_path, pids=[ended.pid], serve=[sys.executable, "-c", serve],
                  previous=[sys.executable, "-c", f"open({str(good)!r}, 'w')"], port=job["port"])
    try:
        t0 = time.monotonic()
        _run_job(d)
        assert time.monotonic() - t0 < 90  # not the two minutes one that hangs is given (a Mac on CI takes half a minute over the tiny web server)
        assert (d / "exit-code").read_text().strip() == "98" and not marker.exists() and ur.answers(job, 5)
        assert (tmp_path / "waiting").read_text() == "True"
    finally:
        ended.wait()
        _stop(job)


def test_an_update_that_died_blocks_nothing(upd, monkeypatch):
    """The marker names the installer's process: once that has ended (the computer was switched off, antivirus stopped
    it), research brain opens at once. A marker from an older version, without one, counts only while its script runs."""
    import subprocess
    d = upd.update_dir()
    d.mkdir(parents=True)
    now = __import__("datetime").datetime.now(__import__("datetime").timezone.utc).isoformat(timespec="seconds")
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1", "to": "2", "started": now, "pid": p.pid, "starting": True})
    assert upd.in_progress() is None
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1", "to": "2", "started": now})
    monkeypatch.setattr(upd, "_old_script_running", lambda: False)
    assert upd.in_progress() is None
    monkeypatch.setattr(upd, "_old_script_running", lambda: True)
    assert upd.in_progress()["to"] == "2"
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1", "to": "2", "started": now, "pid": None, "starting": True})
    assert upd.in_progress()["to"] == "2"  # its installer is being started this moment


def test_a_version_that_cannot_be_installed_here_is_refused_before_anything_stops(upd, monkeypatch, tmp_path):
    import types
    upd.update_dir().mkdir(parents=True)
    monkeypatch.setattr(upd, "find_program", lambda n: "uv")
    info = {"version": "9.0.0", "wheel": "w.whl", "source": str(tmp_path)}
    said = {"code": 0, "out": "research-brain 9.0.0", "err": ""}
    monkeypatch.setattr(upd.subprocess, "run", lambda *a, **k: types.SimpleNamespace(returncode=said["code"], stdout=said["out"], stderr=said["err"]))
    upd.try_out(info, "uv")
    upd.try_out(info, "pipx")  # only uv can
    for err, words in (("error: Failed to build `x`\nlast line", "can't be installed on this computer"), ("error sending request for url", "internet")):
        said.update(code=1, out="", err=err)
        with pytest.raises(upd.UpdateError, match=words):
            upd.try_out(info, "uv")
    said.update(code=2, out="", err="error: unexpected argument '--isolated'")  # an old uv: unclear, so go ahead
    upd.try_out(info, "uv")


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


def test_start_install_marks_it_running_reports_once_and_reopens_the_window(upd, monkeypatch):
    """A dead update (an old marker) never blocks starting; start_install marks the update running so research
    brain waits for it, reports the result once (failed while the old version still runs), and on Windows waits
    for an open window and reopens it."""
    d = upd.update_dir()
    d.mkdir(parents=True)
    upd.paths.write_json_atomic(d / "updating.json", {"from": "1", "to": "2", "started": "2001-01-01T00:00:00+00:00"})
    assert upd.in_progress() is None and upd.wait_while_updating(limit=5) is False
    started = []
    monkeypatch.setattr(upd, "how_installed", lambda: "venv")
    monkeypatch.setattr(upd.subprocess, "Popen", lambda *a, **k: started.append((a, k)))
    (d / "research_brain-9.0.0-py3-none-any.whl").write_bytes(b"PK")
    info = {"version": "9.0.0", "wheel": str(d / "research_brain-9.0.0-py3-none-any.whl"), "source": str(d)}
    upd.start_install(info, port=8765, pids=[123])
    assert started and upd.in_progress()["to"] == "9.0.0" and upd.last_result() is None  # still running
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert job["pids"] == [123]
    # putting this version back installs exactly what it has now
    pins = (d / "previous" / "constraints.txt").read_text(encoding="utf-8").splitlines()
    assert f"numpy=={__import__('numpy').__version__}" in pins and not any(x.startswith("research") for x in pins)
    assert job["previous_cwd"] == str(d / "previous") and "-c" not in job["previous_unpinned"]
    assert ("-c" in job["previous"]) == (sys.version_info >= (3, 12))
    upd.paths.write_json_atomic(d / "updating.json", {**json.loads((d / "updating.json").read_text()), "pid": None})
    # research brain waits for it instead of starting the half-replaced copy
    assert upd.wait_while_updating(limit=0.6) is True
    (d / "updating.json").unlink()
    (d / "exit-code").write_text("0\n")
    r = upd.last_result()
    assert r["ok"] is False and r["to"] == "9.0.0" and "error" in r  # this is still the old version
    old_version = upd.__version__
    monkeypatch.setattr(upd, "__version__", "9.0.0")
    assert upd.last_result()["ok"] is True
    upd.acknowledge()
    assert upd.last_result() is None
    monkeypatch.setattr(upd, "__version__", old_version)

    # on Windows, with the window open: the script waits for it and reopens it
    monkeypatch.setattr(upd.subprocess, "Popen", lambda *a, **k: None)
    monkeypatch.setattr(upd, "window_pid", lambda: 4321)
    monkeypatch.setattr(upd, "_windows", lambda: True)
    info = {"version": "9.0.0", "wheel": str(d / "w.whl"), "source": str(d)}
    upd.start_install(info, port=8765, pids=[123])
    job = json.loads((d / "job.json").read_text(encoding="utf-8"))
    assert 4321 in job["pids"] and job["open"][-4:] == ["app.cli", "open", "--port", "8765"] and (d / "update_run.py").is_file()


def test_update_api_from_a_checkout(client):
    r = client.get("/api/update")
    assert r.status_code == 200
    d = r.json()
    assert d["current"] and d["can_install"] is False and "git pull" in d["reason"] and d["system"]
    assert client.post("/api/update/install", json={}).status_code == 409
    assert client.post("/api/update/ack", json={}).json() == {"ok": True}


@pytest.mark.skipif(os.name == "nt", reason="a stand-in uv in sh")
def test_uv_install_works_from_a_folder_with_a_space(upd, tmp_path):
    """uv 0.12 splits a -c path at spaces ("File not found: .../Library/Application"), and the Mac data folder
    is ~/Library/Application Support: the update names constraints.txt relatively and runs from its folder."""
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
    d, job = _job(tmp_path, install=[str(fake), *cmd[1:]], cwd=upd.install_cwd(info))
    try:
        _run_job(d)
        assert (d / "exit-code").read_text().strip() == "0" and "installed" in (d / "update.log").read_text()
    finally:
        _stop(job)
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

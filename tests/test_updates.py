"""Release updates (app/updates.py, `research-brain update`, /api/update)."""
import json
import sys

import pytest


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


def test_checks_are_off_offline_and_quiet_without_a_source(upd, monkeypatch):
    monkeypatch.setattr(upd, "_github_token", lambda: "")  # private repo, no token: nothing to ask
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


def test_update_endpoints(upd, tmp_path):
    from fastapi.testclient import TestClient
    from app import server
    c = TestClient(server.app)
    lab = _folder(tmp_path, "9.1.0")
    out = c.put("/api/update/source", json={"source": str(lab)}).json()
    assert out["newer"] and out["latest"] == "9.1.0" and out["command"] == "research-brain update"
    assert c.get("/api/update").json()["latest"] == "9.1.0"
    assert c.put("/api/update/source", json={"source": str(tmp_path / "missing")}).status_code == 400
    assert c.put("/api/update/source", json={"source": ""}).json()["source"] == "github"

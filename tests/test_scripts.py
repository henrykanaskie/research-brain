"""Shell scripts: vault_backup.sh logic (run for real on a temp vault), install_launchd.sh in dry-run
mode, run.sh/launchd syntax, and the plist templates parse as XML property lists."""
import os
import plistlib
import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
SCRIPTS = ROOT / "scripts"
BACKUP = SCRIPTS / "vault_backup.sh"
INSTALL = SCRIPTS / "install_launchd.sh"
PLISTS = sorted((SCRIPTS / "launchd").glob("*.plist"))

needs_git = pytest.mark.skipif(not shutil.which("git"), reason="git is not installed")


def _env(tmp_path, **extra):
    # A clean HOME with no global git identity, so the script's own identity is what gets used.
    home = tmp_path / "home"
    home.mkdir(exist_ok=True)
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    env.update(HOME=str(home), GIT_CONFIG_NOSYSTEM="1", **extra)
    env.pop("RB_VAULT", None)
    return env


def _git(vault, *args):
    return subprocess.run(["git", "-C", str(vault), *args], capture_output=True, text=True, check=True).stdout


def _backup(vault, env):
    return subprocess.run(["bash", str(BACKUP), str(vault)], capture_output=True, text=True, env=env)


@pytest.mark.parametrize("script", [BACKUP, INSTALL, ROOT / "run.sh"], ids=lambda p: p.name)
def test_bash_syntax(script):
    r = subprocess.run(["bash", "-n", str(script)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr


@needs_git
def test_backup_inits_ignores_and_commits(tmp_path):
    vault = tmp_path / "vault"
    for d in ("notes", "pdfs", "text", ".index", ".trash", "exports/2026"):
        (vault / d).mkdir(parents=True)
    (vault / "notes" / "a.md").write_text("---\ntitle: A\n---\nbody\n")
    (vault / "text" / "a.txt").write_text("full text")
    (vault / "pdfs" / "a.pdf").write_bytes(b"%PDF-1.4")
    (vault / ".index" / "emb.npy").write_bytes(b"x")
    (vault / ".trash" / "old.md").write_text("gone")
    (vault / "exports" / "2026" / "index.html").write_text("<html>")
    env = _env(tmp_path)

    r = _backup(vault, env)
    assert r.returncode == 0, r.stderr
    assert (vault / ".git").is_dir()
    ignore = (vault / ".gitignore").read_text().splitlines()
    for pat in ("pdfs/", ".index/", ".trash/", "exports/"):
        assert pat in ignore
    files = set(_git(vault, "ls-files").split())
    assert files == {".gitignore", "notes/a.md", "text/a.txt"}
    log = _git(vault, "log", "--format=%an <%ae>|%cn <%ce>|%s").strip().splitlines()
    assert len(log) == 1
    author, committer, subject = log[0].split("|")
    assert author == committer == "research_brain <research_brain@localhost>"
    assert subject.startswith("vault snapshot 20")
    full = _git(vault, "log", "--format=%B%an%ae%cn%ce").lower()
    assert "claude" not in full and "anthropic" not in full and "co-authored" not in full

    # no changes -> no new commit
    r = _backup(vault, env)
    assert r.returncode == 0 and "no changes" in r.stdout
    assert len(_git(vault, "log", "--oneline").splitlines()) == 1

    # edit + delete + new note -> one more commit capturing all of it
    (vault / "notes" / "a.md").write_text("---\ntitle: A2\n---\nbody\n")
    (vault / "text" / "a.txt").unlink()
    (vault / "notes" / "b.md").write_text("---\ntitle: B\n---\n")
    r = _backup(vault, env)
    assert r.returncode == 0, r.stderr
    assert len(_git(vault, "log", "--oneline").splitlines()) == 2
    assert set(_git(vault, "ls-files").split()) == {".gitignore", "notes/a.md", "notes/b.md"}
    assert _git(vault, "status", "--porcelain") == ""


@needs_git
def test_backup_keeps_user_gitignore_and_restores_rules(tmp_path):
    vault = tmp_path / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "a.md").write_text("x")
    env = _env(tmp_path)
    assert _backup(vault, env).returncode == 0
    (vault / ".gitignore").write_text("*.tmp\n")  # user trimmed it
    assert _backup(vault, env).returncode == 0
    ignore = (vault / ".gitignore").read_text().splitlines()
    assert ignore[0] == "*.tmp" and {"pdfs/", ".index/", ".trash/"} <= set(ignore)


@needs_git
def test_backup_vault_inside_another_repo_gets_its_own(tmp_path):
    outer = tmp_path / "outer"
    vault = outer / "vault"
    (vault / "notes").mkdir(parents=True)
    (vault / "notes" / "a.md").write_text("x")
    subprocess.run(["git", "init", "-q", str(outer)], check=True)
    r = _backup(vault, _env(tmp_path))
    assert r.returncode == 0, r.stderr
    assert (vault / ".git").is_dir()
    assert _git(vault, "rev-parse", "--show-toplevel").strip() == str(vault.resolve())


def test_backup_missing_vault_fails(tmp_path):
    r = _backup(tmp_path / "nope", _env(tmp_path))
    assert r.returncode != 0 and "no vault" in r.stderr


@pytest.mark.parametrize("plist", PLISTS, ids=lambda p: p.name)
def test_plist_templates_are_valid(plist):
    assert len(PLISTS) == 2
    data = plistlib.loads(plist.read_bytes())  # XML parse, same format plutil -lint checks
    assert data["Label"] == plist.stem
    assert data["ProgramArguments"][0] == "/bin/bash"
    assert "__HOME__/Library/Logs/" in data["StandardOutPath"]
    if plist.stem.endswith(".server"):
        assert data["RunAtLoad"] is True and data["KeepAlive"] == {"SuccessfulExit": False}
        assert data["ProgramArguments"][1] == "__REPO__/run.sh"
        assert data["StandardOutPath"].endswith("/research_brain.log")
    else:
        assert data["StartCalendarInterval"] == {"Hour": 2, "Minute": 30}
        assert data["ProgramArguments"][1:] == ["__REPO__/scripts/vault_backup.sh", "__VAULT__"]


def test_install_launchd_dry_run(tmp_path):
    home = tmp_path / "H & <me>"  # characters that need XML and sed escaping
    vault = tmp_path / "my|vault"
    env = dict(os.environ, HOME=str(home), RB_VAULT=str(vault), RB_LAUNCHD_DRY_RUN="1")
    r = subprocess.run(["bash", str(INSTALL)], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    agents = home / "Library" / "LaunchAgents"
    server = plistlib.loads((agents / "com.research_brain.server.plist").read_bytes())
    backup = plistlib.loads((agents / "com.research_brain.backup.plist").read_bytes())
    assert server["ProgramArguments"] == ["/bin/bash", f"{ROOT}/run.sh"]
    assert server["WorkingDirectory"] == str(ROOT)
    assert server["StandardOutPath"] == f"{home}/Library/Logs/research_brain.log"
    assert server["EnvironmentVariables"]["RB_NO_OPEN"] == "1"
    # regression: the server agent must serve the same vault the backup agent snapshots
    assert server["EnvironmentVariables"]["RB_VAULT"] == str(vault)
    assert backup["ProgramArguments"] == ["/bin/bash", f"{ROOT}/scripts/vault_backup.sh", str(vault)]
    assert f"launchctl bootstrap gui/{os.getuid()} {agents}/com.research_brain.server.plist" in r.stdout
    assert "__" not in (agents / "com.research_brain.server.plist").read_text()

    r = subprocess.run(["bash", str(INSTALL), "--uninstall"], capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "launchctl bootout" in r.stdout or "Removed" in r.stdout
    assert not list(agents.glob("*.plist"))

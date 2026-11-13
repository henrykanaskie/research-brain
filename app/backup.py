"""`research-brain backup`: snapshot the library into its own local git history, on any OS.

The same thing scripts/vault_backup.sh does, in Python so it also runs on Windows:
first run `git init`s the library folder, and the documents folder when it is elsewhere (your papers,
notes, questions and highlights; app/layout.py), each with a .gitignore that leaves out PDFs and derived
data; every run commits any changes as "library snapshot <date>". Nothing is pushed anywhere. The user's own git identity
and settings are never used or changed.

`research-brain backup --schedule` adds a nightly run with the system's own scheduler:
    Windows  a Task Scheduler task "research brain backup" (schtasks), daily at 02:30
    Linux    a systemd user timer (research-brain-backup.timer), else a crontab line it prints
    macOS    scripts/install_launchd.sh, which also sets up starting at login
`--unschedule` removes it.
"""
from __future__ import annotations

import datetime as dt
import os
import shutil
import subprocess
import sys
from pathlib import Path

IGNORE = ("pdfs/", "PDFs/", ".index/", ".trash/", "exports/", "backups/", "*.tmp")
WHO = ["-c", "user.name=research_brain", "-c", "user.email=research_brain@localhost", "-c", "commit.gpgsign=false",
       "-c", "core.autocrlf=false"]
TASK = "research brain backup"


class BackupError(RuntimeError):
    pass


def _git(vault: Path, *args: str, check: bool = True) -> subprocess.CompletedProcess:
    git = shutil.which("git")
    if not git:
        hint = {"win32": "Install Git for Windows (winget install Git.Git)", "darwin": "Run xcode-select --install"}
        raise BackupError(f"git isn't installed. {hint.get(sys.platform, 'Install git with your package manager')}.")
    env = dict(os.environ, GIT_AUTHOR_NAME="research_brain", GIT_AUTHOR_EMAIL="research_brain@localhost",
               GIT_COMMITTER_NAME="research_brain", GIT_COMMITTER_EMAIL="research_brain@localhost",
               GIT_CONFIG_NOSYSTEM="1")
    r = subprocess.run([git, *WHO, *args], cwd=str(vault), env=env, capture_output=True, text=True,
                       encoding="utf-8", errors="replace")
    if check and r.returncode != 0:
        raise BackupError(f"git {args[0]} failed: {(r.stderr or r.stdout).strip()[:300]}")
    return r


def snapshot_library(vault: Path) -> str:
    """Snapshot the library folder and, when it is elsewhere, its documents folder. One line each."""
    from . import paths
    vault = Path(vault)
    out = [snapshot(vault)]
    docs = paths.documents_dir(vault)
    if docs.is_dir() and docs.resolve() != vault.resolve():
        out.append(snapshot(docs))
    return "\n".join(out)


def snapshot(vault: Path) -> str:
    """Commit the library's changes. Returns a one-line report."""
    vault = Path(vault)
    if not vault.is_dir():
        raise BackupError(f"No library at {vault}")
    if not (vault / ".git").is_dir():  # its own repository, even inside another checkout
        _git(vault, "init", "-q")
    gi = vault / ".gitignore"
    have = gi.read_text(encoding="utf-8").splitlines() if gi.exists() else []
    missing = [p for p in IGNORE if p not in have]
    if missing:
        gi.write_text("\n".join(have + missing).strip() + "\n", encoding="utf-8", newline="\n")
    _git(vault, "add", "-A")
    if _git(vault, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return "No changes since the last snapshot."
    stamp = dt.datetime.now().astimezone().isoformat(timespec="seconds")
    _git(vault, "commit", "-q", "--no-verify", "-m", f"library snapshot {stamp}")
    sha = _git(vault, "rev-parse", "--short", "HEAD").stdout.strip()
    return f"Saved snapshot {sha} of {vault}."


def _command() -> list[str]:
    exe = shutil.which("research-brain")
    return [exe, "backup"] if exe else [sys.executable, "-m", (__package__ or "app") + ".cli", "backup"]


def schedule(enable: bool = True) -> str:
    cmd = _command()
    if sys.platform == "win32":
        if not enable:
            subprocess.run(["schtasks", "/Delete", "/TN", TASK, "/F"], capture_output=True)
            return f"Removed the scheduled task “{TASK}”."
        tr = subprocess.list2cmdline(cmd)
        r = subprocess.run(["schtasks", "/Create", "/TN", TASK, "/TR", tr, "/SC", "DAILY", "/ST", "02:30", "/F"],
                           capture_output=True, text=True)
        if r.returncode != 0:
            raise BackupError(f"Task Scheduler refused: {(r.stderr or r.stdout).strip()}")
        return f"Added the scheduled task “{TASK}”: a snapshot every night at 02:30 (Task Scheduler)."
    if sys.platform == "darwin":
        script = Path(__file__).resolve().parent.parent / "scripts" / "install_launchd.sh"
        return (f"On macOS run {script}{' --uninstall' if not enable else ''}: it sets up the nightly snapshot "
                "(and starting at login).")
    units = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config") / "systemd" / "user"
    if shutil.which("systemctl"):
        if not enable:
            subprocess.run(["systemctl", "--user", "disable", "--now", "research-brain-backup.timer"], capture_output=True)
            for f in ("research-brain-backup.service", "research-brain-backup.timer"):
                (units / f).unlink(missing_ok=True)
            return "Removed the nightly snapshot timer."
        units.mkdir(parents=True, exist_ok=True)
        (units / "research-brain-backup.service").write_text(
            "[Unit]\nDescription=research brain library snapshot\n\n[Service]\nType=oneshot\n"
            f"ExecStart={subprocess.list2cmdline(cmd)}\n", encoding="utf-8", newline="\n")
        (units / "research-brain-backup.timer").write_text(
            "[Unit]\nDescription=Nightly research brain library snapshot\n\n[Timer]\nOnCalendar=*-*-* 02:30\n"
            "Persistent=true\n\n[Install]\nWantedBy=timers.target\n", encoding="utf-8", newline="\n")
        r = subprocess.run(["systemctl", "--user", "enable", "--now", "research-brain-backup.timer"],
                           capture_output=True, text=True)
        if r.returncode == 0:
            return "Added a systemd user timer: a snapshot every night at 02:30 (and after a missed night)."
        return (f"Wrote the timer to {units} but systemd refused to start it: {(r.stderr or '').strip()}. "
                "Run: systemctl --user enable --now research-brain-backup.timer")
    line = f"30 2 * * * {subprocess.list2cmdline(cmd)}"
    return f"No systemd here. Add this line with `crontab -e` for a nightly snapshot:\n  {line}"

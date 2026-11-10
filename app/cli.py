"""The `research-brain` command.

    research-brain                       start the app (same as `serve`) and open it in the browser
    research-brain serve [--host H] [--port P] [--vault DIR | --profile NAME] [--no-open]
    research-brain --version
    research-brain where                 print the data folder and the library this would use
    research-brain profiles              list profiles
    research-brain profiles create NAME [--path DIR]
    research-brain profiles use NAME     open NAME from the next start on
    research-brain export FILE.zip [--no-pdfs]
    research-brain import FILE.zip --profile NAME
    research-brain move-legacy-vault     move an old ./vault into the data folder (server must be stopped)

`serve` supervises the server process: when the app asks for a restart (switching profile, moving the
legacy library) it writes RB_RESTART_FILE and exits, and serve starts it again with the new settings.
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
import webbrowser
from pathlib import Path

from . import __version__, paths


def _port_in_use(host: str, port: int) -> bool:
    with socket.socket() as s:
        s.settimeout(0.5)
        return s.connect_ex((host if host not in ("0.0.0.0", "::", "") else "127.0.0.1", port)) == 0


def _claude_hint() -> None:
    if os.environ.get("RB_CLAUDE_BIN") or os.environ.get("RB_AGENT") or \
            any(shutil.which(n) for n in ("claude", "codex", "gemini", "copilot", "opencode", "cursor-agent", "ollama")) or \
            (Path.home() / ".local/bin/claude").exists() or (Path.home() / ".claude/local/claude").exists():
        return
    print("Note: no AI agent CLI was found on your PATH. Your library, PDFs, search, graph, review and the browser\n"
          "extension all work without one; critiques and Ask/Research need an AI tool you already have (Claude Code,\n"
          "Codex, Gemini, Copilot, OpenCode, Cursor) or a local model server (Ollama, LM Studio). No API key needed.\n"
          "Pick one in View > Library settings > AI agent.")


def _open_when_ready(url: str) -> None:
    def run():
        for _ in range(120):
            try:
                with urllib.request.urlopen(url + "/api/status", timeout=1):
                    webbrowser.open(url)
                    return
            except OSError:
                time.sleep(0.5)
    threading.Thread(target=run, daemon=True).start()


def cmd_serve(a) -> int:
    host, port = a.host, a.port
    if a.vault:
        os.environ["RB_VAULT"] = str(Path(a.vault).expanduser().resolve())
    if a.profile:
        if not paths.valid_profile(a.profile):
            print(f"Invalid profile name {a.profile!r}", file=sys.stderr)
            return 2
        os.environ["RB_PROFILE"] = a.profile
    local = "127.0.0.1" if host in ("0.0.0.0", "::", "") else host
    url = f"http://{local}:{port}"
    if _port_in_use(host, port):
        print(f"Port {port} is already in use; research_brain may already be running: {url}")
        if not a.no_open:
            webbrowser.open(url)
        return 0
    _claude_hint()
    if host not in ("127.0.0.1", "localhost"):
        print(f"Warning: listening on {host}: other devices on that network can reach the app, with no password.")
    restart_file = Path(tempfile.mkdtemp(prefix="rb_sup_")) / "restart.json"
    first = True
    child: subprocess.Popen | None = None

    def forward(sig, _frame):
        if child and child.poll() is None:
            child.send_signal(sig)
    signal.signal(signal.SIGTERM, forward)
    try:
        while True:
            if paths.load_settings().get("move_legacy_on_start"):
                try:
                    dst = paths.move_legacy_vault()
                    print(f"Moved your library to {dst} (the old folder was renamed, not deleted).")
                except paths.MoveError as e:
                    print(f"Couldn't move the old library: {e}")
                    paths.save_settings({"move_legacy_on_start": None})
            r = paths.resolve()
            for n in r.notices:
                print(n)
            print(f"research_brain {__version__}: {url}  library: {r.vault}"
                  + (f"  profile: {r.profile}" if r.profile else "") + "  (Ctrl+C to stop)")
            env = dict(os.environ, RB_RESTART_FILE=str(restart_file), RB_PORT=str(port))
            args = [sys.executable, "-m", "uvicorn", f"{__package__ or 'app'}.server:app", "--host", host, "--port", str(port),
                    "--log-level", a.log_level]
            child = subprocess.Popen(args, env=env, cwd=str(paths.PKG_DIR.parent))
            if first and not a.no_open:
                _open_when_ready(url)
            first = False
            code = child.wait()
            if not restart_file.exists():
                return code
            try:
                req = json.loads(restart_file.read_text(encoding="utf-8") or "{}")
            except ValueError:
                req = {}
            restart_file.unlink(missing_ok=True)
            if req.get("profile"):  # the app selected a profile in settings.json; let that win
                os.environ.pop("RB_PROFILE", None)
            for _ in range(40):  # wait for the port to be free again
                if not _port_in_use(host, port):
                    break
                time.sleep(0.25)
            print("Restarting research_brain…")
    except KeyboardInterrupt:
        if child and child.poll() is None:
            child.send_signal(signal.SIGINT)
            try:
                child.wait(10)
            except subprocess.TimeoutExpired:
                child.kill()
        return 0
    finally:
        shutil.rmtree(restart_file.parent, ignore_errors=True)


def cmd_where(a) -> int:
    r = paths.resolve(a.vault, a.profile)
    out = r.as_dict()
    out.update(version=__version__, extension=str(paths.app_root() / "extension"),
               settings=str(paths.settings_path()))
    print(json.dumps(out, indent=1))
    return 0


def cmd_profiles(a) -> int:
    from . import profiles
    try:
        if a.action == "create":
            out = profiles.create(a.name, a.path)
            print(f"Created profile {out['name']} at {out['path']}")
        elif a.action == "use":
            out = profiles.select(a.name)
            print(f"research_brain will open profile {out['name']} ({out['path']}) from the next start on.")
        else:
            cur = paths.resolve()
            for p in profiles.list_profiles(cur)["profiles"]:
                mark = "*" if p["selected"] else " "
                print(f"{mark} {p['name']:<20} {p['notes']:>6} notes  {p['path']}")
    except profiles.ProfileError as e:
        print(e, file=sys.stderr)
        return 1
    return 0


def cmd_export(a) -> int:
    from . import vaultzip
    r = paths.resolve(a.vault, a.profile)
    m = vaultzip.export_zip(r.vault, Path(a.file).expanduser(), include_pdfs=not a.no_pdfs)
    paths.log(f"exported {r.vault} to {a.file}")
    print(f"Wrote {a.file}: {m['notes']} notes, {m['pdfs']} PDFs, {m['bytes'] / 1e6:.1f} MB")
    return 0


def cmd_import(a) -> int:
    from . import migrate, profiles, vaultzip
    if not paths.valid_profile(a.profile):
        print("Profile names use lowercase letters, digits, - and _", file=sys.stderr)
        return 2
    if a.profile in profiles.names() and paths.profile_path(a.profile).exists():
        print(f"Profile {a.profile!r} already exists; pick a new name.", file=sys.stderr)
        return 1
    target = paths.user_data_dir() / "profiles" / a.profile
    try:
        res = vaultzip.import_zip(Path(a.file).expanduser(), target)
        migrate.run(target)
    except (vaultzip.ZipImportError, migrate.SchemaError) as e:
        print(e, file=sys.stderr)
        return 1
    paths.log(f"imported {a.file} into profile {a.profile}")
    print(f"Imported {res['notes']} notes into profile {a.profile} ({target}). "
          f"Open it with: research-brain profiles use {a.profile}")
    return 0


def cmd_move_legacy(a) -> int:
    if _port_in_use("127.0.0.1", a.port):
        print(f"Stop research_brain first (something is listening on port {a.port}).", file=sys.stderr)
        return 1
    try:
        dst = paths.move_legacy_vault()
    except paths.MoveError as e:
        print(e, file=sys.stderr)
        return 1
    print(f"Moved your library to {dst}. The old folder was renamed to vault.moved-<date>; delete it once "
          "you've checked everything is there.")
    return 0


def build_parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog="research-brain", description="A local research library for papers and notes.")
    ap.add_argument("--version", action="version", version=f"research_brain {__version__}")
    sub = ap.add_subparsers(dest="cmd")

    def lib_opts(p):
        g = p.add_mutually_exclusive_group()
        g.add_argument("--vault", help="use this library folder (turns off profile switching)")
        g.add_argument("--profile", help="use this profile")

    s = sub.add_parser("serve", help="start the app (default)")
    s.add_argument("--host", default=os.environ.get("RB_HOST", "127.0.0.1"))
    s.add_argument("--port", type=int, default=int(os.environ.get("RB_PORT", "8765")))
    s.add_argument("--no-open", action="store_true", default=bool(os.environ.get("RB_NO_OPEN")),
                   help="don't open a browser tab")
    s.add_argument("--log-level", default="warning")
    lib_opts(s)
    s.set_defaults(fn=cmd_serve)

    w = sub.add_parser("where", help="print the data folder and which library would be used")
    lib_opts(w)
    w.set_defaults(fn=cmd_where)

    p = sub.add_parser("profiles", help="list, create or select profiles")
    p.add_argument("action", nargs="?", choices=["list", "create", "use"], default="list")
    p.add_argument("name", nargs="?")
    p.add_argument("--path", help="(create) keep this profile's library in this folder")
    p.set_defaults(fn=cmd_profiles)

    e = sub.add_parser("export", help="write the whole library to a zip")
    e.add_argument("file")
    e.add_argument("--no-pdfs", action="store_true")
    lib_opts(e)
    e.set_defaults(fn=cmd_export)

    i = sub.add_parser("import", help="unpack a library zip into a new profile")
    i.add_argument("file")
    i.add_argument("--profile", required=True)
    i.set_defaults(fn=cmd_import)

    m = sub.add_parser("move-legacy-vault", help="move an old ./vault into the data folder")
    m.add_argument("--port", type=int, default=int(os.environ.get("RB_PORT", "8765")))
    m.set_defaults(fn=cmd_move_legacy)
    return ap


def main(argv: list[str] | None = None) -> int:
    ap = build_parser()
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0].startswith("--") and argv[0] not in ("--version", "-h", "--help"):
        argv = ["serve", *argv]  # `research-brain --profile x` == `research-brain serve --profile x`
    a = ap.parse_args(argv)
    if a.cmd == "profiles" and a.action in ("create", "use") and not a.name:
        ap.error(f"profiles {a.action} needs a NAME")
    return a.fn(a)


if __name__ == "__main__":
    sys.exit(main())

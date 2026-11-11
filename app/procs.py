"""Starting and stopping the AI agent CLIs the same way on macOS, Linux and Windows.

Three things differ on Windows:

1. Most agent CLIs are npm packages, installed as `claude.cmd`, `codex.cmd` and so on. Windows runs a .cmd
   through cmd.exe, which re-parses its arguments: a `"`, `%`, `&` or a newline in one (a system prompt,
   or a request some CLIs take on the command line) would be cut short or run as a command. npm's shims
   only start Node with a script, so `resolve()` reads the shim and runs Node with that script directly;
   cmd.exe never sees the arguments. A batch file it can't read is refused when an argument holds one
   of those characters, rather than run unsafely.
2. A process can't lead a POSIX session, so it starts in its own process group with no console window.
3. Killing a .cmd kills cmd.exe but not the Node process it started, so `kill_tree()` uses `taskkill /T`.
"""
from __future__ import annotations

import os
import re
import shutil
import signal
import subprocess
import sys
from pathlib import Path

WINDOWS = sys.platform == "win32"

# what cmd.exe would act on inside an argument (quotes, variables, delayed expansion, operators, lines)
_CMD_UNSAFE = re.compile(r'["%!^&|<>()\r\n]')
# npm / pnpm / yarn cmd-shims: ..."%dp0%\node_modules\pkg\cli.js" %*   (also %~dp0, and .cjs / .mjs)
_SHIM_SCRIPT = re.compile(r'"%~?dp0%?\\?([^"%]+?\.(?:c|m)?js)"', re.I)
_SHIM_EXE = re.compile(r'"%~?dp0%?\\?([^"%]+?\.exe)"\s+%\*', re.I)


def install_dirs() -> list[Path]:
    """Where agent CLIs usually land when they aren't on the PATH this app was started with."""
    home = Path.home()
    dirs = [home / ".local/bin", home / ".npm-global/bin", home / ".bun/bin", home / ".volta/bin", home / "bin"]
    if WINDOWS:
        env = os.environ
        for base, sub in (("APPDATA", "npm"), ("LOCALAPPDATA", "Microsoft/WinGet/Links"),
                          ("LOCALAPPDATA", "pnpm"), ("LOCALAPPDATA", "Programs/claude"), ("USERPROFILE", "scoop/shims")):
            if env.get(base):
                dirs.append(Path(env[base]) / sub)
        dirs += [home / ".claude/local", home / ".volta/bin"]
    elif sys.platform == "darwin":
        dirs += [Path("/opt/homebrew/bin"), Path("/usr/local/bin"), home / ".homebrew/bin"]
    else:
        dirs += [Path("/usr/local/bin"), Path("/usr/bin"), Path("/snap/bin"), home / ".local/share/pnpm",
                 Path("/home/linuxbrew/.linuxbrew/bin")]
    return dirs


def exe_names(name: str) -> list[str]:
    """`name` as it would be spelled on disk here (Windows: name.exe, name.cmd, name.bat)."""
    if WINDOWS and not name.lower().endswith((".exe", ".cmd", ".bat")):
        return [f"{name}.exe", f"{name}.cmd", f"{name}.bat", name]
    return [name]


def runnable(p: str) -> bool:
    return bool(p) and os.path.isfile(p) and (WINDOWS or os.access(p, os.X_OK))


class UnsafeCommand(ValueError):
    """A Windows batch file would receive arguments cmd.exe could misread."""


def _node_for(shim: Path) -> str | None:
    local = shim.parent / "node.exe"
    if local.is_file():
        return str(local)
    return shutil.which("node")


def resolve(cmd: list[str]) -> list[str]:
    """`cmd` ready to start. On Windows a .cmd/.bat npm shim becomes `node <its script> ...args`."""
    if not WINDOWS or not cmd:
        return list(cmd)
    exe = cmd[0]
    if not exe.lower().endswith((".cmd", ".bat")):
        return list(cmd)
    shim = Path(exe)
    try:
        text = shim.read_text(encoding="utf-8", errors="replace")
    except OSError:
        text = ""
    m = _SHIM_SCRIPT.search(text)
    if m:
        script = (shim.parent / m.group(1).replace("\\", os.sep)).resolve()
        node = _node_for(shim)
        if node and script.is_file():
            return [node, str(script), *cmd[1:]]
    m = _SHIM_EXE.search(text)
    if m:
        target = (shim.parent / m.group(1).replace("\\", os.sep)).resolve()
        if target.is_file():
            return [str(target), *cmd[1:]]
    if any(_CMD_UNSAFE.search(a or "") for a in cmd[1:]):
        raise UnsafeCommand(
            f"{shim.name} is a batch file research_brain can't run safely with this request on Windows. "
            "Install the agent's standalone .exe if it has one, or reinstall it with npm.")
    return list(cmd)


def popen_kwargs() -> dict:
    """Start the process as the leader of its own group, so it and its children can be stopped together."""
    if WINDOWS:
        return {"creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | getattr(subprocess, "CREATE_NO_WINDOW", 0)}
    return {"start_new_session": True}


def kill_tree(proc: subprocess.Popen) -> None:
    """Stop the process and everything it started. Never raises."""
    if proc.poll() is not None:
        return
    try:
        if WINDOWS:
            subprocess.run(["taskkill", "/PID", str(proc.pid), "/T", "/F"], capture_output=True, timeout=10,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        else:
            os.killpg(proc.pid, signal.SIGKILL)
    except (OSError, ProcessLookupError, subprocess.SubprocessError):
        pass
    try:
        if proc.poll() is None:
            proc.kill()
    except OSError:
        pass


def stop_gently(proc: subprocess.Popen) -> None:
    """Ask a child server to shut down (Ctrl+C on POSIX, Ctrl+Break on Windows)."""
    try:
        if WINDOWS:
            proc.send_signal(signal.CTRL_BREAK_EVENT)
        else:
            proc.send_signal(signal.SIGINT)
    except (OSError, ValueError):
        proc.terminate()

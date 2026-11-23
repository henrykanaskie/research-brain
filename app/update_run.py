"""Installs an update while research brain is stopped, then starts it again. Copied into the data folder's update/
and run from there by the Python underneath research brain's own (the one its environment was made from), so nothing it
needs is being replaced. Standard library only; it must not import the package.

    pythonw -I -S update_run.py job.json

The scripts this replaced could wait for ever on the wrong process, left a version that doesn't start in place, and on
Windows replaced files still in use and never opened the window again (the server they started held app.log open, and
Windows refuses a second redirect into an open file). On a Mac the window stays open and reloads by itself.

 1. wait for research brain to end: the processes it was told about, and any program run from the folder being replaced
    (uv's launchers, an AI agent's tools server); after a while, end what is left;
 2. install, trying again if a file was still in use (antivirus looks at new files for a moment);
 3. the new version must run; if it doesn't, or never answers once started, put the old one back;
 4. whatever happens: say how it went (exit-code), clear updating.json, start research brain (its window, if it was open).
"""
import json
import os
import subprocess
import sys
import time
import urllib.request

NO_WINDOW = 0x08000000 if os.name == "nt" else 0


def main(path):
    with open(path, encoding="utf-8") as f:
        job = json.load(f)
    d = os.path.dirname(os.path.abspath(path))
    log = open(os.path.join(d, "update.log"), "a", encoding="utf-8", errors="backslashreplace", buffering=1)

    def say(msg):
        log.write(time.strftime("%Y-%m-%d %H:%M:%S  ") + str(msg) + "\n")

    def finish(code, installed=False):
        if installed and os.name != "nt":
            return  # the Mac's window waits for the marker to go: that's once the new version answers (or the old one is back)
        tmp = os.path.join(d, "exit-code")
        with open(tmp, "w", encoding="ascii") as f:
            f.write("%d\n" % code)
        try:
            os.unlink(job["marker"])
        except OSError:
            pass

    code = 1
    try:
        say("updating %s to %s" % (job["from"], job["to"]))
        stop_all(job, say)
        code = install(job, job["install"], log, say)
        if code == 0 and not runs(job, say):
            say("the new version doesn't run")
            code = 97
        if code and not runs(job, say):
            put_back(job, log, say)
        finish(code, installed=True)
        new = start(job, say)
        if not answers(job, 120, new):
            say("it stopped, or didn't answer within 2 minutes")
            if code == 0:
                code = 98
                end(new)
                stop_all(job, say, patience=2)
                put_back(job, log, say)
                finish(code, installed=True)
                new = start(job, say)
                answers(job, 120, new)
        finish(code)
        say("done (%d)" % code)
    except BaseException as e:  # noqa: BLE001  (whatever happened, research brain must open again)
        import traceback
        say("it stopped: %r\n%s" % (e, traceback.format_exc()))
        finish(code or 99)
        if not answers(job, 3):
            start(job, say)


# ---------------------------------------------------------------- processes
def alive(pid):
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True
        # ended, but not yet collected by whoever started it (the Mac window started this server, and stays open)
        try:
            return not subprocess.run(["ps", "-o", "stat=", "-p", str(pid)], capture_output=True, timeout=5).stdout.strip().startswith(b"Z")
        except (OSError, subprocess.SubprocessError):
            return True
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x1000, False, pid)
    if not h:
        return False
    try:
        c = ctypes.c_ulong()
        return bool(k.GetExitCodeProcess(ctypes.c_void_p(h), ctypes.byref(c))) and c.value == 259
    finally:
        k.CloseHandle(ctypes.c_void_p(h))


def kill(pid):
    if os.name != "nt":
        import signal
        try:
            os.kill(pid, signal.SIGKILL)
        except OSError:
            pass
        return
    import ctypes
    k = ctypes.windll.kernel32
    k.OpenProcess.restype = ctypes.c_void_p
    h = k.OpenProcess(0x0001, False, pid)
    if h:
        k.TerminateProcess(ctypes.c_void_p(h), 1)
        k.CloseHandle(ctypes.c_void_p(h))


def run_from(folder):
    """pids of programs whose file is in folder (Windows)."""
    if os.name != "nt" or not folder:
        return []
    import ctypes
    k, ps = ctypes.windll.kernel32, ctypes.windll.psapi
    k.OpenProcess.restype = ctypes.c_void_p
    arr = (ctypes.c_ulong * 8192)()
    got = ctypes.c_ulong()
    if not ps.EnumProcesses(ctypes.byref(arr), ctypes.sizeof(arr), ctypes.byref(got)):
        return []
    want, out = os.path.normcase(long_name(folder)).rstrip("\\") + "\\", []
    for pid in arr[: got.value // ctypes.sizeof(ctypes.c_ulong)]:
        if not pid or pid == os.getpid():
            continue
        h = k.OpenProcess(0x1000, False, pid)
        if not h:
            continue
        buf, n = ctypes.create_unicode_buffer(4096), ctypes.c_ulong(4096)
        ok = k.QueryFullProcessImageNameW(ctypes.c_void_p(h), 0, buf, ctypes.byref(n))
        k.CloseHandle(ctypes.c_void_p(h))
        if ok and os.path.normcase(long_name(buf.value)).startswith(want):
            out.append(int(pid))
    return out


def long_name(p):
    """C:\\Users\\RUNNER~1 as C:\\Users\\runneradmin."""
    if os.name != "nt":
        return p
    import ctypes
    buf = ctypes.create_unicode_buffer(4096)
    return buf.value if ctypes.windll.kernel32.GetLongPathNameW(p, buf, 4096) else p


def stop_all(job, say, patience=25):
    left, t0 = [], time.time()
    while True:
        left = sorted({p for p in job["pids"] if alive(p)} | set(run_from(job.get("folder"))))
        if not left:
            say("research brain has stopped (%.0f s)" % (time.time() - t0))
            return
        if time.time() - t0 > patience:
            break
        time.sleep(0.25)
    say("still running after %d s, ending: %s" % (patience, left))
    for p in left:
        kill(p)
    t0 = time.time()
    while time.time() - t0 < 10 and (any(alive(p) for p in left) or run_from(job.get("folder"))):
        time.sleep(0.25)


# ---------------------------------------------------------------- installing
def install(job, cmd, log, say):
    code = 1
    for turn in range(4):
        say("installing: " + " ".join(cmd))
        at = log.tell()
        try:
            code = subprocess.run(cmd, cwd=job.get("cwd") or None, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                  creationflags=NO_WINDOW, timeout=1200).returncode
        except subprocess.TimeoutExpired:
            say("the install took longer than 20 minutes")
            code = 96
        except OSError as e:
            say("the installer couldn't be started: %r" % e)
            code = 95
        if code == 0:
            return 0
        with open(log.name, encoding="utf-8", errors="replace") as f:
            f.seek(0, 2)
            f.seek(max(0, f.tell() - 4000))
            said = f.read().lower()
        if not any(w in said for w in ("os error 5", "os error 32", "access is denied", "being used by another process", "winerror 5", "winerror 32")):
            break
        say("a file was still in use: trying again")
        stop_all(job, say, patience=3 + 4 * turn)
        time.sleep(2 + 3 * turn)
    say("the installer stopped with code %d" % code)
    return code


def runs(job, say):
    try:
        r = subprocess.run(job["check"], stdin=subprocess.DEVNULL, capture_output=True, creationflags=NO_WINDOW, timeout=120, cwd=job.get("home") or None)
    except (OSError, subprocess.SubprocessError) as e:
        say("couldn't run it: %r" % e)
        return False
    if r.returncode != 0:
        say("it said: " + (r.stderr or r.stdout or b"").decode("utf-8", "replace")[-1500:])
    return r.returncode == 0


def put_back(job, log, say):
    if job.get("previous"):
        say("putting %s back" % job["from"])
        code = install(dict(job, cwd=job.get("previous_cwd") or job.get("home")), job["previous"], log, say)
        if code and job.get("previous_unpinned") not in (None, job["previous"]):
            install(dict(job, cwd=job.get("home")), job["previous_unpinned"], log, say)


# ---------------------------------------------------------------- starting
def start(job, say):
    cmd = job.get("open") or job["serve"]
    say("starting: " + " ".join(cmd))
    flags = (0x00000200 | NO_WINDOW) if os.name == "nt" else 0
    env = {k: v for k, v in os.environ.items() if k not in ("RB_RESTART_FILE", "RB_PORT")}
    try:
        with open(job["applog"], "ab") as out:
            return subprocess.Popen(cmd, stdin=subprocess.DEVNULL, stdout=out, stderr=subprocess.STDOUT, env=env, cwd=job.get("home") or None,
                             close_fds=True, creationflags=flags, start_new_session=os.name != "nt")
    except OSError as e:
        say("couldn't start it: %r" % e)
    return None


def end(proc):
    """End what start() started, and what that started (Mac, Linux; on Windows stop_all finds them by their folder)."""
    if proc is not None and os.name != "nt":
        import signal
        try:
            os.killpg(proc.pid, signal.SIGKILL)
        except OSError:
            pass
        try:
            proc.wait(10)
        except subprocess.SubprocessError:
            pass


def answers(job, limit, proc=None):
    t0 = time.time()
    opener = urllib.request.build_opener(urllib.request.ProxyHandler({}))
    while time.time() - t0 < limit:
        try:
            with opener.open("http://127.0.0.1:%d/api/heartbeat" % job["port"], timeout=3) as r:
                if r.status == 200:
                    return True
        except OSError:
            pass
        if proc is not None and os.name != "nt" and proc.poll() is not None:
            return False  # it has stopped (on Windows this is the launcher of a window, which may end by itself)
        time.sleep(0.5)
    return False


if __name__ == "__main__":
    main(sys.argv[1])

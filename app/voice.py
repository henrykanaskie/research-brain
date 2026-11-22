"""The natural voice: answers read aloud by Kokoro, a small open neural voice model, entirely on this computer.

Set up once, when you first ask for it (Voice mode or Read aloud offer it): about 330 MB, kept in the app's
data folder under voice/, so app updates don't touch it:
    voice/env/           its own Python environment (kokoro-onnx, onnxruntime, soundfile), made with uv
                         (or python -m venv) so none of it goes into the app's own environment
    voice/kokoro-v1.0.onnx, voice/voices-v1.0.bin   the model and its voices (checked against their sha256)
Then one worker process (app/voice_worker.py, run with that environment's Python) keeps the model loaded and
turns each sentence into a WAV file; POST /api/voice/tts hands it to the page, which plays it.
Nothing leaves the computer except the one-time downloads (PyPI and the model files on GitHub); the text of
your answers never does.
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import urllib.request
from pathlib import Path

from . import paths

MODEL_BASE = "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0/"
FILES = {  # name: (sha256, size in bytes)
    "kokoro-v1.0.onnx": ("7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5", 325532387),
    "voices-v1.0.bin": ("bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d", 28214398),
}
PACKAGES = ["kokoro-onnx==0.6.1", "soundfile==0.14.0"]
# hearing you (Voice mode): Whisper, an open speech-recognition model, run on this computer
HEAR_PACKAGES = ["faster-whisper==1.1.1", "requests==2.32.3"]
WHISPER_MODEL = "base.en"  # about 140 MB; transcribes a few seconds of speech in under half a second
DEFAULT_VOICE = "af_heart"
VOICES = {  # the English voices offered, with a plain name
    "af_heart": "Heart (US, warm)", "af_bella": "Bella (US)", "af_nicole": "Nicole (US, soft)", "af_sarah": "Sarah (US)",
    "am_michael": "Michael (US)", "am_fenrir": "Fenrir (US, deep)", "am_puck": "Puck (US, bright)",
    "bf_emma": "Emma (UK)", "bf_isabella": "Isabella (UK)", "bm_george": "George (UK)", "bm_fable": "Fable (UK)",
}

_lock = threading.Lock()
_state = {"state": "", "progress": 0.0, "error": "", "step": ""}


def root() -> Path:
    return Path(os.environ.get("RB_VOICE_DIR") or (paths.user_data_dir() / "voice"))


def _python() -> Path:
    env = root() / "env"
    return env / ("Scripts/python.exe" if os.name == "nt" else "bin/python")


def ready() -> bool:
    if not _python().exists():
        return False
    for name, (_, size) in FILES.items():
        p = root() / name
        if not p.exists() or p.stat().st_size != size:
            return False
    return (root() / ".ok").exists()


def hear_ready() -> bool:
    """It can hear you too: Whisper and its model are set up."""
    return ready() and (root() / ".ok-hear").exists() and (root() / "whisper").is_dir()


def status() -> dict:
    st = dict(_state)
    if not st["state"]:
        st["state"] = "ready" if ready() else "missing"
    st["hear"] = hear_ready()
    st["voice"] = chosen_voice()
    st["voices"] = VOICES
    st["female"] = [k for k in VOICES if gender(k) == "female"]
    st["male"] = [k for k in VOICES if gender(k) == "male"]
    st["system_voices"] = SYSTEM_VOICES
    st["size_mb"] = 700  # on disk: the voice (350 MB), the listening model (140 MB) and their Python environment
    return st


# without the download: the computer's own voices (the browser's speech synthesis), female or male
SYSTEM_VOICES = {"system-female": "Built-in female", "system-male": "Built-in male"}


def gender(v: str) -> str:
    return "male" if v in ("system-male",) or v[:2] in ("am", "bm") else "female"


def chosen_voice() -> str:
    v = (paths.load_settings().get("voice") or "").strip()
    return v if v in VOICES or v in SYSTEM_VOICES else DEFAULT_VOICE


def set_voice(v: str) -> str:
    if v not in VOICES and v not in SYSTEM_VOICES:
        raise ValueError("Not one of the voices")
    paths.save_settings({"voice": v})
    return v


def remove() -> None:
    """Delete the natural voice (its environment, model and voices); the built-in voices are used again."""
    worker.stop()
    if _state.get("state") == "installing":
        raise RuntimeError("It's still being set up; try again when that's done")
    shutil.rmtree(root(), ignore_errors=True)
    _state.update(state="", progress=0.0, error="", step="")


# ---------------------------------------------------------------- setting it up
def install() -> dict:
    """Start setting the voice up in the background (no-op while it runs or once it's ready)."""
    with _lock:
        if _state["state"] == "installing" or (ready() and hear_ready()):
            return status()
        _state.update(state="installing", progress=0.0, error="", step="Preparing")
    threading.Thread(target=_install, daemon=True, name="rb-voice-install").start()
    return status()


def _step(step: str, progress: float) -> None:
    _state.update(step=step, progress=round(max(0.0, min(1.0, progress)), 3))


def _install() -> None:
    try:
        d = root()
        d.mkdir(parents=True, exist_ok=True)
        if not _python().exists():
            _step("Setting up the voice’s environment", 0.02)
            uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
            env = d / "env"
            if Path(uv).exists():
                subprocess.run([uv, "venv", "-q", "-p", "3.12", str(env)], check=True, capture_output=True, timeout=600)
                _step("Installing the voice’s software", 0.06)
                subprocess.run([uv, "pip", "install", "-q", "--python", str(_python()), *PACKAGES], check=True,
                               capture_output=True, timeout=1200)
            else:
                subprocess.run([sys.executable, "-m", "venv", str(env)], check=True, capture_output=True, timeout=600)
                _step("Installing the voice’s software", 0.06)
                subprocess.run([str(_python()), "-m", "pip", "install", "-q", *PACKAGES], check=True,
                               capture_output=True, timeout=1200)
        # hearing (an older setup could only speak): Whisper's software, added to the same environment
        if not (d / ".ok-hear").exists():
            _step("Installing the listening software", 0.08)
            uv = shutil.which("uv") or str(Path.home() / ".local/bin/uv")
            cmd = ([uv, "pip", "install", "-q", "--python", str(_python()), *HEAR_PACKAGES] if Path(uv).exists()
                   else [str(_python()), "-m", "pip", "install", "-q", *HEAR_PACKAGES])
            subprocess.run(cmd, check=True, capture_output=True, timeout=1200)
        total = sum(s for _, s in FILES.values())
        done = 0
        for name, (sha, size) in FILES.items():
            p = d / name
            if p.exists() and p.stat().st_size == size:
                done += size
                continue
            _step("Downloading the voice", 0.1 + 0.88 * done / total)
            part = p.with_suffix(p.suffix + ".part")
            h = hashlib.sha256()
            req = urllib.request.Request(MODEL_BASE + name, headers={"User-Agent": "research_brain voice setup"})
            with urllib.request.urlopen(req, timeout=60) as r, open(part, "wb") as f:
                while True:
                    chunk = r.read(1 << 20)
                    if not chunk:
                        break
                    f.write(chunk)
                    h.update(chunk)
                    done += len(chunk)
                    _step("Downloading the voice", 0.1 + 0.88 * done / total)
            if h.hexdigest() != sha:
                part.unlink(missing_ok=True)
                raise RuntimeError(f"{name} didn't download correctly (its checksum doesn't match). Try again.")
            part.replace(p)
        (d / ".ok").write_text(time.strftime("%Y-%m-%d"), encoding="utf-8")
        if not (d / ".ok-hear").exists() or not (d / "whisper").is_dir():
            _step("Downloading the listening model", 0.97)
            code = ("import sys; from faster_whisper import WhisperModel; "
                    f"WhisperModel({WHISPER_MODEL!r}, device='cpu', compute_type='int8', download_root=sys.argv[1])")
            subprocess.run([str(_python()), "-c", code, str(d / "whisper")], check=True, capture_output=True, timeout=1800)
            (d / ".ok-hear").write_text(time.strftime("%Y-%m-%d"), encoding="utf-8")
        _state.update(state="ready", progress=1.0, step="", error="")
    except subprocess.CalledProcessError as e:
        msg = (e.stderr or b"").decode("utf-8", "replace").strip().splitlines()[-1:] or [str(e)]
        _state.update(state="error", error="Couldn't install the voice’s software: " + msg[0][:200], step="")
    except Exception as e:
        _state.update(state="error", error=str(e)[:300] or e.__class__.__name__, step="")


# ---------------------------------------------------------------- speaking
class _Worker:
    def __init__(self):
        self.proc = None
        self.lock = threading.Lock()
        self.n = 0

    def _start(self):
        script = Path(__file__).with_name("voice_worker.py")
        self.proc = subprocess.Popen([str(_python()), str(script), str(root() / "kokoro-v1.0.onnx"),
                                      str(root() / "voices-v1.0.bin")],
                                     stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
                                     text=True, encoding="utf-8", errors="replace", bufsize=1,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        first = self.proc.stdout.readline()
        if not first or not json.loads(first).get("ready"):
            self.stop()
            raise RuntimeError("The voice didn't start")

    def speak(self, text: str, voice: str, speed: float = 1.0) -> bytes:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.n += 1
            out = Path(tempfile.gettempdir()) / f"rb-voice-{os.getpid()}-{self.n}.wav"
            req = {"id": str(self.n), "text": text, "voice": voice, "speed": speed, "out": str(out)}
            try:
                self.proc.stdin.write(json.dumps(req) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except (BrokenPipeError, OSError):
                self.stop()
                raise RuntimeError("The voice stopped. Try again.")
            if not line:
                self.stop()
                raise RuntimeError("The voice stopped. Try again.")
            ans = json.loads(line)
            if not ans.get("ok"):
                raise RuntimeError(ans.get("error") or "The voice couldn't say that")
            try:
                return out.read_bytes()
            finally:
                out.unlink(missing_ok=True)

    def hear(self, wav: bytes) -> str:
        with self.lock:
            if self.proc is None or self.proc.poll() is not None:
                self._start()
            self.n += 1
            src = Path(tempfile.gettempdir()) / f"rb-hear-{os.getpid()}-{self.n}.wav"
            src.write_bytes(wav)
            try:
                self.proc.stdin.write(json.dumps({"id": str(self.n), "op": "stt", "wav": str(src),
                                                  "whisper": str(root() / "whisper"), "model": WHISPER_MODEL}) + "\n")
                self.proc.stdin.flush()
                line = self.proc.stdout.readline()
            except (BrokenPipeError, OSError):
                self.stop()
                raise RuntimeError("The voice stopped. Try again.")
            finally:
                src.unlink(missing_ok=True)
            if not line:
                self.stop()
                raise RuntimeError("The voice stopped. Try again.")
            ans = json.loads(line)
            if not ans.get("ok"):
                raise RuntimeError(ans.get("error") or "Couldn't make out what was said")
            return ans.get("text") or ""

    def stop(self):
        p, self.proc = self.proc, None
        if p is not None and p.poll() is None:
            p.kill()


worker = _Worker()


def hear(wav: bytes) -> str:
    """What was said in a short recording (WAV, 16 kHz mono from the page), transcribed on this computer."""
    if not hear_ready():
        raise RuntimeError("Listening isn't set up yet")
    if not wav or len(wav) < 1000:
        return ""
    return worker.hear(wav)


def speak(text: str, voice: str | None = None, speed: float = 1.0) -> bytes:
    """WAV audio of the text in the natural voice. Raises RuntimeError when it isn't set up or fails."""
    if not ready():
        raise RuntimeError("The natural voice isn't set up yet")
    text = " ".join(str(text or "").split())[:2000]
    if not text:
        raise RuntimeError("Nothing to say")
    v = voice if voice in VOICES else chosen_voice()
    if v not in VOICES:  # a built-in voice is chosen but the natural one was asked for: same kind of voice
        v = "am_michael" if gender(v) == "male" else DEFAULT_VOICE
    return worker.speak(text, v, max(0.6, min(1.6, float(speed or 1.0))))


def warm() -> None:
    """Load the model in the background (Voice mode calls this as it opens, so the first answer isn't slow)."""
    if not ready():
        return

    def go():
        try:
            with worker.lock:
                if worker.proc is None or worker.proc.poll() is not None:
                    worker._start()
        except Exception as e:
            print(f"[research_brain] voice warm-up: {e!r}")
    threading.Thread(target=go, daemon=True, name="rb-voice-warm").start()

"""The natural voice's worker (app/voice.py runs it with the voice's own Python, not the app's).

Reads one JSON request per line on stdin: {"id": "...", "text": "...", "voice": "af_heart", "speed": 1.0, "out": "<path>"}
writes the speech to <out> as a WAV file, and answers on stdout with {"id": "...", "ok": true} or
{"id": "...", "ok": false, "error": "..."}. The model stays loaded between requests. Standalone on purpose:
it imports nothing from research brain, only kokoro_onnx and soundfile from the voice's environment.
"""
import json
import sys

_whisper = None  # the listening model, loaded the first time it's needed


def main() -> int:
    model, voices = sys.argv[1], sys.argv[2]
    from kokoro_onnx import Kokoro
    import soundfile as sf
    k = Kokoro(model, voices)
    names = set(k.get_voices())
    print(json.dumps({"ready": True, "voices": sorted(names)}), flush=True)
    for line in sys.stdin:
        try:
            req = json.loads(line)
        except ValueError:
            continue
        rid = req.get("id", "")
        if req.get("op") == "stt":  # hearing: a short recording in, its words out
            try:
                global _whisper
                if _whisper is None:
                    from faster_whisper import WhisperModel
                    _whisper = WhisperModel(req.get("model") or "base.en", device="cpu", compute_type="int8",
                                            download_root=req["whisper"], local_files_only=True)
                audio, rate = sf.read(req["wav"], dtype="float32", always_2d=False)
                if getattr(audio, "ndim", 1) > 1:
                    audio = audio.mean(axis=1)
                if rate != 16000:  # the page sends 16 kHz; anything else is resampled simply
                    import numpy as np
                    n = int(len(audio) * 16000 / rate)
                    audio = np.interp(np.linspace(0, len(audio) - 1, n), np.arange(len(audio)), audio).astype("float32")
                segs, _ = _whisper.transcribe(audio, beam_size=1, language="en", condition_on_previous_text=False,
                                              vad_filter=False, no_speech_threshold=0.6)
                text = " ".join(x.text.strip() for x in segs if x.no_speech_prob < 0.7).strip()
                print(json.dumps({"id": rid, "ok": True, "text": text}), flush=True)
            except Exception as e:
                print(json.dumps({"id": rid, "ok": False, "error": str(e)[:300]}), flush=True)
            continue
        try:
            voice = req.get("voice") if req.get("voice") in names else "af_heart"
            text = str(req.get("text") or "")[:2000]
            lang = "en-gb" if voice[:1] == "b" else "en-us"
            samples, rate = k.create(text, voice=voice, speed=float(req.get("speed") or 1.0), lang=lang)
            sf.write(req["out"], samples, rate)
            print(json.dumps({"id": rid, "ok": True}), flush=True)
        except Exception as e:  # one bad sentence never stops the voice
            print(json.dumps({"id": rid, "ok": False, "error": str(e)[:300]}), flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())

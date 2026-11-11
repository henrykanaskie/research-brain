"""Registry of AI backends (see base.py for the interface and the rules every backend follows).

Each adapter module in this package calls register(Backend()) when imported; every module here is
imported on first use (one that fails to import is skipped and logged once). The user's choice is the
`agent` key in the app settings file (app/paths.py); without one, the first available backend in
ORDER is used. RB_AGENT (environment) overrides the saved choice, e.g. for tests or scripting.
"""
from __future__ import annotations

import importlib
import logging
import os
import pkgutil
import threading

from .base import AgentError, Backend, Detection, ToolGuardError  # noqa: F401

ORDER = ("claude", "codex", "gemini", "copilot", "opencode", "qwen", "cursor", "ollama", "openai-local")
_SKIP = {"base", "research"}

_backends: dict[str, Backend] = {}
_loaded = False
_lock = threading.RLock()
_failed: set[str] = set()
log = logging.getLogger("research_brain.agents")


def register(backend: Backend) -> Backend:
    _guard_backend(backend)
    with _lock:
        _backends[backend.id] = backend
    return backend


# ---------------------------------------------------------------- the privacy choke point (app/privacy.py)
# Every backend's ask / ask_stream / ask_parts / stream_parts is wrapped when it is registered, so nothing
# reaches a model without privacy.check_send: [[links]] to private notes are scrubbed, any private id or
# copied private text refuses the call (PrivacyError, an AgentError) and the send is written to the audit
# log. Callers that know more (the purpose, e.g. "chat") call guard_send first; the wrapper then sees the
# same, already approved, text and neither checks nor logs it twice.
def guard_send(backend: Backend, purpose: str, *parts: str, sources=None, approve=()) -> list[str]:
    from .. import privacy
    return privacy.check_send(backend, purpose, list(parts), sources=sources, approve=approve)


def _guard_backend(b: Backend) -> None:
    if getattr(b, "_rb_privacy_wrapped", False):
        return
    import functools

    def wrap_plain(fn):
        @functools.wraps(fn)
        def ask(prompt, *a, **kw):
            (prompt,) = guard_send(b, "other", prompt)
            return fn(prompt, *a, **kw)
        return ask

    def wrap_stream(fn):
        @functools.wraps(fn)
        def ask_stream(prompt, *a, **kw):
            (prompt,) = guard_send(b, "other", prompt)  # before the process starts, not at first read
            return fn(prompt, *a, **kw)
        return ask_stream

    def wrap_parts(fn):
        @functools.wraps(fn)
        def parts(instruction, context="", system="", *a, **kw):
            instruction, context = guard_send(b, "other", instruction, context)
            return fn(instruction, context, system, *a, **kw)
        return parts

    for name, wrap in (("ask", wrap_plain), ("ask_stream", wrap_stream), ("ask_parts", wrap_parts),
                       ("stream_parts", wrap_parts)):
        fn = getattr(b, name, None)
        if callable(fn):
            try:
                setattr(b, name, wrap(fn))
            except (AttributeError, TypeError):  # pragma: no cover (a backend with __slots__)
                pass
    try:
        b._rb_privacy_wrapped = True
    except AttributeError:  # pragma: no cover
        pass


def unregister(backend_id: str) -> None:
    with _lock:
        _backends.pop(backend_id, None)


def _load() -> None:
    global _loaded
    with _lock:
        if _loaded:
            return
        _loaded = True
        for m in pkgutil.iter_modules(__path__):
            if m.name in _SKIP or m.name.startswith("_"):
                continue
            try:
                importlib.import_module(f"{__name__}.{m.name}")
            except Exception as e:  # a broken adapter must not take the app down
                if m.name not in _failed:
                    _failed.add(m.name)
                    log.warning("research_brain: skipped AI agent adapter %s (%s: %s)", m.name, type(e).__name__, e)


def _rank(b: Backend) -> tuple[int, str]:
    return (ORDER.index(b.id) if b.id in ORDER else len(ORDER), b.id)


def all_backends() -> list[Backend]:
    _load()
    with _lock:
        return sorted(_backends.values(), key=_rank)


def get(backend_id: str) -> Backend | None:
    _load()
    with _lock:
        return _backends.get(backend_id)


def _settings() -> dict:
    from .. import paths
    return paths.load_settings()


def chosen_id() -> str:
    """The explicitly chosen backend id ("" when none): RB_AGENT, else settings `agent`."""
    env = os.environ.get("RB_AGENT", "").strip()
    if env:
        return env
    return str(_settings().get("agent") or "")


def active() -> Backend:
    """The backend to use now: the chosen one if it is registered, else the first available in ORDER,
    else Claude (whose errors explain how to install it), else whatever is registered."""
    want = chosen_id()
    b = get(want) if want else None
    if b is not None:
        return b
    backs = all_backends()
    # prefer an agent that is installed and not known to be signed out
    for c in backs:
        d = detection(c)
        if d.available and d.signed_in is not False:
            return c
    for c in backs:
        if detection(c).available:
            return c
    fallback = get("claude") or (backs[0] if backs else None)
    if fallback is None:
        raise AgentError("No AI agent adapters are installed.")
    return fallback


def set_active(backend_id: str, model: str | None = None, url: str | None = None) -> Backend:
    """Save the choice (and, for local servers, the model and server URL) in the settings file.
    A URL is checked by the backend first (local servers accept loopback addresses only)."""
    b = get(backend_id)
    if b is None:
        raise AgentError(f"Unknown AI agent: {backend_id!r}")
    from .. import paths
    updates: dict = {"agent": backend_id}
    if url is not None:
        if not hasattr(b, "check_url"):
            raise AgentError(f"{b.name} doesn't use a server address.")
        urls = dict(_settings().get("agent_urls") or {})
        if url.strip():
            urls[backend_id] = b.check_url(url)
        else:
            urls.pop(backend_id, None)
        updates["agent_urls"] = urls
        _det_cache.pop(backend_id, None)
    if model is not None:
        models = dict(_settings().get("agent_models") or {})
        if model:
            models[backend_id] = str(model)
        else:
            models.pop(backend_id, None)
        updates["agent_models"] = models
    paths.save_settings(updates)
    return b


def model_for(backend_id: str) -> str:
    """The model saved for a backend ("" = the backend's default)."""
    return str((_settings().get("agent_models") or {}).get(backend_id) or "")


def url_for(backend_id: str) -> str:
    """The server URL saved for a local-server backend ("" = its default)."""
    return str((_settings().get("agent_urls") or {}).get(backend_id) or "")


def setting(key: str, default=None):
    return _settings().get(key, default)


# ---------------------------------------------------------------- detection cache
DETECT_TTL = float(os.environ.get("RB_AGENT_DETECT_TTL", "30"))
_det_cache: dict[str, tuple[float, Detection]] = {}


def detection(b: Backend, fresh: bool = False) -> Detection:
    """b.detect(), cached for DETECT_TTL seconds (CLIs are probed with `--version`). Never raises."""
    import time
    now = time.monotonic()
    hit = _det_cache.get(b.id)
    if hit and not fresh and now - hit[0] < DETECT_TTL:
        return hit[1]
    try:
        det = b.detect()
    except Exception as e:
        det = Detection(False, note=f"Couldn't check: {e}")
    _det_cache[b.id] = (now, det)
    return det


def forget_detection() -> None:
    _det_cache.clear()


def describe(b: Backend, fresh: bool = False) -> dict:
    det = detection(b, fresh)
    out = {"id": b.id, "name": b.name, "kind": b.kind, "home": b.home, "cost_note": b.cost_note,
           "detection": det.as_dict()}
    if b.kind != "local-server":
        out["model"] = model_for(b.id)
        # suggested models, only where listing them is free (no process start, no network)
        if getattr(b, "lists_models_cheaply", False) and det.available:
            try:
                out["models"] = b.models()
            except Exception:
                out["models"] = []
        else:
            out["models"] = []
    if b.kind == "local-server":
        out["model"] = model_for(b.id)
        try:
            out["models"] = b.models() if det.available else []
        except Exception:
            out["models"] = []
        if hasattr(b, "base_url"):
            try:
                out["url"] = b.base_url()
            except Exception:
                out["url"] = ""
    return out


# ---------------------------------------------------------------- calls used by the server
TIMEOUT = float(os.environ.get("RB_AGENT_TIMEOUT", os.environ.get("RB_CLAUDE_TIMEOUT", "240")))


def join_parts(instruction: str, context: str = "") -> str:
    """One prompt from the server's (instruction, context) pair, for backends that take a single prompt:
    the material first, the task last."""
    if not context:
        return instruction
    return f"{context}\n\n---\nTASK: {instruction}"


def ask_parts(instruction: str, context: str = "", system: str = "", backend: Backend | None = None,
              purpose: str = "ask", sources=None) -> str:
    b = backend or active()
    instruction, context = guard_send(b, purpose, instruction, context, sources=sources,
                                      approve=lambda c: [join_parts(*c)])
    if hasattr(b, "ask_parts"):
        return b.ask_parts(instruction, context, system)
    return b.ask(join_parts(instruction, context), system=system, timeout=TIMEOUT).strip()


def stream_parts(instruction: str, context: str = "", system: str = "", backend: Backend | None = None,
                 heartbeat: float | None = None, purpose: str = "ask", sources=None):
    """Yield answer text as it arrives; with `heartbeat`, "" whenever nothing arrived for that long.
    The privacy check runs when this is first iterated, before anything is sent."""
    b = backend or active()
    instruction, context = guard_send(b, purpose, instruction, context, sources=sources,
                                      approve=lambda c: [join_parts(*c)])
    if hasattr(b, "stream_parts"):
        yield from b.stream_parts(instruction, context, system, heartbeat=heartbeat)
        return
    gen = b.ask_stream(join_parts(instruction, context), system=system, timeout=TIMEOUT)
    yield from with_heartbeat(gen, heartbeat)


def with_heartbeat(gen, heartbeat: float | None):
    """Run generator `gen` in a worker thread and re-yield its items, yielding "" every `heartbeat`
    seconds of silence. Closing this generator asks the worker to close `gen` (which kills the CLI)
    at its next item."""
    if not heartbeat:
        yield from gen
        return
    import queue as _queue
    q: _queue.Queue = _queue.Queue()
    stop = threading.Event()
    END = object()

    def work():
        try:
            for item in gen:
                if stop.is_set():
                    break
                q.put(("item", item))
        except BaseException as e:  # handed to the consumer
            q.put(("err", e))
        finally:
            try:
                gen.close()
            except Exception:
                pass
            q.put(("end", END))

    threading.Thread(target=work, daemon=True).start()
    try:
        while True:
            try:
                kind, val = q.get(timeout=heartbeat)
            except _queue.Empty:
                yield ""
                continue
            if kind == "item":
                yield val
            elif kind == "err":
                raise val
            else:
                return
    finally:
        stop.set()

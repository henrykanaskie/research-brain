"""Live updates: watch vault/notes/ for edits made outside the app and fan them out to open pages.

    file change (Obsidian, an editor, git pull, sync)  ->  watchdog  ->  300 ms debounce
        ->  MetaIndex.sync(only=changed ids)  ->  drop the app's own recent writes
        ->  EventBus.publish({"type": "notes-changed", "ids": [...]})  ->  GET /api/events (SSE)

The app's own saves already update the metadata index directly (Store.save -> MetaIndex.put); the
watcher sees those files change too, and MetaIndex.is_self_write() filters them out for ~2 s so the
page that made the edit isn't told to reload what it just saved.
"""
from __future__ import annotations

import asyncio
import json
import threading
import time
from pathlib import Path

DEBOUNCE = 0.3
KEEPALIVE = 20.0


class EventBus:
    """Thread-safe fan-out of small JSON events to asyncio subscribers (one queue per SSE client)."""

    def __init__(self):
        self._subs: set[tuple[asyncio.AbstractEventLoop, asyncio.Queue]] = set()
        self._lock = threading.Lock()
        self.closed = False
        self._listeners: list = []  # in-process fn(event) callbacks (e.g. app/questions.py's matcher)

    def listen(self, fn) -> None:
        """Call fn(event) for every published event, in the publishing thread. Keep it quick."""
        with self._lock:
            self._listeners.append(fn)

    def close(self, final: dict | None = None) -> None:
        """End every open stream (server shutdown). Safe from a signal handler or any thread. `final` is sent
        first, so a page waiting on its stream gets it before the stream ends."""
        if final is not None and not self.closed:
            self.publish(final)
        self.closed = True
        self.publish(None)

    def subscribe(self) -> tuple[asyncio.AbstractEventLoop, asyncio.Queue]:
        sub = (asyncio.get_running_loop(), asyncio.Queue(maxsize=200))
        with self._lock:
            self._subs.add(sub)
        return sub

    def unsubscribe(self, sub) -> None:
        with self._lock:
            self._subs.discard(sub)

    @property
    def count(self) -> int:
        with self._lock:
            return len(self._subs)

    def publish(self, event: dict | None) -> None:
        with self._lock:
            subs = list(self._subs)
            listeners = list(self._listeners) if event is not None else []
        for fn in listeners:
            try:
                fn(event)
            except Exception as e:
                print(f"[research_brain] event listener failed: {e!r}")
        for loop, q in subs:
            try:
                loop.call_soon_threadsafe(_put_nowait, q, event)
            except RuntimeError:  # loop closed
                self.unsubscribe((loop, q))


def _put_nowait(q: asyncio.Queue, event: dict) -> None:
    try:
        q.put_nowait(event)
    except asyncio.QueueFull:  # a stalled client: drop the oldest, keep the newest
        try:
            q.get_nowait()
            q.put_nowait(event)
        except (asyncio.QueueEmpty, asyncio.QueueFull):
            pass


def sse(event: str, data) -> str:
    payload = data if isinstance(data, str) else json.dumps(data, ensure_ascii=False)
    return f"event: {event}\n" + "".join(f"data: {line}\n" for line in payload.split("\n")) + "\n"


async def event_stream(bus: EventBus, request, keepalive: float = KEEPALIVE):
    """Async generator of SSE text for one client. Ends when the client disconnects."""
    sub = bus.subscribe()
    q = sub[1]
    try:
        yield "retry: 2000\n\n" + sse("hello", {"ok": True})
        while not bus.closed:
            try:
                ev = await asyncio.wait_for(q.get(), timeout=keepalive)
            except asyncio.TimeoutError:
                if await request.is_disconnected():
                    break
                yield ": keep-alive\n\n"
                continue
            if ev is None:  # bus closed: the server is shutting down
                break
            yield sse(ev.get("type", "message"), ev)
    finally:
        bus.unsubscribe(sub)


class VaultWatcher:
    """watchdog observer on notes/ with a debounce; resyncs meta for the changed files and publishes."""

    def __init__(self, store, bus: EventBus, on_change=None, debounce: float = DEBOUNCE):
        self.store, self.bus, self.on_change, self.debounce = store, bus, on_change, debounce
        self._pending: set[str] = set()
        self._lock = threading.Lock()
        self._timer: threading.Timer | None = None
        self.observer = None
        self.error = ""

    def start(self) -> bool:
        try:
            from watchdog.events import FileSystemEventHandler
            from watchdog.observers import Observer
        except ImportError:
            self.error = "watchdog isn't installed (pip install watchdog); outside edits show up on the next refresh"
            print(f"[research_brain] {self.error}")
            return False
        watcher = self

        class Handler(FileSystemEventHandler):
            def on_any_event(self, event):
                if event.is_directory:
                    return
                for p in (getattr(event, "src_path", ""), getattr(event, "dest_path", "")):
                    if p:
                        watcher._touch(p)

        try:
            self.observer = Observer()
            self.observer.daemon = True
            self.observer.schedule(Handler(), str(self.store.notes_dir), recursive=False)
            self.observer.start()
            return True
        except Exception as e:  # e.g. inotify watch limit reached
            self.error = f"couldn't watch {self.store.notes_dir}: {e}"
            print(f"[research_brain] {self.error}")
            self.observer = None
            return False

    def stop(self) -> None:
        if self.observer is not None:
            self.observer.stop()
            self.observer.join(timeout=3)
            self.observer = None
        with self._lock:
            if self._timer:
                self._timer.cancel()

    def _touch(self, path: str) -> None:
        name = Path(path if isinstance(path, str) else path.decode()).name
        if not name.endswith(".md") or name.startswith("."):
            return  # ignores our own <id>.md.tmp writes and editor swap files
        with self._lock:
            self._pending.add(name[:-3])
            if self._timer:
                self._timer.cancel()
            self._timer = threading.Timer(self.debounce, self._flush)
            self._timer.daemon = True
            self._timer.start()

    def _flush(self) -> None:
        with self._lock:
            ids, self._pending = self._pending, set()
            self._timer = None
        if not ids:
            return
        try:
            meta = self.store.meta
            if meta is not None and meta.ok:
                meta.sync(only=ids)  # may find nothing new if a request already synced these files
                outside = meta.pop_external(ids)
            else:
                outside = sorted(ids)
            if not outside:
                return
            if self.on_change:
                self.on_change(outside)
            self.bus.publish({"type": "notes-changed", "ids": outside, "at": time.time()})
        except Exception as e:
            print(f"[research_brain] live update failed: {e!r}")

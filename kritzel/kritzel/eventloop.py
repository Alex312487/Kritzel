"""A small single-threaded event loop: file descriptors, named timers and
calls posted from other threads or signal handlers."""

from __future__ import annotations

import collections
import logging
import os
import select
import time

log = logging.getLogger("kritzel.loop")


class Loop:
    def __init__(self):
        self.running = True
        self._timers: dict[str, tuple[float, object]] = {}
        self._readers: dict[int, object] = {}
        self._idle: list = []
        self._calls: collections.deque = collections.deque()
        self._wake_r, self._wake_w = os.pipe()
        os.set_blocking(self._wake_r, False)
        os.set_blocking(self._wake_w, False)

    # -- timers ------------------------------------------------------------

    def set_timer(self, name: str, delay: float, fn):
        """(Re)arms the one-shot timer `name`."""
        self._timers[name] = (time.monotonic() + delay, fn)

    def cancel_timer(self, name: str):
        self._timers.pop(name, None)

    def has_timer(self, name: str) -> bool:
        return name in self._timers

    # -- sources -----------------------------------------------------------

    def add_reader(self, fd: int, fn):
        """fn() is called when fd is readable or closed; returning False
        stops the loop."""
        self._readers[fd] = fn

    def add_idle(self, fn):
        """fn() runs once per iteration, before the loop goes to sleep."""
        self._idle.append(fn)

    def call_soon_threadsafe(self, fn, *args):
        """Runs fn(*args) on the loop thread.  Safe from other threads and
        from signal handlers (deque.append takes no lock)."""
        self._calls.append((fn, args))
        try:
            os.write(self._wake_w, b"x")
        except OSError:
            pass                        # pipe full: a wake-up is pending anyway

    def stop(self):
        self.running = False

    # -- running -----------------------------------------------------------

    def run(self):
        poller = select.poll()
        poller.register(self._wake_r, select.POLLIN)
        for fd in self._readers:
            poller.register(fd, select.POLLIN)
        while self.running:
            for fn in self._idle:
                fn()
            timeout = None
            if self._timers:
                timeout = max(0.0, min(t for t, _ in self._timers.values()) - time.monotonic()) * 1000 + 1
            for fd, _ev in poller.poll(timeout):
                if fd == self._wake_r:
                    try:
                        os.read(self._wake_r, 4096)
                    except OSError:
                        pass
                elif self._readers[fd]() is False:
                    return
            while self._calls:
                fn, args = self._calls.popleft()
                self._guarded(fn, *args)
            now = time.monotonic()
            for name, (deadline, fn) in list(self._timers.items()):
                if deadline <= now and self._timers.get(name, (None,))[0] == deadline:
                    del self._timers[name]
                    self._guarded(fn)

    @staticmethod
    def _guarded(fn, *args):
        try:
            fn(*args)
        except Exception:
            log.exception("callback %r failed", getattr(fn, "__name__", fn))

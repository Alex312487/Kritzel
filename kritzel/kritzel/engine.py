"""Runs the recogniser on a background thread so that drawing never stalls."""

from __future__ import annotations

import logging
import queue
import threading
import time

log = logging.getLogger("kritzel.engine")


class RecognitionWorker:
    """start() loads the model (about 100 MB of RAM), which is why it is only
    called when the first stroke is drawn.  Results come back on the loop
    thread: on_ready(), on_error(message), on_result(generation, candidates)
    with candidates = [(text, confidence), ...]."""

    def __init__(self, cfg, loop, on_ready, on_error, on_result):
        self.cfg = cfg
        self.loop = loop
        self.on_ready, self.on_error, self.on_result = on_ready, on_error, on_result
        self.ready = False
        self._jobs: queue.Queue = queue.Queue()
        self._thread = None

    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, name="recognizer", daemon=True)
            self._thread.start()

    def submit(self, generation: int, image):
        self._jobs.put((generation, image))

    def _mark_ready(self):
        self.ready = True
        self.on_ready()

    def _run(self):
        try:
            from .recognizer import Lexicon, Recognizer

            lexicon = None
            languages = self.cfg.dictionary_languages()
            if languages and self.cfg.dictionary_weight > 0:
                lexicon = Lexicon(languages, self.cfg.dictionary_weight)
                log.info("dictionaries: %s", ", ".join(lexicon.loaded) or "none found")
            recognizer = Recognizer(self.cfg.model_path(), self.cfg.threads, lexicon, self.cfg.scripts)
        except Exception as e:  # noqa: BLE001 - shown to the user in the panel
            log.exception("could not load the recognition model")
            self.loop.call_soon_threadsafe(self.on_error, str(e))
            return
        self.loop.call_soon_threadsafe(self._mark_ready)
        while True:
            generation, image = self._jobs.get()
            while not self._jobs.empty():       # only the newest ink matters
                generation, image = self._jobs.get()
            try:
                t0 = time.monotonic()
                candidates = recognizer.recognize(image)
                log.debug("recognised %r in %.0f ms", candidates[:1], (time.monotonic() - t0) * 1000)
            except Exception:
                log.exception("recognition failed")
                candidates = []
            self.loop.call_soon_threadsafe(self.on_result, generation, candidates)

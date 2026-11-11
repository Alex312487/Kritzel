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
    thread:
        on_ready()
        on_error(message, fatal)    fatal: there is no recogniser at all;
                                    otherwise the previous settings stay in use
        on_result(generation, candidates)   candidates = [(text, confidence), ...]
    """

    def __init__(self, cfg, loop, on_ready, on_error, on_result, languages=None, personal=()):
        self.loop = loop
        self.on_ready, self.on_error, self.on_result = on_ready, on_error, on_result
        self.ready = False
        self._setup = (cfg, self._languages(cfg, languages), tuple(personal))
        self._jobs: queue.Queue = queue.Queue()
        self._thread = None

    @staticmethod
    def _languages(cfg, languages):
        return tuple(cfg.dictionary_languages() if languages is None else languages)

    @property
    def started(self) -> bool:
        return self._thread is not None

    def start(self):
        if self._thread is None:
            self._thread = threading.Thread(target=self._run, args=(self._setup,), name="recognizer", daemon=True)
            self._thread.start()

    def submit(self, generation: int, image):
        self._jobs.put(("recognise", generation, image))

    def configure(self, cfg, languages=None, personal=()):
        """New settings, dictionary languages or personal words.  Takes
        effect for the next recognition; the model is only loaded again if
        the model settings changed."""
        self._setup = (cfg, self._languages(cfg, languages), tuple(personal))
        if self.started:
            self._jobs.put(("configure", self._setup))

    def _mark_ready(self):
        self.ready = True
        self.on_ready()

    # -- on the recogniser thread ------------------------------------------

    @staticmethod
    def _model_settings(cfg):
        return cfg.model_path(), cfg.threads, cfg.scripts

    def _load_model(self, cfg, lexicon):
        from .recognizer import Recognizer

        path, threads, scripts = self._model_settings(cfg)
        return Recognizer(path, threads, lexicon, scripts)

    @staticmethod
    def _letters(setup):
        """The letters beyond a-z that may be recognised (see
        Recognizer.set_letters): those of the languages in use and of the
        personal words."""
        from .languages import letters_of

        cfg, languages, personal = setup
        letters = letters_of(languages) if cfg.language_letters and languages else None
        if letters is None:
            return None
        return letters + "".join(c for word in personal for c in word.lower() if ord(c) > 0x7F)

    @staticmethod
    def _make_lexicon(setup):
        from .lexicon import Lexicon

        cfg, languages, personal = setup
        if cfg.dictionary_weight <= 0 or not (languages or personal):
            return None
        lexicon = Lexicon(languages, cfg.dictionary_weight, personal, cfg.corrections)
        log.info("dictionaries: %s; %d personal words",
                 ", ".join(lexicon.loaded) or "none", len(lexicon.personal))
        return lexicon

    def _run(self, setup):
        try:
            recognizer = self._load_model(setup[0], self._make_lexicon(setup))
            recognizer.set_letters(self._letters(setup))
        except Exception as e:  # noqa: BLE001 - shown to the user in the panel
            log.exception("could not load the recognition model")
            self.loop.call_soon_threadsafe(self.on_error, str(e), True)
            return
        self.loop.call_soon_threadsafe(self._mark_ready)
        while True:
            jobs = [self._jobs.get()]
            while not self._jobs.empty():
                jobs.append(self._jobs.get())
            for job in jobs:
                if job[0] == "configure":
                    recognizer, setup = self._reconfigure(recognizer, setup, job[1])
            newest = next((job for job in reversed(jobs) if job[0] == "recognise"), None)
            if newest is None:
                continue                        # only the newest ink matters
            _, generation, image = newest
            try:
                t0 = time.monotonic()
                candidates = recognizer.recognize(image, setup[0].suggestions)
                # what was written is nobody's business, not even the log's: it may be a password
                log.debug("recognised %d readings (best: %d characters) in %.0f ms", len(candidates),
                          len(candidates[0][0]) if candidates else 0, (time.monotonic() - t0) * 1000)
            except Exception:
                log.exception("recognition failed")
                candidates = []
            self.loop.call_soon_threadsafe(self.on_result, generation, candidates)

    def _reconfigure(self, recognizer, old, new):
        """Returns the (possibly new) recogniser and the setup in effect."""
        try:
            lexicon = self._make_lexicon(new)
            if self._model_settings(new[0]) != self._model_settings(old[0]):
                recognizer = self._load_model(new[0], lexicon)
                log.info("model reloaded: %s", new[0].model_path())
            else:
                recognizer.lexicon = lexicon
            recognizer.set_letters(self._letters(new))
            return recognizer, new
        except Exception as e:  # noqa: BLE001 - the previous recogniser stays in use
            log.exception("could not apply the new settings")
            self.loop.call_soon_threadsafe(self.on_error, str(e), False)
            return recognizer, old

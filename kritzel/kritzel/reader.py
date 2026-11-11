"""The ink that waits in the writing area, and getting it read.

    pen down          begin()
    stroke finished   add()            (unless the panel took it for a gesture)
    pen up            end()            starts the pause
       a moment later                  the ink is read ahead; `pending` is the
                                       reading, which the panel shows in grey
       pause over                      on_reading(readings): the panel types
    a key is pressed  read_now()       ... the same without waiting

The recogniser works on another thread and answers later (on_result), by
which time the user may have written more.  Every change of the ink
therefore gets a new `generation`; an answer for an older one is dropped.

InkReader knows nothing about text fields or painting: the panel passes it
strokes and is told when there is something to type or to repaint.
"""

from __future__ import annotations

import time

from .config import load_state, save_state
from .ink import LineScale, ink_bounds, render_line
from .pace import Pace

PREVIEW_DELAY = 0.25        # the pen is lifted this long before the ink is read ahead
NOT_READY_RETRY = 0.2       # the model is still being loaded: ask again after this long
PAUSE_SAVED_STEP = 0.05     # a fitted pause is written down when it has moved this far


class InkReader:
    def __init__(self, loop, worker, cfg, hint, on_reading, on_change, remember_pause: bool = True):
        """worker: engine.RecognitionWorker, whose results must be passed to
            on_result()
        hint() -> recognizer.Hint: what the text field tells about the writing
        on_reading(readings): the ink is to be typed; best reading first,
            none if it could not be read.  The panel answers with done()
            once the ink has served its purpose.
        on_change(): `pending` or `waiting` changed, or ink went away."""
        self.loop, self.worker = loop, worker
        self.hint, self.on_reading, self.on_change = hint, on_reading, on_change
        self.remember_pause = remember_pause

        self.strokes: list = []         # finished strokes not yet typed
        self.generation = 0             # bumped whenever the ink changes
        self.pending = ""               # the best reading of the ink, before it is typed
        self.waiting = False            # the reading is to be typed as soon as it is there

        self._drawing = False           # the pen is down
        self._recognizing = False       # the recogniser is at work for us
        self._reading = None            # (generation, readings): what it made of the ink
        self._pen_lifted_at = 0.0       # when the last stroke of the waiting ink ended
        self._area = (0.0, 0.0)
        self.linescale = LineScale(*self._area)
        self.pace = Pace(cfg.delay_ms / 1000.0, load_state().get("pause"))
        self._saved_pause = self.pace.pause
        self.configure(cfg)

    def configure(self, cfg):
        self.preview = cfg.preview
        self.fitted_pause = cfg.delay_auto
        self.pace.configure(cfg.delay_ms / 1000.0)

    def fit(self, top: float, height: float):
        """The writing area is at `top` and `height` high.  If that is new,
        what was learnt about the user's line no longer applies."""
        if (top, height) != self._area and not self.strokes:
            self._area = (top, height)
            self.linescale.reset(top, height)

    @property
    def pause(self) -> float:
        """Seconds between the last stroke and recognition."""
        return self.pace.pause if self.fitted_pause else self.pace.configured

    # -- what the pen does ----------------------------------------------------

    def begin(self, erasing: bool = False):
        """The pen is set down in the writing area."""
        self._cancel_timers()
        self._ink_changed()
        self._drawing = True
        if self.strokes and not erasing:
            self.pace.note_break(time.monotonic() - self._pen_lifted_at)

    def add(self, stroke):
        self.strokes.append(stroke)
        self._pen_lifted_at = time.monotonic()

    def erase(self, x: float, y: float, radius: float) -> bool:
        """Rubs out the strokes near (x, y).  Returns True if there were any."""
        keep = [s for s in self.strokes if not s.near(x, y, radius)]
        erased = len(keep) != len(self.strokes)
        self.strokes = keep
        return erased

    def end(self):
        """The pen is lifted: recognise once the user has paused writing.
        With the preview on, the ink is read a moment after every stroke
        already, so that the reading can be shown and, if nothing more is
        written, typed without further waiting when the pause is over."""
        self._drawing = False
        if not self.strokes:
            self._cancel_timers()
            self.pending = ""
            self.on_change()
            return
        pause = self.pause
        self.loop.set_timer("recognize", pause, self.read_now)
        if self.preview and PREVIEW_DELAY < pause:
            self.loop.set_timer("preview", PREVIEW_DELAY, self._read_ahead)

    def clear(self):
        """Throws the ink away."""
        self._drawing = False
        self._forget_ink()
        self._cancel_timers()

    def done(self):
        """The ink has been typed.  A whole line of writing shows how tall
        the user writes."""
        bounds = ink_bounds(self.strokes)
        if bounds:
            x0, y0, x1, y1 = bounds
            self.linescale.learn(y0, y1, (x1 - x0) / max(1.0, y1 - y0))
        self._forget_ink()
        self._remember_pause()

    # -- reading ----------------------------------------------------------------

    def read_now(self):
        """The pause is over, or a key wants the waiting ink typed first."""
        if not self.strokes or self._drawing:
            return
        if not self.worker.ready:
            self.loop.set_timer("recognize", NOT_READY_RETRY, self.read_now)
            return
        self._cancel_timers()
        self.waiting = True
        if self._has_reading():
            self._deliver()
        else:
            self._request()
            self.on_change()

    def on_result(self, generation: int, candidates):
        """The recogniser's answer: [(text, confidence), ...], best first."""
        self._recognizing = False
        if generation != self.generation:
            self._on_outdated_result()
            return
        self._reading = (generation, [text for text, _confidence in candidates])
        if self.waiting:
            self._deliver()
        else:
            self.pending = self._reading[1][0] if self._reading[1] else ""
            self.on_change()

    def _on_outdated_result(self):
        """The ink changed while it was being read."""
        if not self.strokes:
            self.on_change()
        elif self._drawing:
            pass                            # lifting the pen schedules the next reading
        elif self.waiting:
            self._request()
        elif not self.loop.has_timer("recognize"):
            self.end()
        elif self.preview and not self.loop.has_timer("preview"):
            self._request()

    def _read_ahead(self):
        if self.worker.ready:
            self._request()

    def _has_reading(self) -> bool:
        return self._reading is not None and self._reading[0] == self.generation

    def _request(self):
        """Has the waiting ink read, unless that is done or under way."""
        if not self.strokes or self._drawing or self._recognizing or self._has_reading():
            return
        image = render_line(self.strokes, self.linescale)
        if image is None:
            return
        self._recognizing = True
        self.worker.submit(self.generation, image, self.hint())

    def _deliver(self):
        _generation, readings = self._reading
        self._reading, self.waiting, self.pending = None, False, ""
        self.on_reading(readings)

    # -- housekeeping -------------------------------------------------------------

    def _ink_changed(self):
        """Whatever was made of the ink no longer describes it."""
        self.generation += 1
        self._reading = None
        self.waiting = False

    def _forget_ink(self):
        self.strokes = []
        self.pending = ""
        self._ink_changed()

    def _cancel_timers(self):
        self.loop.cancel_timer("recognize")
        self.loop.cancel_timer("preview")

    def _remember_pause(self):
        """A fitted pause is the starting point after the next restart."""
        pause = self.pace.pause
        if self.fitted_pause and self.remember_pause and abs(pause - self._saved_pause) >= PAUSE_SAVED_STEP:
            self._saved_pause = pause
            save_state({**load_state(), "pause": round(pause, 2)})

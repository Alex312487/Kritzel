"""How long the panel waits after a stroke, fitted to the writer.

The pause before recognition has to be longer than the breaks the writer
makes inside a word - between two letters, before the dot on an i.  Pace
measures those breaks (the time from lifting the pen to setting it down
again while ink is waiting) and waits a good deal longer than nearly all of
them.  Somebody who writes slowly gets a longer pause and no more split
words; somebody who writes fast gets the text sooner.

Only the resulting pause is remembered across restarts, not the breaks.
"""

from __future__ import annotations

SAMPLES = 60                # breaks looked at: the most recent ones
MIN_SAMPLES = 20            # before that the configured pause is used
SHARE = 0.9                 # "nearly all" breaks
FACTOR = 2.5                # the pause is this much longer than nearly all breaks
LOWEST, HIGHEST = 0.6, 2.5  # limits, as multiples of the configured pause


class Pace:
    def __init__(self, configured: float, remembered=None):
        """configured: the `delay_ms` setting in seconds.  remembered: the
        pause found the last time, if any."""
        self.configured = configured
        self._breaks: list[float] = []
        self._pause = self._within_limits(remembered) if _is_time(remembered) else configured

    def _within_limits(self, pause: float) -> float:
        return max(LOWEST * self.configured, min(HIGHEST * self.configured, pause))

    def configure(self, configured: float):
        if configured != self.configured:
            self.configured = configured
            self._pause = self._within_limits(self._pause)

    def note_break(self, seconds: float):
        """The pen was lifted for `seconds` in the middle of writing."""
        if not _is_time(seconds):
            return
        self._breaks.append(seconds)
        del self._breaks[:-SAMPLES]
        if len(self._breaks) >= MIN_SAMPLES:
            ranked = sorted(self._breaks)
            usual_longest = ranked[min(len(ranked) - 1, int(SHARE * len(ranked)))]
            # move a quarter of the way: one odd break changes little
            self._pause += (self._within_limits(FACTOR * usual_longest) - self._pause) * 0.25

    @property
    def pause(self) -> float:
        """Seconds to wait after a stroke before the writing is recognised."""
        return self._pause


def _is_time(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool) and 0.0 < value < 60.0

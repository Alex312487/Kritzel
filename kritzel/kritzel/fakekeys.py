"""Key combinations: copy, cut and paste.

An input method can type text and press single keys (textfield.py), but the
input-method protocol has no way to hold Ctrl or Shift.  KWin offers its
input method a second door, the fake-input interface, through which key
presses enter exactly like those of a real keyboard.

The combinations are the classic ones on the Insert and Delete keys.
Unlike Ctrl+C / Ctrl+V they sit on the same keys in every keyboard layout,
and in a terminal they copy and paste instead of interrupting the running
program.
"""

from __future__ import annotations

import logging

log = logging.getLogger("kritzel.fakekeys")

# Linux key codes (linux/input-event-codes.h)
LEFT_CTRL, LEFT_SHIFT, INSERT, DELETE = 29, 42, 110, 111

SHORTCUTS = {
    "copy": (LEFT_CTRL, INSERT),
    "cut": (LEFT_SHIFT, DELETE),
    "paste": (LEFT_SHIFT, INSERT),
}
KEYBOARD_KEY_SINCE = 4      # version of the interface that has keyboard_key


class FakeKeyboard:
    def __init__(self, session):
        self.device = session.g.get("org_kde_kwin_fake_input")
        self._authenticated = False

    @property
    def available(self) -> bool:
        return self.device is not None and self.device.version >= KEYBOARD_KEY_SINCE

    def press(self, shortcut: str) -> bool:
        """Presses and releases the combination.  False if this compositor
        does not let us."""
        if not self.available:
            return False
        if not self._authenticated:
            self.device.authenticate("Kritzel", "copy, cut and paste keys of the handwriting panel")
            self._authenticated = True
        keys = SHORTCUTS[shortcut]
        for key in keys:
            self.device.keyboard_key(key, 1)
        for key in reversed(keys):
            self.device.keyboard_key(key, 0)
        log.debug("pressed %s", shortcut)
        return True

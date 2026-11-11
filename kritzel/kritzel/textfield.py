"""The text field that has the focus, and how text gets into it.

KWin describes the focused field through a zwp_input_method_context_v1
object: the application's text around the cursor comes in, committed text
and key presses go out.  TextField wraps that; PreviewField is the stand-in
used by the try-out window, which has no real text field.
"""

from __future__ import annotations

import logging
import shutil
import subprocess
import threading
import time

log = logging.getLogger("kritzel.textfield")

BACKSPACE = "backspace"
ENTER = "enter"
ARROWS = ("left", "up", "right", "down")
KEYSYMS = {BACKSPACE: 0xFF08, ENTER: 0xFF0D,         # XKB_KEY_BackSpace, XKB_KEY_Return
           "left": 0xFF51, "up": 0xFF52, "right": 0xFF53, "down": 0xFF54,
           "tab": 0xFF09, "delete": 0xFFFF, "home": 0xFF50, "end": 0xFF57}

# content purposes (text-input protocol)
DIGITS, NUMBER, PHONE, URL, EMAIL, PASSWORD, DATE, TIME, DATETIME, TERMINAL = 2, 3, 4, 5, 6, 8, 9, 10, 11, 12
# fields that hold no prose: no automatic spaces, no capitals
PLAIN_PURPOSES = {DIGITS, NUMBER, PHONE, URL, EMAIL, PASSWORD, DATE, TIME, DATETIME}
# fields for numbers, and what may be written there besides the digits
NUMERIC_PURPOSES = {DIGITS: "", NUMBER: "+-.,", PHONE: "+-()/#* ", DATE: "./- ", TIME: ":. ", DATETIME: "./-: "}
DIGIT_CHARACTERS = "0123456789"
HINT_HIDDEN_TEXT = 0x40

# Applications handle key presses later than input-method text (Qt queues
# them).  When a correction has to be made with Backspace keys, the new text
# is therefore held back for this long so that it cannot overtake them.
KEYS_FIRST_DELAY = 0.15


def last_word_length(before: str) -> int:
    """How many characters the 'word' button removes: the last word together
    with the white space after it; a punctuation mark counts as a word."""
    stripped = before.rstrip()
    i = len(stripped)
    if i and not stripped[i - 1].isalnum():
        i -= 1
    else:
        while i and stripped[i - 1].isalnum():
            i -= 1
    return max(1, len(before) - i)


class TextField:
    def __init__(self, loop, replace_with_keys: bool = False):
        self.loop = loop
        self.replace_with_keys = replace_with_keys
        self.on_state = lambda: None    # the application sent new state
        self.ctx = None
        self.serial = 0
        self._tail = ""                 # what we typed last; used when the app reports nothing
        self._reported = None           # text before the cursor as reported by the app
        self._reports_text = False      # the app has reported real text at least once
        self._held_back = None          # text waiting for Backspace keys to be handled
        self._describe(0, 0)

    def _describe(self, hint: int, purpose: int):
        """What kind of field it is, from the application's description."""
        self.secret = purpose == PASSWORD or bool(hint & HINT_HIDDEN_TEXT)  # what is written must not be shown
        self.plain = purpose in PLAIN_PURPOSES or self.secret       # the field holds no prose
        # ordinary text, from which words may be learnt (a terminal sees passwords, too)
        self.prose = not self.plain and purpose != TERMINAL
        marks = NUMERIC_PURPOSES.get(purpose)
        # the only characters that make sense in the field; None: any
        self.numeric = None if marks is None or self.secret else DIGIT_CHARACTERS + marks

    # -- focus -------------------------------------------------------------

    @property
    def active(self) -> bool:
        return self.ctx is not None

    def attach(self, ctx):
        """A text field got the focus."""
        self._drop_context()
        self.ctx = ctx
        self.serial = 0
        self._tail = ""
        self._reported = None
        self._reports_text = False
        self._describe(0, 0)
        ctx.dispatcher["content_type"] = self._on_content_type
        ctx.dispatcher["surrounding_text"] = self._on_surrounding_text
        ctx.dispatcher["commit_state"] = self._on_commit_state
        ctx.dispatcher["reset"] = self._on_reset

    def detach(self, ctx) -> bool:
        """Returns True if `ctx` was the current field."""
        current = ctx is self.ctx
        if current:
            self._drop_context()
        else:
            ctx.destroy()
        return current

    def _drop_context(self):
        self.loop.cancel_timer("held-back-text")
        self._held_back = None
        if self.ctx is not None:
            self.ctx.destroy()
            self.ctx = None

    # -- what the application tells us ------------------------------------

    def _on_surrounding_text(self, _ctx, text, cursor, _anchor):
        # `cursor` is a byte offset.  Applications without text-input support
        # always report "", which must not be mistaken for an empty field.
        text = text or ""                   # the wire format also allows "no string"
        if text:
            self._reports_text = True
        self._reported = text.encode("utf-8")[:cursor].decode("utf-8", "ignore") if self._reports_text else None
        self.on_state()

    def _on_content_type(self, _ctx, hint, purpose):
        self._describe(hint, purpose)
        self.on_state()

    def _on_commit_state(self, _ctx, serial):
        self.serial = serial
        self.on_state()

    def _on_reset(self, _ctx):
        self._tail = ""

    def reported_before_cursor(self):
        """Text before the cursor if the application currently tells us,
        else None."""
        return self._reported

    def before_cursor(self) -> str:
        """Best knowledge of the text before the cursor."""
        return self._reported if self._reported is not None else self._tail

    # -- what we send ------------------------------------------------------

    def commit(self, text: str):
        if not text:
            return
        self._flush_held_back()
        self._commit_now(text)

    def _commit_now(self, text: str):
        self._tail = (self._tail + text)[-80:]
        self._reported = None           # stale until the application reports back
        if self.ctx is not None:
            self.ctx.commit_string(self.serial, text)

    def key(self, name: str):
        """Press and release Backspace, Enter or an arrow key.

        Sent as key *symbols*, like Plasma's own keyboard does: KWin 6.8
        ignores raw key codes from an input method without a keyboard grab."""
        self._flush_held_back()
        self._key_now(name)

    def _key_now(self, name: str):
        if self.ctx is not None:
            t = int(time.monotonic() * 1000) & 0xFFFFFFFF
            self.ctx.keysym(self.serial, t, KEYSYMS[name], 1, 0)
            self.ctx.keysym(self.serial, t, KEYSYMS[name], 0, 0)
        if name == BACKSPACE:
            self._tail = self._tail[:-1]
        else:
            self._tail = ""
        self._reported = None

    def changed_behind_our_back(self):
        """Something we cannot follow changed the text (a paste, say): what
        we believed to be before the cursor is no longer true."""
        self._tail = ""
        self._reported = None

    def backspace(self, count: int):
        self._flush_held_back()
        for _ in range(count):
            self._key_now(BACKSPACE)

    def replace(self, old: str, new: str) -> bool:
        """Replaces `old`, which we inserted right before the cursor, by `new`.

        Deleting and inserting have to reach the application in that order.
        If the application reports that its text ends with `old`, both go
        through the text-input channel (delete_surrounding_text +
        commit_string), which keeps them in order.  Otherwise - terminals and
        the like report nothing, some applications report late - Backspace
        keys are sent and the new text is held back briefly, because
        applications handle keys later than text."""
        self._flush_held_back()
        if self.ctx is None:
            return False
        if self._reported is not None and self._reported.endswith(old) and not self.replace_with_keys:
            n = len(old.encode("utf-8"))        # the protocol counts bytes
            self.ctx.delete_surrounding_text(-n, n)
            self._tail = self._tail[: max(0, len(self._tail) - len(old))]
            self._commit_now(new)
            return True
        for _ in range(len(old)):
            self._key_now(BACKSPACE)
        self._held_back = new
        self.loop.set_timer("held-back-text", KEYS_FIRST_DELAY, self._flush_held_back)
        return True

    def _flush_held_back(self):
        self.loop.cancel_timer("held-back-text")
        text, self._held_back = self._held_back, None
        if text:
            self._commit_now(text)


def _feed(process, data: bytes):
    """Writes `data` to the standard input of `process` and waits for it."""
    try:
        process.communicate(data, timeout=5)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    except OSError:
        pass


class PreviewField:
    """Stand-in for the try-out window: collects the text, shows it in the
    panel and copies it to the clipboard."""

    replace_with_keys = False
    plain = False
    prose = True
    secret = False
    numeric = None
    active = True

    def __init__(self):
        self.text = ""
        self.on_state = lambda: None
        self._wl_copy = shutil.which("wl-copy")

    def reported_before_cursor(self):
        return self.text

    def before_cursor(self) -> str:
        return self.text

    def commit(self, text: str):
        self.text += text
        self._copy()

    def key(self, name: str):
        if name == BACKSPACE:
            self.text = self.text[:-1]
        elif name == ENTER:
            self.text = ""
        self._copy()                    # the other keys do nothing here

    def changed_behind_our_back(self):
        pass

    def backspace(self, count: int):
        self.text = self.text[: max(0, len(self.text) - count)]
        self._copy()

    def replace(self, old: str, new: str) -> bool:
        if not self.text.endswith(old):
            return False
        self.text = self.text[: len(self.text) - len(old)] + new
        self._copy()
        return True

    def _copy(self):
        """Puts the text on the clipboard.  wl-copy reads it from its
        standard input: on a command line every user of the computer could
        read it (ps), and a long text would not fit."""
        if not self.text or self._wl_copy is None:
            return
        try:
            wl_copy = subprocess.Popen([self._wl_copy, "--type", "text/plain"], stdin=subprocess.PIPE,
                                       stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError:
            return
        # hand the text over without making the panel wait for wl-copy
        threading.Thread(target=_feed, args=(wl_copy, self.text.encode("utf-8")), name="wl-copy", daemon=True).start()

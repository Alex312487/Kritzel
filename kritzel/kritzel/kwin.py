"""Runtime requests to KWin's virtual-keyboard D-Bus interface
(org.kde.kwin.VirtualKeyboard), made with busctl so that no D-Bus library
is needed.  Choosing *which* keyboard KWin runs is in switch.py."""

from __future__ import annotations

import shutil
import subprocess
import threading

_TARGET = ("org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard")
_FORCE_ACTIVATE = ("call", *_TARGET, "forceActivate")
ANSWER_TIMEOUT = 3          # seconds; a question the panel can do without
CALL_TIMEOUT = 30           # seconds; longer than busctl itself waits for KWin


def _busctl(*arguments: str):
    """The command line for `busctl --user ...`; None without busctl."""
    busctl = shutil.which("busctl")
    return None if busctl is None else [busctl, "--user", *arguments]


def _send(*arguments: str):
    """Runs busctl without waiting for it."""
    command = _busctl(*arguments)
    if command is None:
        return
    try:
        process = subprocess.Popen(command, stdin=subprocess.DEVNULL,
                                   stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        return
    # collect the exit status when it is there, so that no zombie is left behind
    threading.Thread(target=process.wait, name="busctl", daemon=True).start()


def deactivate_keyboard():
    """What Plasma's own keyboard toggle does: KWin hides the keyboard until
    a text field is tapped with pen or finger again."""
    _send("set-property", *_TARGET, "active", "b", "false")


def force_activate() -> tuple[int, str]:
    """Show the keyboard now, whatever the last input device was.  Returns
    (exit status, error message)."""
    command = _busctl(*_FORCE_ACTIVATE)
    if command is None:
        return 1, "busctl not found"
    try:
        r = subprocess.run(command, capture_output=True, text=True, timeout=CALL_TIMEOUT)
    except (OSError, subprocess.SubprocessError) as e:
        return 1, str(e)
    return r.returncode, r.stderr.strip()


def force_activate_soon():
    """force_activate() without waiting for the result."""
    _send(*_FORCE_ACTIVATE)


def ask_will_show(loop, callback, *args):
    """Asks KWin whether it would show a keyboard right now, that is whether
    the last thing the user did was done with a finger or the pen rather
    than mouse, touchpad or keyboard.  Calls callback(answer, *args) on the
    loop thread.  Without an answer: True."""
    _ask(loop, callback, args, ("call", *_TARGET, "willShowOnActive"), default=True)


def ask_visible(loop, callback, *args):
    """Asks KWin whether the docked panel is on screen at this moment and
    calls callback(answer, *args) on the loop thread.  Without an answer:
    True."""
    _ask(loop, callback, args, ("get-property", *_TARGET, "visible"), default=True)


def _ask(loop, callback, args, arguments, default: bool):
    command = _busctl(*arguments)
    if command is None:
        loop.call_soon_threadsafe(callback, default, *args)
        return

    def run():
        answer = default
        try:
            r = subprocess.run(command, capture_output=True, text=True, timeout=ANSWER_TIMEOUT)
            if r.returncode == 0:
                answer = "true" in r.stdout     # busctl prints "b true" or "b false"
        except (OSError, subprocess.SubprocessError):
            pass
        loop.call_soon_threadsafe(callback, answer, *args)

    threading.Thread(target=run, name="ask-kwin", daemon=True).start()

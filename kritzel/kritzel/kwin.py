"""Runtime requests to KWin's virtual-keyboard D-Bus interface
(org.kde.kwin.VirtualKeyboard), made with busctl so that no D-Bus library
is needed.  Choosing *which* keyboard KWin runs is in switch.py."""

from __future__ import annotations

import shutil
import subprocess
import threading

_TARGET = ["org.kde.KWin", "/VirtualKeyboard", "org.kde.kwin.VirtualKeyboard"]


def _busctl():
    return shutil.which("busctl")


def deactivate_keyboard():
    """What Plasma's own keyboard toggle does: KWin hides the keyboard until
    a text field is tapped with pen or finger again."""
    busctl = _busctl()
    if busctl is None:
        return
    try:
        subprocess.Popen([busctl, "--user", "set-property", *_TARGET, "active", "b", "false"],
                         stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except OSError:
        pass


def force_activate() -> tuple[int, str]:
    """Show the keyboard now, whatever the last input device was."""
    busctl = _busctl()
    if busctl is None:
        return 1, "busctl not found"
    r = subprocess.run([busctl, "--user", "call", *_TARGET, "forceActivate"], capture_output=True, text=True)
    return r.returncode, r.stderr.strip()


def ask_will_show(loop, callback, *args):
    """Asks KWin whether it would show a docked keyboard right now (the text
    field was tapped with pen or finger, not typed into) and calls
    callback(answer, *args) on the loop thread.  Without an answer: True."""
    busctl = _busctl()
    if busctl is None:
        loop.call_soon_threadsafe(callback, True, *args)
        return

    def run():
        answer = True
        try:
            r = subprocess.run([busctl, "--user", "call", *_TARGET, "willShowOnActive"],
                               capture_output=True, text=True, timeout=3)
            if r.returncode == 0:
                answer = "true" in r.stdout
        except (OSError, subprocess.SubprocessError):
            pass
        loop.call_soon_threadsafe(callback, answer, *args)

    threading.Thread(target=run, name="ask-kwin", daemon=True).start()

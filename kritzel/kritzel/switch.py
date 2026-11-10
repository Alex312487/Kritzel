"""Selecting Plasma's virtual keyboard (the InputMethod key in kwinrc)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys

from .config import STATE_DIR

DATA_HOME = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
DESKTOP_FILE = os.path.join(DATA_HOME, "applications", "kritzel.desktop")
PREVIOUS_FILE = os.path.join(STATE_DIR, "previous-inputmethod")
# keyboards to fall back to when none was selected before Kritzel
KNOWN_KEYBOARDS = ("org.kde.plasma.keyboard.desktop", "com.github.maliit.keyboard.desktop")


def tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def kread():
    t = tool("kreadconfig6", "kreadconfig5")
    if not t:
        return None
    r = subprocess.run([t, "--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod"],
                       capture_output=True, text=True)
    return r.stdout.strip()


def kwrite(value) -> bool:
    t = tool("kwriteconfig6", "kwriteconfig5")
    if not t:
        print("kwriteconfig6 not found - is this a Plasma session?", file=sys.stderr)
        return False
    cmd = [t, "--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod"]
    cmd += ["--delete"] if value is None else [value]
    if subprocess.run(cmd).returncode != 0:
        return False
    notify_kwin()
    return True


def notify_kwin():
    """Tell the running KWin that kwinrc changed, the same way System Settings
    does (KConfig's change notification on the session bus), so the virtual
    keyboard is switched immediately instead of at the next login."""
    busctl = tool("busctl")
    if not busctl:
        return
    key = b"InputMethod"
    cmd = [busctl, "--user", "emit", "/kwinrc", "org.kde.kconfig.notify", "ConfigChanged",
           "a{saay}", "1", "Wayland", "1", str(len(key))] + [str(b) for b in key]
    subprocess.run(cmd, capture_output=True)


def is_ours(path) -> bool:
    return bool(path) and os.path.basename(path) == "kritzel.desktop"


def previous_keyboard() -> str:
    """The keyboard that was selected before Kritzel ('' if none)."""
    try:
        with open(PREVIOUS_FILE, encoding="utf-8") as f:
            prev = f.read().strip()
    except OSError:
        return ""
    return prev if prev and os.path.isfile(prev) and not is_ours(prev) else ""


def other_keyboard() -> str:
    """The normal on-screen keyboard to switch to: the one used before
    Kritzel, otherwise an installed Plasma keyboard ('' if there is none)."""
    prev = previous_keyboard()
    if prev:
        return prev
    dirs = [DATA_HOME] + (os.environ.get("XDG_DATA_DIRS") or "/usr/local/share:/usr/share").split(":")
    for name in KNOWN_KEYBOARDS:
        for d in dirs:
            path = os.path.join(d, "applications", name)
            if d and os.path.isfile(path):
                return path
    return ""


def use_kritzel() -> tuple[bool, str]:
    if not os.path.isfile(DESKTOP_FILE):
        return False, f"{DESKTOP_FILE} is missing - run install.sh first."
    current = kread()
    if current is None:
        return False, "kreadconfig6 not found - is this a Plasma session?"
    if is_ours(current):
        return True, "Kritzel already is the Plasma virtual keyboard."
    os.makedirs(STATE_DIR, exist_ok=True)
    with open(PREVIOUS_FILE, "w", encoding="utf-8") as f:
        f.write(current + "\n")
    if not kwrite(DESKTOP_FILE):
        return False, "could not write kwinrc"
    msg = "Kritzel is now the Plasma virtual keyboard."
    if current:
        msg += f"\n(Previously: {current})"
    return True, msg


def use_other() -> tuple[bool, str]:
    """Switch from Kritzel to the normal on-screen keyboard."""
    target = other_keyboard()
    if not target:
        return False, "no other on-screen keyboard is installed (sudo dnf install plasma-keyboard)"
    if not kwrite(target):
        return False, "could not write kwinrc"
    return True, f"Virtual keyboard is now: {target}"


def restart() -> tuple[bool, str]:
    """Make KWin start the panel afresh (after an update or a settings change)."""
    import time

    if not is_ours(kread()):
        return False, "Kritzel is not the active virtual keyboard."
    if not kwrite(None):
        return False, "could not write kwinrc"
    time.sleep(0.6)
    if not kwrite(DESKTOP_FILE):
        return False, "could not write kwinrc"
    return True, "Kritzel restarted."

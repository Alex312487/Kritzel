"""Selecting Plasma's virtual keyboard (the InputMethod key in kwinrc)."""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
import time

from .config import STATE_DIR, write_atomically, xdg_dir

DATA_HOME = xdg_dir("XDG_DATA_HOME", "~/.local/share")
DESKTOP_FILE = os.path.join(DATA_HOME, "applications", "kritzel.desktop")
PREVIOUS_FILE = os.path.join(STATE_DIR, "previous-inputmethod")
# keyboards to fall back to when none was selected before Kritzel
KNOWN_KEYBOARDS = ("org.kde.plasma.keyboard.desktop", "com.github.maliit.keyboard.desktop")
_KWINRC_KEY = ("--file", "kwinrc", "--group", "Wayland", "--key", "InputMethod")
TOOL_TIMEOUT = 10           # seconds; the KDE tools answer at once or not at all


def tool(*names):
    for n in names:
        p = shutil.which(n)
        if p:
            return p
    return None


def kread():
    """The selected virtual keyboard: the path of its desktop file, "" if
    there is none, None if this is not a Plasma session."""
    t = tool("kreadconfig6", "kreadconfig5")
    if not t:
        return None
    try:
        r = subprocess.run([t, *_KWINRC_KEY], capture_output=True, text=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return None
    return r.stdout.strip()


def kwrite(value) -> bool:
    """Selects the virtual keyboard with the desktop file `value`; None for
    no keyboard."""
    t = tool("kwriteconfig6", "kwriteconfig5")
    if not t:
        print("kwriteconfig6 not found - is this a Plasma session?", file=sys.stderr)
        return False
    if value is not None and not is_desktop_file(value):
        # KWin runs the program this file names; and a value that starts
        # with a dash would be read as an option of kwriteconfig
        print(f"not a desktop file: {value!r}", file=sys.stderr)
        return False
    cmd = [t, *_KWINRC_KEY, *(["--delete"] if value is None else [value])]
    try:
        if subprocess.run(cmd, timeout=TOOL_TIMEOUT).returncode != 0:
            return False
    except (OSError, subprocess.SubprocessError):
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
    try:
        subprocess.run(cmd, capture_output=True, timeout=TOOL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        pass


def is_ours(path) -> bool:
    return bool(path) and os.path.basename(path) == "kritzel.desktop"


def is_desktop_file(path) -> bool:
    """An absolute path to an existing *.desktop file: the only thing that
    is ever written into kwinrc."""
    return bool(path) and os.path.isabs(path) and path.endswith(".desktop") and os.path.isfile(path)


def _remembered_keyboard() -> str:
    """What the file written by use_kritzel() names, if that still is a
    keyboard that can be selected ('' otherwise)."""
    try:
        with open(PREVIOUS_FILE, encoding="utf-8") as f:
            previous = f.read(4096).strip()
    except (OSError, UnicodeDecodeError):
        return ""
    return previous if is_desktop_file(previous) else ""


def previous_keyboard() -> str:
    """The keyboard that was selected before Kritzel ('' if none)."""
    previous = _remembered_keyboard()
    return "" if is_ours(previous) else previous


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
            if d and is_desktop_file(path):
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
    try:
        write_atomically(PREVIOUS_FILE, current + "\n")
    except OSError as e:
        return False, f"could not write {PREVIOUS_FILE}: {e}"
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


def restore_previous() -> tuple[bool, str]:
    """`kritzel disable`: exactly the setting from before Kritzel, which may
    be "no virtual keyboard"."""
    current = kread()
    if current is None:
        return False, "kreadconfig6 not found - is this a Plasma session?"
    if not is_ours(current):
        return True, "Kritzel is not the active virtual keyboard; nothing to do."
    previous = _remembered_keyboard()
    if not kwrite(previous or None):
        return False, "could not write kwinrc"
    return True, f"Virtual keyboard restored to: {previous or 'none'}"


def restart() -> tuple[bool, str]:
    """Make KWin start the panel afresh (after an update or a settings change)."""
    if not is_ours(kread()):
        return False, "Kritzel is not the active virtual keyboard."
    if not kwrite(None):
        return False, "could not write kwinrc"
    time.sleep(0.6)
    if not kwrite(DESKTOP_FILE):
        return False, "could not write kwinrc"
    return True, "Kritzel restarted."

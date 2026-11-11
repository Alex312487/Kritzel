"""Finding and signalling the running panel from another process
(`kritzel show`, `kritzel reload`, the settings window)."""

from __future__ import annotations

import os
import signal

from .config import STATE_DIR, ensure_private_dir

PANEL = "kritzel"
SETTINGS = "kritzel-settings"

original_qt_platform = None      # QT_QPA_PLATFORM before the panel process changed it for itself


def window_environment() -> dict:
    """Environment for a program with a normal window (the settings),
    started from the panel process."""
    env = dict(os.environ)
    env.pop("WAYLAND_SOCKET", None)             # that connection belongs to the panel alone
    if original_qt_platform:
        env["QT_QPA_PLATFORM"] = original_qt_platform
    else:
        env.pop("QT_QPA_PLATFORM", None)
    return env


def pid_file(name: str = PANEL) -> str:
    runtime_dir = os.environ.get("XDG_RUNTIME_DIR", "")
    return os.path.join(runtime_dir if os.path.isabs(runtime_dir) else STATE_DIR, name + ".pid")


def write_pid(name: str = PANEL):
    """Notes the process id of this process.  The file is written under
    another name and moved into place, so that a reader never finds it
    half-written and a symbolic link lying in its place is not followed."""
    path = pid_file(name)
    tmp = f"{path}.{os.getpid()}.tmp"
    try:
        ensure_private_dir(os.path.dirname(path))
        try:
            os.unlink(tmp)                      # left behind by a process that had our number before
        except OSError:
            pass
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW | os.O_CLOEXEC, 0o600)
        with os.fdopen(fd, "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
        os.replace(tmp, path)
    except OSError:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def remove_pid(name: str = PANEL):
    try:
        os.unlink(pid_file(name))
    except OSError:
        pass


def _is_kritzel(pid: int, name: str) -> bool:
    """Is process `pid` a Kritzel of ours (for SETTINGS: its settings
    window)?  The pid file may be stale and its number long since given to
    another program, which must not get our signals: SIGHUP and SIGUSR1
    end a program that does not expect them."""
    try:
        if os.stat(f"/proc/{pid}").st_uid != os.getuid():
            return False
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            arguments = f.read().split(b"\0")
    except OSError:
        return False
    # `python -m kritzel ...` or a launcher called kritzel - but not an
    # editor that merely has kritzel.log or ~/.config/kritzel/config.ini open
    if not any(os.path.basename(argument) == b"kritzel" for argument in arguments):
        return False
    return (b"settings" in arguments) == (name == SETTINGS)


def running_pid(name: str = PANEL):
    """Process id of the running panel (or settings window), else None."""
    try:
        with open(pid_file(name), encoding="ascii") as f:
            pid = int(f.read(32).strip())
    except (OSError, ValueError):
        return None
    if pid <= 1 or pid == os.getpid() or not _is_kritzel(pid, name):
        return None
    return pid


def signal_panel(signum: int) -> bool:
    pid = running_pid()
    if pid is None:
        return False
    try:
        os.kill(pid, signum)
    except OSError:
        return False
    return True


def show_panel() -> bool:
    return signal_panel(signal.SIGUSR1)


def reload_panel() -> bool:
    """Makes the running panel read the settings again."""
    return signal_panel(signal.SIGHUP)

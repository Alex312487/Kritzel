"""Finding and signalling the running panel from another process
(`kritzel show`, `kritzel reload`, the settings window)."""

from __future__ import annotations

import os
import signal

from .config import STATE_DIR


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


def pid_file(name: str = "kritzel") -> str:
    return os.path.join(os.environ.get("XDG_RUNTIME_DIR") or STATE_DIR, name + ".pid")


def write_pid(name: str = "kritzel"):
    try:
        with open(pid_file(name), "w", encoding="ascii") as f:
            f.write(str(os.getpid()))
    except OSError:
        pass


def running_pid(name: str = "kritzel"):
    """Process id of the running panel (or settings window), else None."""
    try:
        with open(pid_file(name), encoding="ascii") as f:
            pid = int(f.read().strip())
        with open(f"/proc/{pid}/cmdline", "rb") as f:
            command = f.read()
        # guard against a stale file whose process id now belongs to something else
        wanted = (b"kritzel", b"settings") if name.endswith("settings") else (b"kritzel",)
        if pid != os.getpid() and all(word in command for word in wanted):
            return pid
    except (OSError, ValueError):
        pass
    return None


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

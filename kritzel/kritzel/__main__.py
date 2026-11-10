"""Command line entry point.

    kritzel            run as KWin's input method (KWin starts it this way)
    kritzel try        open a normal window to try the recognition
    kritzel enable     make Kritzel the Plasma virtual keyboard
    kritzel keyboard   switch to the normal on-screen keyboard
    kritzel switch     toggle between the two
    kritzel disable    restore exactly the setting from before Kritzel
    kritzel settings   open the settings window
    kritzel reload     make the running panel read its settings again
    kritzel restart    restart the panel (after an update)
    kritzel status     show configuration and run a recognition self-test
    kritzel show       ask KWin to show the panel now (bind it to a shortcut)
"""

from __future__ import annotations

import argparse
import logging
import logging.handlers
import os
import signal
import sys

from .config import APP_DIR, CONFIG_FILE, STATE_DIR, Config

from . import control, kwin, switch
from .switch import PREVIOUS_FILE, is_ours as _is_ours, kread as _kread, kwrite as _kwrite

LOG_FILE = os.path.join(STATE_DIR, "kritzel.log")


def setup_logging(verbose: bool):
    os.makedirs(STATE_DIR, exist_ok=True)
    handlers: list[logging.Handler] = [logging.StreamHandler()]
    try:
        handlers.append(logging.handlers.RotatingFileHandler(LOG_FILE, maxBytes=200_000, backupCount=1))
    except OSError:
        pass
    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=handlers,
    )


def cmd_enable(_args):
    ok, msg = switch.use_kritzel()
    print(msg, file=sys.stdout if ok else sys.stderr)
    if ok:
        print("Tap a text field with the pen or a finger to bring up the panel.")
        print("`kritzel keyboard` (or the keyboard button on the panel) switches back.")
    return 0 if ok else 1


def cmd_keyboard(_args):
    ok, msg = switch.use_other()
    print(msg, file=sys.stdout if ok else sys.stderr)
    return 0 if ok else 1


def cmd_restart(_args):
    ok, msg = switch.restart()
    print(msg)
    return 0 if ok else 1


def cmd_switch(args):
    """Toggle between Kritzel and the normal on-screen keyboard."""
    return cmd_keyboard(args) if _is_ours(_kread()) else cmd_enable(args)


def cmd_disable(_args):
    current = _kread()
    if current is None:
        return 1
    if not _is_ours(current):
        print("Kritzel is not the active virtual keyboard; nothing to do.")
        return 0
    previous = ""
    try:
        with open(PREVIOUS_FILE, encoding="utf-8") as f:
            previous = f.read().strip()
    except OSError:
        pass
    if previous and not os.path.isfile(previous):
        previous = ""
    if not _kwrite(previous or None):
        return 1
    print(f"Virtual keyboard restored to: {previous or 'none'}")
    return 0


def cmd_show(_args):
    rc, error = kwin.force_activate()
    if rc != 0:
        print(error, file=sys.stderr)
    # KWin only knows about the docked panel; tell a floating one directly
    control.show_panel()
    return rc


def cmd_reload(_args):
    if control.reload_panel():
        print("Settings reloaded.")
        return 0
    print("The panel is not running (it starts with the settings in effect).")
    return 0


def cmd_settings(args):
    if control.running_pid("kritzel-settings") is not None:
        print("The settings window is already open.")
        return 0
    from .settings import run

    return run(args.page or "")


def self_test(cfg: Config) -> tuple[bool, str]:
    """Recognises a bundled pen sample; proves model + dependencies work."""
    import json
    import time

    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication

    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    from .ink import Stroke, render_line
    from .recognizer import Recognizer

    with open(os.path.join(APP_DIR, "data", "sample-strokes.json"), encoding="utf-8") as f:
        sample = json.load(f)
    strokes = []
    for pts in sample["strokes"]:
        s = Stroke()
        for x, y in pts:
            s.add(x, y)
        strokes.append(s)
    t0 = time.monotonic()
    rec = Recognizer(cfg.model_path(), cfg.threads, None, cfg.scripts)
    t1 = time.monotonic()
    res = rec.recognize(render_line(strokes))
    t2 = time.monotonic()
    got = res[0][0] if res else ""
    ok = got == sample["text"]
    msg = (f"recognised {got!r} (expected {sample['text']!r}); "
           f"model load {1000 * (t1 - t0):.0f} ms, recognition {1000 * (t2 - t1):.0f} ms")
    del app
    return ok, msg


def cmd_status(args):
    cfg = Config()
    current = _kread()
    print(f"install dir      : {APP_DIR}")
    print(f"config file      : {CONFIG_FILE}{'' if os.path.isfile(CONFIG_FILE) else ' (not present, defaults in use)'}")
    print(f"log file         : {LOG_FILE}")
    print(f"virtual keyboard : {current or 'none'}{'  <- Kritzel' if _is_ours(current) else ''}")
    print(f"session          : {os.environ.get('XDG_SESSION_TYPE', '?')} / {os.environ.get('XDG_CURRENT_DESKTOP', '?')}")
    print(f"model            : {cfg.model_path()}")
    missing = []
    for mod in ("numpy", "onnxruntime", "PyQt6.QtGui"):
        try:
            m = __import__(mod, fromlist=["x"])
            ver = getattr(m, "__version__", None) or getattr(m, "PYQT_VERSION_STR", "") or ""
            print(f"  {mod:<14} ok {ver}")
        except Exception as e:  # noqa: BLE001
            missing.append(mod)
            print(f"  {mod:<14} MISSING ({e})")
    if missing:
        return 1
    try:
        from .config import load_words
        from .lexicon import Lexicon

        lex = Lexicon(cfg.dictionary_languages(), cfg.dictionary_weight, load_words())
        print(f"dictionaries     : {', '.join(os.path.basename(p) for p in lex.loaded) or 'none found (optional)'}")
        print(f"personal words   : {len(lex.personal)}")
        ok, msg = self_test(cfg)
    except Exception as e:  # noqa: BLE001
        ok, msg = False, f"{type(e).__name__}: {e}"
    print(f"self-test        : {'ok' if ok else 'FAILED'} - {msg}")
    return 0 if ok else 1


def cmd_run(args, window_mode: bool):
    setup_logging(args.verbose)
    log = logging.getLogger("kritzel")
    # Qt is only used to paint into images; it must not open its own Wayland
    # connection (and must not swallow KWin's WAYLAND_SOCKET).
    control.original_qt_platform = os.environ.get("QT_QPA_PLATFORM")
    os.environ["QT_QPA_PLATFORM"] = "offscreen"
    from PyQt6.QtGui import QGuiApplication

    _app = QGuiApplication(sys.argv[:1])  # must stay alive: fonts and painting need it
    from .panel import NotInputMethod, Panel

    try:
        panel = Panel(Config(), window_mode=window_mode)
    except NotInputMethod:
        print(
            "The compositor did not offer the input-method interface.\n"
            "KWin only hands it to the program selected as virtual keyboard:\n"
            "  kritzel enable     make Kritzel the virtual keyboard\n"
            "  kritzel try        try the recognition in a normal window",
            file=sys.stderr,
        )
        return 2
    except Exception:
        log.exception("start-up failed")
        return 1
    log.info("kritzel started (%s)", "try-out window" if window_mode else "input method")
    if not window_mode:
        control.write_pid()
    try:
        panel.run()
    except KeyboardInterrupt:
        pass
    except Exception:
        log.exception("fatal error")
        if not window_mode:
            # KWin restarts a virtual keyboard that died from a signal (up to
            # five times), but not one that merely exited with an error.
            logging.shutdown()
            os.kill(os.getpid(), signal.SIGKILL)
        return 1
    return 0


def main(argv=None):
    ap = argparse.ArgumentParser(prog="kritzel", description="Handwriting input panel for KDE Plasma (Wayland).")
    ap.add_argument("-v", "--verbose", action="store_true", help="debug logging")
    ap.add_argument("command", nargs="?", default="run",
                    choices=["run", "try", "enable", "disable", "keyboard", "switch", "settings", "reload", "restart",
                             "status", "show"])
    ap.add_argument("page", nargs="?", help="for `settings`: the page to open, e.g. languages")
    args = ap.parse_args(argv)
    if args.command == "run":
        return cmd_run(args, window_mode=False)
    if args.command == "try":
        return cmd_run(args, window_mode=True)
    return {"enable": cmd_enable, "disable": cmd_disable, "keyboard": cmd_keyboard, "switch": cmd_switch, "restart": cmd_restart,
            "status": cmd_status, "show": cmd_show, "settings": cmd_settings, "reload": cmd_reload}[args.command](args)


if __name__ == "__main__":
    sys.exit(main())

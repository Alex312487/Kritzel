"""The handwriting panel itself: what happens when you write, tap a button
or pick a suggestion.

KWin starts the configured "virtual keyboard" and hands it a private Wayland
connection.  On it the program may bind zwp_input_method_v1 (learn about the
focused text field, put text into it) and zwp_input_panel_v1 (show a panel
that never takes the keyboard focus).  That is how Plasma Keyboard and Maliit
integrate too; Kritzel draws a writing area instead of keys.

Panel is the conductor.  The parts it conducts:

    compositor.Session      connection, globals, screens
    surface.PanelSurface    the window (docked / floating / try-out) and its pixels
    devices.InputDevices    mouse, touch and pen -> press / motion / release
    textfield.TextField     the focused text field
    engine.RecognitionWorker  handwriting -> text, on a background thread
    compose                 reading -> typed text (spaces, capitals)
    languages.LanguageChoice  the language being written
    fakekeys.FakeKeyboard   copy / cut / paste
    ui.View                 layout and painting; keys.KEYS says which keys exist
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import sys
import time

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainter

from . import control, kwin
from .compose import Context, compose, compose_symbol
from .compositor import NotInputMethod, Session
from .config import Config, Theme, load_state, load_words, save_state
from .devices import InputDevices, is_touch, source_kind
from .engine import RecognitionWorker
from .eventloop import Loop
from .fakekeys import FakeKeyboard
from .ink import LineScale, Stroke, ink_bounds, render_line
from .keys import COMMAND, KEY, KEYSYM, SHORTCUT, TEXT
from .languages import ALL, LanguageChoice
from .surface import PanelSurface
from .textfield import PreviewField, TextField, last_word_length
from .ui import ALL_LANGUAGES, LANGUAGE, LANGUAGES, MORE_LANGUAGES, SYMBOL, SYMBOLS, WRITE, View

__all__ = ["Panel", "NotInputMethod"]

log = logging.getLogger("kritzel")

ERASER_RADIUS = 9.0
REPEAT_DELAY = 0.45
REPEAT_INTERVAL = 0.07
SHOW_COMMAND_WINDOW = 1.0            # see show_now()
DISPLAY_CHECK_DELAY = 0.5            # see _insist_on_being_displayed()
FOCUS_THEFT_WINDOW = 0.15       # see _focus_was_stolen()
# KWin 5 reports touch positions like pointer positions (see PanelSurface.drag);
# only the test-suite, which runs against KWin 5, sets this.
KWIN5_TOUCH_DRAG = os.environ.get("KRITZEL_TOUCH_DRAG") == "relative"


class Insertion:
    """The text the panel typed last, so that it can be swapped or undone."""

    def __init__(self, span: str, own_length: int, context: Context, trailing_space: bool):
        self.span = span                    # the text before the cursor that a swap replaces
        self.own_length = own_length        # how much of it this insertion typed; the rest is
        #                                     the automatic space of the insertion before
        self.context = context              # the text field before it; another reading is
        #                                     composed for the same situation
        self.trailing_space = trailing_space    # it ends with an automatic space
        self.seen_in_field = False          # the application has reported it back


class Panel:
    def __init__(self, cfg: Config, window_mode: bool = False):
        self.cfg = cfg
        self.window_mode = window_mode
        self.loop = Loop()
        self.theme = Theme()
        self.view = View(self.theme, cfg)

        # -- what the view paints (see ui.View.paint) --
        self.strokes: list[Stroke] = []         # finished strokes not yet recognised
        self.candidates: list[str] = []         # suggestion chips
        self.selected = -1                      # index of the chip that is in the text field
        self.status = ""                        # short message at the right of the bar
        self.pressed = None                     # name of the key held down
        self.hint = "Write here"

        # -- ink and recognition --
        self.current: Stroke | None = None      # the stroke being drawn
        self._linescale_area = (self.view.canvas.top(), self.view.canvas.height())
        self.linescale = LineScale(*self._linescale_area)
        self.generation = 0                     # bumped whenever the ink changes
        self.recognizing = False
        self.last: Insertion | None = None
        self.after_recognition: list = []       # actions waiting for pending ink to be typed

        # -- gesture in progress: (source, kind, data); kind is one of
        #    "draw", "erase", "button", "chip", "move" --
        self.gesture = None

        self.languages = LanguageChoice(cfg)    # which dictionary judges the readings
        self.settings_window = None             # the settings program, while we started it

        # -- visibility --
        self.user_hidden = False                # the hide button was pressed
        self.float_allowed = False              # floating: the text field was tapped with finger or pen
        self.shown_by_command_at = -10.0        # `kritzel show`
        self.float_broken = False               # floating does not work with this compositor
        self.focus_thefts = 0
        self.deactivated_at = 0.0
        self.asking_kwin = False
        self.asked_kwin_at = 0.0

        self.session = Session(input_method=not window_mode)
        self.field = PreviewField() if window_mode else TextField(self.loop, cfg.replace == "keys")
        self.field.on_state = self._field_state_changed
        self.surface = PanelSurface(
            self.session, self.loop, cfg, window_mode,
            paint=self._paint, on_layout=self._on_layout, on_close=self.loop.stop,
            on_float_lost=self._on_float_lost, on_gone=self._on_surface_gone)
        self.devices = InputDevices(self.session, sink=self, writing_area=self.view.in_canvas, cfg=cfg)
        self.fake_keyboard = FakeKeyboard(self.session)
        self.worker = RecognitionWorker(cfg, self.loop, self._on_model_ready, self._on_model_error, self._on_result,
                                        languages=self.languages.active(), personal=load_words())
        self._restore_floating()
        if not window_mode:
            im = self.session.g["zwp_input_method_v1"]
            im.dispatcher["activate"] = self._on_activate
            im.dispatcher["deactivate"] = self._on_deactivate
        self._update_visibility()

    @property
    def preview(self):
        """Try-out window: the text that would have been typed."""
        return self.field.text if self.window_mode else None

    # ------------------------------------------------------------------
    # Running
    # ------------------------------------------------------------------

    def run(self):
        conn = self.session.conn

        def on_readable():
            if not conn.read():
                log.info("compositor closed the connection")
                return False
            conn.dispatch()

        def before_sleeping():
            conn.dispatch()
            self.surface.flush_drag()
            conn.flush()

        signal.signal(signal.SIGUSR1, lambda *_: self.loop.call_soon_threadsafe(self.show_now))
        signal.signal(signal.SIGHUP, lambda *_: self.loop.call_soon_threadsafe(self.reload_settings))
        self.loop.add_reader(conn.fileno(), on_readable)
        self.loop.add_idle(before_sleeping)
        self.loop.run()

    def show_now(self):
        """`kritzel show`: the command has also asked KWin to activate the
        keyboard, which may reach us before or after this."""
        self.user_hidden = False
        self.float_allowed = True
        self.shown_by_command_at = time.monotonic()
        self._update_visibility()
        self._insist_on_being_displayed()

    def reload_settings(self):
        """`kritzel reload`, or Apply in the settings window."""
        log.info("reloading settings")
        self.cfg = cfg = Config()
        self.field.replace_with_keys = cfg.replace == "keys"
        self.view.configure(cfg)
        self._show_page(WRITE)
        self.devices.configure(cfg)
        self.languages.update(cfg)
        self.worker.configure(cfg, self.languages.active(), load_words())
        if self.worker.ready:
            self.status = ""
        self.surface.apply_config(cfg)
        if self.surface.floating and not self.view.has_key("float"):
            self._toggle_float()            # the key that would dock it again is gone
        self._update_visibility()
        self.redraw()

    # ------------------------------------------------------------------
    # Painting
    # ------------------------------------------------------------------

    def redraw(self):
        self.surface.redraw()

    def _paint(self, p: QPainter):
        self.view.paint(p, self)
        if self.current is not None:
            p.setPen(self.view.ink_pen())
            p.setClipRect(self.view.canvas)
            p.drawPath(self.current.path())

    def _on_layout(self, w: int, h: int):
        self.view.layout(w, h)
        self._fit_linescale()

    def _fit_linescale(self):
        """The writing area moved or changed its height: what was learnt
        about the user's line no longer applies."""
        area = (self.view.canvas.top(), self.view.canvas.height())
        if self.view.page == WRITE and area != self._linescale_area and not self.strokes:
            self._linescale_area = area
            self.linescale.reset(*area)

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def _update_visibility(self):
        """The panel is for fingers and pens: it appears when a text field
        is tapped, not when one is clicked with the mouse or typed into.

        For the docked panel KWin applies that rule itself; mapping the
        surface only means "ready to be displayed" (but see
        PanelSurface._create_dock).  A floating panel is an ordinary
        overlay as far as KWin is concerned, so it has to ask KWin what the
        user did last."""
        if self.window_mode:
            visible = True
        elif not self.field.active or self.user_hidden:
            visible = False
        elif self.surface.floating:
            visible = self.float_allowed or self.cfg.show == "always"
        else:
            visible = True
        self.surface.set_visible(visible)

    def _on_activate(self, _im, ctx):
        self.field.attach(ctx)
        self.user_hidden = False
        self._forget_last()
        if self.theme.changed():
            self.theme.reload()
        by_command = time.monotonic() - self.shown_by_command_at < SHOW_COMMAND_WINDOW
        if self.surface.floating:
            self.float_allowed = by_command
            self._ask_kwin()
        self._update_visibility()
        if by_command:
            self._insist_on_being_displayed()
        elif self.cfg.show == "always":
            kwin.force_activate_soon()
            self._insist_on_being_displayed()
        self.redraw()
        log.debug("text field activated")

    def _on_deactivate(self, _im, ctx):
        if self.field.detach(ctx):
            self.deactivated_at = time.monotonic()
            self._clear_ink()
            self._forget_last()
            self._show_page(WRITE)
            self.cancel()
            self._update_visibility()
        log.debug("text field deactivated")

    def _insist_on_being_displayed(self):
        """For `kritzel show` and the "always" setting, where the panel is
        wanted although the text field may have been reached with mouse or
        keyboard.  KWin has been asked to show the keyboard regardless, but
        only newer versions (6.7?) then display a docked panel.  If this one
        does not, the panel floats instead: a floating panel is ours to show."""
        self.loop.set_timer("display-check", DISPLAY_CHECK_DELAY, self._check_displayed)

    def _docked_and_mapped(self) -> bool:
        return not self.window_mode and not self.surface.floating and self.surface.mapped and self.field.active

    def _check_displayed(self):
        if self._docked_and_mapped():
            kwin.ask_visible(self.loop, self._on_displayed_answer, self.field.ctx)

    def _on_displayed_answer(self, displayed: bool, ctx):
        if displayed or ctx is not self.field.ctx or not self._docked_and_mapped():
            return
        if self.float_broken or not self.surface.can_float:
            return
        log.info("KWin does not display the docked panel after mouse or keyboard input; floating instead")
        self._toggle_float()
        self.redraw()

    def _hide(self):
        """Hide button.  The surface is unmapped at once, and KWin is told to
        deactivate its virtual keyboard, as Plasma's own keyboard toggle does.
        KWin then brings the panel back by its normal rules: when a text
        field is tapped with pen or finger, not while typing on a keyboard."""
        self._clear_ink()
        self.user_hidden = True
        self._update_visibility()
        kwin.deactivate_keyboard()

    def _ask_kwin(self):
        if not self.asking_kwin:
            self.asking_kwin = True
            self.asked_kwin_at = time.monotonic()
            kwin.ask_will_show(self.loop, self._on_kwin_answer, self.field.ctx)

    def _on_kwin_answer(self, will_show: bool, ctx):
        self.asking_kwin = False
        if will_show and ctx is self.field.ctx and not self.float_allowed:
            self.float_allowed = True
            self._update_visibility()

    def _field_state_changed(self):
        self._check_last_insertion()
        # The application re-sent its state, which it also does when the
        # field is tapped again: a floating panel that is not shown asks anew.
        if (self.surface.floating and self.field.active and not self.float_allowed and not self.user_hidden
                and self.cfg.show != "always"
                and time.monotonic() - self.asked_kwin_at > 0.3):
            self._ask_kwin()

    # -- floating ------------------------------------------------------

    def _toggle_float(self):
        if self.window_mode:
            self.status = "only in the real panel"
            return
        if not self.surface.floating and (self.float_broken or not self.surface.can_float):
            self.status = "floating is not supported here"
            return
        self._clear_ink()
        floating = not self.surface.floating
        self.view.floating = floating
        self.float_allowed = True           # the panel is on screen right now
        self.surface.set_floating(floating)
        self._remember_floating()
        if not floating:
            self._show_docked_again()
            self._insist_on_being_displayed()   # "dock" clicked with the mouse

    def _remember_floating(self):
        if self.cfg.remember_floating and not self.window_mode:
            pos = self.surface.float_pos
            save_state({**load_state(), "floating": self.surface.floating, "float_pos": list(pos) if pos else None})

    def _restore_floating(self):
        """Setting "remember floating": start the way the panel was left."""
        state = load_state()
        if not (self.cfg.remember_floating and state.get("floating") and self.surface.can_float
                and self.view.has_key("float") and not self.window_mode):
            return
        pos = state.get("float_pos")
        if isinstance(pos, list) and len(pos) == 2 and all(isinstance(v, (int, float)) for v in pos):
            self.surface.float_pos = (int(pos[0]), int(pos[1]))
        self.view.floating = True
        self.surface.set_floating(True)

    def _show_docked_again(self):
        """The panel was floating and visible; the docked surface that
        replaces it is new to KWin, which would only display it after the
        next tap on a text field."""
        if self.field.active and not self.user_hidden:
            kwin.force_activate_soon()

    def _on_float_lost(self, _reason: str):
        self._clear_ink()
        self.view.floating = False
        self.status = "floating failed – docked again"
        self._remember_floating()
        self._show_docked_again()
        self.redraw()

    def _on_surface_gone(self):
        self.devices.forget_pen()
        self.cancel()

    def _focus_was_stolen(self) -> bool:
        """The text field lost the focus at the very moment the floating
        panel was touched: this compositor (KWin 5) gives the focus to the
        panel.  If that happens twice, floating is given up for this session
        rather than letting the panel flicker."""
        if not self.surface.floating or time.monotonic() - self.deactivated_at >= FOCUS_THEFT_WINDOW:
            return False
        self.focus_thefts += 1
        if self.focus_thefts >= 2:
            self.float_broken = True
            log.warning("touching the floating panel takes the focus away from the text field")
            self.view.floating = False
            self.surface.set_floating(False)
            self._on_float_lost("focus")
        return True

    # ------------------------------------------------------------------
    # Gestures: called by devices.InputDevices for mouse, finger and pen
    # ------------------------------------------------------------------

    def press(self, source, x, y, eraser=False):
        if self._focus_was_stolen() or not self.surface.mapped:
            return
        log.debug("press %s at %.0f,%.0f", source_kind(source), x, y)
        if self.gesture is not None:
            return                          # one finger / pen at a time
        key = self.view.button_at(x, y)
        if key:
            self.gesture = (source, "button", key)
            self.pressed = key
            if self._acts_on_press(key):
                self._do_key(key)
                if self.cfg.key_repeat:
                    self.loop.set_timer("repeat", REPEAT_DELAY, self._repeat_key)
            self.redraw()
            return
        chip = self.view.chip_at(x, y)
        if chip:
            self.gesture = (source, "chip", chip.index)
            return
        if self.surface.floating and self.view.in_handle(x, y):
            self.gesture = (source, "move", (x, y))
            self.surface.begin_drag()
            return
        if not self.view.in_canvas(x, y):
            return
        self.loop.cancel_timer("recognize")
        self.generation += 1
        if eraser:
            self.gesture = (source, "erase", None)
            self._erase(x, y)
            return
        self._start_worker()
        self.current = Stroke()
        self.current.add(x, y)
        self.gesture = (source, "draw", None)
        self.redraw()

    def motion(self, source, x, y):
        if self.gesture is None or self.gesture[0] != source:
            return
        _, kind, data = self.gesture
        if kind == "draw":
            prev = self.current.points[-1]
            if self.current.add(x, y):
                self.surface.draw_directly(lambda p: self._paint_segment(p, prev, (x, y)))
        elif kind == "erase":
            self._erase(x, y)
        elif kind == "move":
            self.surface.drag(x - data[0], y - data[1], relative_to_origin=self._touch_keeps_origin(source))
        elif kind == "button":
            inside = self.view.button_at(x, y) == data
            if (data if inside else None) != self.pressed:
                self.pressed = data if inside else None
                if not inside:
                    self.loop.cancel_timer("repeat")
                self.redraw()

    def release(self, source):
        if self.gesture is None or self.gesture[0] != source:
            return
        _, kind, data = self.gesture
        self.gesture = None
        if kind == "draw":
            stroke, self.current = self.current, None
            if stroke and stroke.points:
                self.strokes.append(stroke)
            self.redraw()
            self._schedule_recognition()
        elif kind == "erase":
            self._schedule_recognition()
        elif kind == "move":
            self.surface.end_drag()
            self._remember_floating()
        elif kind == "button":
            self.loop.cancel_timer("repeat")
            hit = self.pressed == data
            self.pressed = None
            if hit and not self._acts_on_press(data):
                self._do_key(data)
            self.redraw()
        elif kind == "chip":
            chip = self.view.chip_at(*self.devices.position_of(source))
            if chip is not None and chip.index == data:
                self._choose(data)

    def cancel(self):
        """Abandon the gesture in progress (touch cancelled, panel hidden)."""
        self.gesture = None
        self.current = None
        self.pressed = None
        self.loop.cancel_timer("repeat")
        self.redraw()

    @staticmethod
    def _touch_keeps_origin(source) -> bool:
        return is_touch(source) and not KWIN5_TOUCH_DRAG

    def _paint_segment(self, p: QPainter, a, b):
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setClipRect(self.view.canvas)
        p.setPen(self.view.ink_pen())
        p.drawLine(QPointF(*a), QPointF(*b))

    def _erase(self, x, y):
        keep = [s for s in self.strokes if not s.near(x, y, ERASER_RADIUS)]
        if len(keep) != len(self.strokes):
            self.strokes = keep
            self.redraw()

    @staticmethod
    def _acts_on_press(name: str) -> bool:
        """Backspace and the like act when pressed (and repeat while held);
        all other keys act when released on them."""
        return name in KEY and KEY[name].repeats

    def _repeat_key(self):
        if self.gesture and self.gesture[1] == "button" and self.pressed == self.gesture[2]:
            self._do_key(self.pressed)
            self.loop.set_timer("repeat", REPEAT_INTERVAL, self._repeat_key)

    # ------------------------------------------------------------------
    # Keys
    # ------------------------------------------------------------------

    def _do_key(self, name: str):
        """A key was pressed: one of keys.KEYS, a symbol or a language."""
        if name.startswith(LANGUAGE):
            self._choose_language(name[len(LANGUAGE):])
            self.redraw()
            return
        kind, argument = (TEXT, name[len(SYMBOL):]) if name.startswith(SYMBOL) else KEY[name].action
        # whatever a key types comes after the ink that is still waiting
        if (kind != COMMAND or argument == "symbols") and self._type_pending_ink_then(lambda: self._do_key(name)):
            return
        if kind == KEYSYM:
            self.field.key(argument)
            self._forget_last()
        elif kind == SHORTCUT:
            self._press_shortcut(argument)
        elif kind == TEXT and name == "space":
            if not self._context().space_pending:   # the automatic space is the space
                self.field.commit(" ")
            self._forget_last()
        elif kind == TEXT:
            context = self._context()
            self._type(compose_symbol(argument, context, self._auto_space()), context)
            self._forget_last()
            if self.view.page == SYMBOLS and not self.cfg.symbols_stay:
                self._show_page(WRITE)
        else:
            self._commands[argument]()
        self.redraw()

    @property
    def _commands(self) -> dict:
        return {"delete_word": self._delete_word, "language": self._language_key, "symbols": self._symbols_key,
                "keyboard": self._switch_keyboard, "float": self._toggle_float, "settings": self._open_settings,
                "hide": self._hide_key}

    def _press_shortcut(self, name: str):
        """Copy, cut, paste."""
        if self.window_mode:
            self.status = "only in the real panel"
        elif not self.fake_keyboard.press(name):
            self.status = f"{name} is not possible with this KWin"
        elif name != "copy":
            self.field.changed_behind_our_back()
            self._forget_last()

    def _hide_key(self):
        if self.window_mode:
            self.loop.stop()
        else:
            self._hide()

    # -- pages: what the area below the suggestions shows ----------------

    def _show_page(self, page: str):
        tiles = []
        if page == LANGUAGES:
            lang = self.languages
            tiles = [(LANGUAGE + (ALL_LANGUAGES if choice is ALL else choice), lang.title(choice),
                      choice == lang.current or len(lang.codes) < 2) for choice in lang.choices()]
            tiles.append((LANGUAGE + MORE_LANGUAGES, "More languages…", False))
        self.view.show_page(page, tiles)
        self._fit_linescale()

    def _symbols_key(self):
        """Swaps the writing area for the symbol keyboard and back."""
        self._show_page(WRITE if self.view.page == SYMBOLS else SYMBOLS)

    def _language_key(self):
        if self.cfg.language_button == "next" and self.languages.can_switch():
            self.languages.next()
            self._language_changed()
        else:
            self._show_page(WRITE if self.view.page == LANGUAGES else LANGUAGES)

    def _choose_language(self, choice: str):
        """A tile of the language page was tapped."""
        self._show_page(WRITE)
        if choice == MORE_LANGUAGES:
            self._open_settings("languages")
            return
        self.languages.select(ALL if choice == ALL_LANGUAGES else choice)
        self._language_changed()

    def _language_changed(self):
        self.worker.configure(self.cfg, self.languages.active(), load_words())
        self._forget_last()

    def _delete_word(self):
        """Ink that is not typed yet is thrown away; otherwise the text that
        was just inserted is removed, otherwise the word before the cursor."""
        if self.strokes or self.current is not None:
            self._clear_ink()
            return
        if self.last is not None:
            count = self.last.own_length
        else:
            count = last_word_length(self.field.before_cursor())
        self._forget_last()
        self.field.backspace(count)

    def _switch_keyboard(self):
        """Hand over to the normal on-screen keyboard.  KWin stops this
        process as soon as the setting has changed."""
        if self.window_mode:
            self.status = "only in the real panel"
            return
        from . import switch

        try:
            ok, msg = switch.use_other()
        except Exception as e:  # noqa: BLE001 - shown in the panel
            ok, msg = False, str(e)
        log.info("switch keyboard: %s", msg)
        if not ok:
            self.status = "no other on-screen keyboard installed" if "installed" in msg else "switching failed"

    def _open_settings(self, page: str = ""):
        """The settings window is a program of its own (`kritzel settings`)."""
        if self.settings_window is not None and self.settings_window.poll() is None:
            return                              # still open (or a second tap on the button)
        safe_path = ["-P"] if getattr(sys.flags, "safe_path", False) else []
        command = [sys.executable, *safe_path, "-m", "kritzel", "settings", *([page] if page else [])]
        try:
            self.settings_window = subprocess.Popen(
                command, env=control.window_environment(), start_new_session=True,
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except OSError as e:
            log.warning("could not open the settings: %s", e)
            self.status = "could not open the settings"

    @property
    def language_label(self) -> str:
        return self.languages.label

    # ------------------------------------------------------------------
    # Ink -> text
    # ------------------------------------------------------------------

    def _start_worker(self):
        if not self.worker.started:
            self.status = "loading…"
            self.worker.start()

    def _on_model_ready(self):
        self.status = ""
        self.redraw()

    def _on_model_error(self, _message: str, fatal: bool):
        self.status = ("recognition failed to start" if fatal else "settings not applied") + " – see `kritzel status`"
        self.redraw()

    def _schedule_recognition(self):
        """Recognise once the user has paused writing."""
        if self.strokes:
            self.loop.set_timer("recognize", self.cfg.delay_ms / 1000.0, self._recognize)
        else:
            self.loop.cancel_timer("recognize")
            if self.worker.ready:
                self.status = ""
            self.redraw()

    def _recognize(self):
        if not self.strokes or (self.gesture and self.gesture[1] in ("draw", "erase")):
            return
        if not self.worker.ready:
            self.loop.set_timer("recognize", 0.2, self._recognize)
            return
        image = render_line(self.strokes, self.linescale)
        if image is None:
            return
        self.recognizing = True
        self.status = "…"
        self.worker.submit(self.generation, image)
        self.redraw()

    def _type_pending_ink_then(self, action) -> bool:
        """If ink is still waiting to be recognised, recognise it now and run
        `action` afterwards.  Returns True when the action was deferred."""
        if not self.strokes or not self.cfg.auto_insert:
            return False
        self.after_recognition.append(action)
        self.loop.cancel_timer("recognize")
        if not self.recognizing:
            self._recognize()
        return True

    def _on_result(self, generation: int, candidates):
        self.recognizing = False
        if generation != self.generation:
            # the ink changed while it was being recognised
            drawing = self.gesture is not None
            if self.after_recognition and self.strokes and not drawing:
                self._recognize()
            elif self.strokes and not drawing and not self.loop.has_timer("recognize"):
                self._schedule_recognition()
            elif not self.strokes:
                self.status = ""
                self.redraw()
            return
        self.status = ""
        texts = [text for text, _confidence in candidates]
        waiting, self.after_recognition = self.after_recognition, []
        if not texts:
            self.status = "not recognised"
            self._ink_done()
            self._forget_last()
        elif self.cfg.auto_insert:
            self.candidates = texts
            self._insert(0)
            self._ink_done()
        else:
            # manual mode: nothing is typed until a suggestion is tapped
            self.candidates, self.selected, self.last = texts, -1, None
        for action in waiting:
            action()
        self.redraw()

    def _ink_done(self):
        bounds = ink_bounds(self.strokes)
        if bounds:
            x0, y0, x1, y1 = bounds
            self.linescale.learn(y0, y1, (x1 - x0) / max(1.0, y1 - y0))
        self.strokes = []
        self.generation += 1

    def _clear_ink(self):
        self.strokes = []
        self.current = None
        self.generation += 1
        self.after_recognition.clear()
        self.loop.cancel_timer("recognize")
        if self.worker.ready:
            self.status = ""

    # ------------------------------------------------------------------
    # Suggestions
    # ------------------------------------------------------------------

    def _context(self) -> Context:
        """The text field as the next insertion finds it."""
        before = self.field.before_cursor()
        pending = self.last is not None and self.last.trailing_space and before.endswith(" ")
        return Context(before, self.field.reported_before_cursor() is not None, pending)

    def _auto_space(self) -> str:
        # no automatic spaces in addresses, numbers and passwords
        return "off" if self.field.plain else self.cfg.auto_space

    def _compose(self, reading: str, context: Context):
        return compose(reading, context, self._auto_space(), self.cfg.capitalize and not self.field.plain)

    def _type(self, typed, context: Context) -> str:
        """Puts composed text into the field.  Returns the span that a later
        swap has to replace."""
        if typed.eats_space:
            self.field.replace(" ", typed.text)
            return typed.text
        self.field.commit(typed.text)
        return (" " if context.space_pending else "") + typed.text

    def _insert(self, index: int):
        context = self._context()
        typed = self._compose(self.candidates[index], context)
        span = self._type(typed, context)
        self.last = Insertion(span, len(typed.text), context, typed.trailing_space)
        self.selected = index

    def _choose(self, index: int):
        """A suggestion chip was tapped."""
        if not 0 <= index < len(self.candidates):
            return
        if self.last is None:
            self._insert(index)             # manual mode
            self._ink_done()
        elif index != self.selected:
            # the other reading, composed for the situation the first one met
            context = self.last.context
            typed = self._compose(self.candidates[index], context)
            keeps_space = context.space_pending and not typed.eats_space
            span = (" " if keeps_space else "") + typed.text
            if self.field.replace(self.last.span, span):
                self.last = Insertion(span, len(typed.text), context, typed.trailing_space)
                self.selected = index
        self.redraw()

    def _check_last_insertion(self):
        """Once the application has shown us our insertion in its text and
        later reports text that no longer ends with it, the user has edited
        or moved the cursor: the suggestions no longer apply."""
        reported = self.field.reported_before_cursor()
        if self.last is None or reported is None:
            return
        if reported.endswith(self.last.span):
            self.last.seen_in_field = True
        elif self.last.seen_in_field:
            self._forget_last()
            self.redraw()

    def _forget_last(self):
        self.last = None
        self.candidates, self.selected = [], -1

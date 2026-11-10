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
    ui.View                 layout and painting
"""

from __future__ import annotations

import logging
import os
import signal
import time

from PyQt6.QtCore import QPointF
from PyQt6.QtGui import QPainter

from . import kwin
from .compositor import NotInputMethod, Session
from .config import Config, Theme
from .devices import InputDevices, is_touch, source_kind
from .engine import RecognitionWorker
from .eventloop import Loop
from .ink import LineScale, Stroke, ink_bounds, render_line
from .surface import PanelSurface
from .textfield import BACKSPACE, ENTER, PreviewField, TextField, last_word_length, needs_space
from .ui import View

__all__ = ["Panel", "NotInputMethod"]

log = logging.getLogger("kritzel")

ERASER_RADIUS = 9.0
BACKSPACE_REPEAT_DELAY = 0.45
BACKSPACE_REPEAT_INTERVAL = 0.07
FOCUS_THEFT_WINDOW = 0.15       # see _focus_was_stolen()
# KWin 5 reports touch positions like pointer positions (see PanelSurface.drag);
# only the test-suite, which runs against KWin 5, sets this.
KWIN5_TOUCH_DRAG = os.environ.get("KRITZEL_TOUCH_DRAG") == "relative"


class Insertion:
    """The text the panel typed last, so that it can be swapped or undone."""

    def __init__(self, text: str, prefix: str):
        self.text = text            # everything that was typed, including the prefix
        self.prefix = prefix        # the automatic space in front, or ""
        self.seen_in_field = False  # the application has reported it back


class Panel:
    def __init__(self, cfg: Config, window_mode: bool = False):
        self.cfg = cfg
        self.window_mode = window_mode
        self.loop = Loop()
        self.theme = Theme()
        self.view = View(self.theme, cfg.pen_width)

        # -- what the view paints (see ui.View.paint) --
        self.strokes: list[Stroke] = []         # finished strokes not yet recognised
        self.candidates: list[str] = []         # suggestion chips
        self.selected = -1                      # index of the chip that is in the text field
        self.status = ""                        # short message at the right of the bar
        self.pressed = None                     # name of the button held down
        self.hint = "Write here"

        # -- ink and recognition --
        self.current: Stroke | None = None      # the stroke being drawn
        self.linescale = LineScale(cfg.height)
        self.generation = 0                     # bumped whenever the ink changes
        self.recognizing = False
        self.last: Insertion | None = None
        self.after_recognition: list = []       # actions waiting for pending ink to be typed

        # -- gesture in progress: (source, kind, data); kind is one of
        #    "draw", "erase", "button", "chip", "move" --
        self.gesture = None

        # -- visibility --
        self.user_hidden = False                # the hide button was pressed
        self.float_allowed = False              # floating: KWin would show a keyboard now
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
        self.devices = InputDevices(self.session, sink=self, palm_zone=self.view.in_canvas)
        self.worker = RecognitionWorker(cfg, self.loop, self._on_model_ready, self._on_model_error, self._on_result)
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
        self.loop.add_reader(conn.fileno(), on_readable)
        self.loop.add_idle(before_sleeping)
        self.loop.run()

    def show_now(self):
        """`kritzel show`."""
        self.user_hidden = False
        self.float_allowed = True
        self._update_visibility()

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
        old_h = self.view.h
        self.view.layout(w, h)
        if abs(old_h - h) > 1 and not self.strokes:
            self.linescale.reset(self.view.canvas.height())
            self.linescale.top += self.view.canvas.top()
            self.linescale.bottom += self.view.canvas.top()

    # ------------------------------------------------------------------
    # Visibility
    # ------------------------------------------------------------------

    def _update_visibility(self):
        if self.window_mode:
            visible = True
        elif not self.field.active or self.user_hidden:
            visible = False
        else:
            # KWin itself decides whether the docked panel is displayed (not
            # while typing on a physical keyboard); a floating one has to ask.
            visible = self.float_allowed if self.surface.floating else True
        self.surface.set_visible(visible)

    def _on_activate(self, _im, ctx):
        self.field.attach(ctx)
        self.user_hidden = False
        self._forget_last()
        if self.theme.changed():
            self.theme.reload()
        if self.surface.floating:
            self.float_allowed = False
            self._ask_kwin()
        self._update_visibility()
        self.redraw()
        log.debug("text field activated")

    def _on_deactivate(self, _im, ctx):
        if self.field.detach(ctx):
            self.deactivated_at = time.monotonic()
            self._clear_ink()
            self._forget_last()
            self.cancel()
            self._update_visibility()
        log.debug("text field deactivated")

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

    def _on_float_lost(self, _reason: str):
        self._clear_ink()
        self.view.floating = False
        self.status = "floating failed – docked again"
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
        button = self.view.button_at(x, y)
        if button:
            self.gesture = (source, "button", button)
            self.pressed = button
            if button == "backspace":       # acts on press and repeats while held
                self._do_button("backspace")
                self.loop.set_timer("repeat", BACKSPACE_REPEAT_DELAY, self._repeat_backspace)
            self.redraw()
            return
        chip = self.view.chip_at(x, y)
        if chip:
            self.gesture = (source, "chip", chip.index)
            return
        if self.surface.floating and self.view.bar.contains(QPointF(x, y)):
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
        elif kind == "button":
            self.loop.cancel_timer("repeat")
            hit = self.pressed == data
            self.pressed = None
            if hit and data != "backspace":
                self._do_button(data)
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

    def _repeat_backspace(self):
        if self.gesture and self.gesture[1] == "button" and self.pressed == "backspace":
            self._do_button("backspace")
            self.loop.set_timer("repeat", BACKSPACE_REPEAT_INTERVAL, self._repeat_backspace)

    # ------------------------------------------------------------------
    # Buttons
    # ------------------------------------------------------------------

    def _do_button(self, name: str):
        if name in ("backspace", "enter", "space") and self._type_pending_ink_then(lambda: self._do_button(name)):
            return
        if name == "backspace":
            self.field.key(BACKSPACE)
            self._forget_last()
        elif name == "enter":
            self.field.key(ENTER)
            self._forget_last()
        elif name == "space":
            self.field.commit(" ")
            self._forget_last()
        elif name == "delword":
            self._delete_word()
        elif name == "keyboard":
            self._switch_keyboard()
        elif name == "float":
            self._toggle_float()
        elif name == "hide":
            if self.window_mode:
                self.loop.stop()
            else:
                self._hide()
        self.redraw()

    def _delete_word(self):
        """Ink that is not typed yet is thrown away; otherwise the text that
        was just inserted is removed, otherwise the word before the cursor."""
        if self.strokes or self.current is not None:
            self._clear_ink()
            return
        if self.last is not None:
            count = len(self.last.text)
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

    def _on_model_error(self, _message: str):
        self.status = "model failed to load – see `kritzel status`"
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

    def _insert(self, index: int):
        text = self.candidates[index]
        prefix = " " if self.cfg.auto_space and needs_space(self.field.before_cursor(), text) else ""
        self.field.commit(prefix + text)
        self.last = Insertion(prefix + text, prefix)
        self.selected = index

    def _choose(self, index: int):
        """A suggestion chip was tapped."""
        if not 0 <= index < len(self.candidates):
            return
        if self.last is None:
            self._insert(index)             # manual mode
            self._ink_done()
        elif index != self.selected:
            text = self.last.prefix + self.candidates[index]
            if self.field.replace(self.last.text, text):
                self.last = Insertion(text, self.last.prefix)
                self.selected = index
        self.redraw()

    def _check_last_insertion(self):
        """Once the application has shown us our insertion in its text and
        later reports text that no longer ends with it, the user has edited
        or moved the cursor: the suggestions no longer apply."""
        reported = self.field.reported_before_cursor()
        if self.last is None or reported is None:
            return
        if reported.endswith(self.last.text):
            self.last.seen_in_field = True
        elif self.last.seen_in_field:
            self._forget_last()
            self.redraw()

    def _forget_last(self):
        self.last = None
        self.candidates, self.selected = [], -1

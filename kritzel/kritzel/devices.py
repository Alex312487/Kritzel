"""Mouse, touch screen and pen, reduced to one vocabulary.

Whatever the device, the panel only sees
    sink.press(source, x, y, eraser=False)
    sink.motion(source, x, y)
    sink.release(source)
    sink.cancel()
with coordinates in logical pixels relative to the panel surface.  A source
is "pointer", ("touch", id) or ("pen", n).
"""

from __future__ import annotations

import time

BTN_LEFT = 0x110
BTN_STYLUS = 0x14B
BTN_STYLUS2 = 0x14C
TOOL_ERASER = 0x141
CURSOR_DEFAULT = 1
CURSOR_CROSSHAIR = 8
CAP_POINTER = 1
CAP_TOUCH = 4
PALM_GRACE = 0.6        # seconds after the pen left during which fingers still cannot draw


def is_touch(source) -> bool:
    return isinstance(source, tuple) and source[0] == "touch"


def source_kind(source) -> str:
    return source if isinstance(source, str) else source[0]


class _PenTool:
    """State of one tablet tool (the pen tip and its eraser end are two)."""

    def __init__(self, source):
        self.source = source
        self.x = self.y = 0.0
        self.type = 0
        self.down = False
        self.moved = False
        self.buttons: set[int] = set()
        self.events: list[str] = []         # "down"/"up" since the last frame


class InputDevices:
    def __init__(self, session, sink, palm_zone):
        """palm_zone(x, y) -> True where a resting hand must not draw."""
        self.session = session
        self.sink = sink
        self.palm_zone = palm_zone
        self.pointer = None
        self.pointer_shape = None
        self.pointer_pos = (0.0, 0.0)
        self.touch = None
        self.touch_pos: dict[int, tuple[float, float]] = {}
        self.tablet_seat = None
        self.tools: dict = {}               # tool object -> _PenTool
        self.pen_near = False
        self.pen_left_at = 0.0
        self._pens_seen = 0

        session.on_seat_caps = self._on_caps
        if session.has("wl_seat"):
            self._on_caps(session.seat_caps)
            if session.has("zwp_tablet_manager_v2"):
                self.tablet_seat = session.g["zwp_tablet_manager_v2"].get_tablet_seat(session.g["wl_seat"])
                self.tablet_seat.dispatcher["tool_added"] = self._on_tool_added

    def position_of(self, source) -> tuple[float, float]:
        if source == "pointer":
            return self.pointer_pos
        if is_touch(source):
            return self.touch_pos.get(source[1], (-1.0, -1.0))
        for tool in self.tools.values():
            if tool.source == source:
                return tool.x, tool.y
        return -1.0, -1.0

    def forget_pen(self):
        """The surface went away; no proximity-out will be sent for it."""
        self.pen_near = False

    # -- mouse -------------------------------------------------------------

    def _on_caps(self, caps):
        seat = self.session.g["wl_seat"]
        if caps & CAP_POINTER and self.pointer is None:
            self.pointer = p = seat.get_pointer()
            p.dispatcher["enter"] = self._pointer_enter
            p.dispatcher["motion"] = self._pointer_motion
            p.dispatcher["button"] = self._pointer_button
            mgr = self.session.g.get("wp_cursor_shape_manager_v1")
            self.pointer_shape = mgr.get_pointer(p) if mgr else None
        if caps & CAP_TOUCH and self.touch is None:
            self.touch = t = seat.get_touch()
            t.dispatcher["down"] = self._touch_down
            t.dispatcher["motion"] = self._touch_motion
            t.dispatcher["up"] = self._touch_up
            t.dispatcher["cancel"] = lambda *_: self.sink.cancel()

    def _pointer_enter(self, _p, serial, _surface, x, y):
        self.pointer_pos = (float(x), float(y))
        if self.pointer_shape is not None:
            self.pointer_shape.set_shape(serial, CURSOR_DEFAULT)

    def _pointer_motion(self, _p, _time, x, y):
        self.pointer_pos = (float(x), float(y))
        self.sink.motion("pointer", *self.pointer_pos)

    def _pointer_button(self, _p, _serial, _time, button, state):
        if button != BTN_LEFT:
            return
        if state:
            self.sink.press("pointer", *self.pointer_pos)
        else:
            self.sink.release("pointer")

    # -- touch -------------------------------------------------------------

    def _touch_down(self, _t, _serial, _time, _surface, tid, x, y):
        x, y = float(x), float(y)
        self.touch_pos[tid] = (x, y)
        # palm rejection: while the pen hovers, fingers may press buttons but not draw
        pen_around = self.pen_near or time.monotonic() - self.pen_left_at < PALM_GRACE
        if pen_around and self.palm_zone(x, y):
            return
        self.sink.press(("touch", tid), x, y)

    def _touch_motion(self, _t, _time, tid, x, y):
        self.touch_pos[tid] = (float(x), float(y))
        self.sink.motion(("touch", tid), float(x), float(y))

    def _touch_up(self, _t, _serial, _time, tid):
        self.sink.release(("touch", tid))       # may still ask for the last position
        self.touch_pos.pop(tid, None)

    # -- pen ---------------------------------------------------------------

    def _on_tool_added(self, _seat, tool_obj):
        self._pens_seen += 1
        tool = _PenTool(("pen", self._pens_seen))
        self.tools[tool_obj] = tool
        mgr = self.session.g.get("wp_cursor_shape_manager_v1")
        shape = mgr.get_tablet_tool_v2(tool_obj) if mgr is not None else None

        def on_type(_t, tool_type):
            tool.type = tool_type

        def on_proximity_in(_t, serial, _tablet, _surface):
            self.pen_near = True
            if shape is not None:
                shape.set_shape(serial, CURSOR_CROSSHAIR)

        def on_proximity_out(_t):
            self.pen_near = False
            self.pen_left_at = time.monotonic()
            if tool.down:
                tool.events.append("up")

        def on_motion(_t, x, y):
            tool.x, tool.y = float(x), float(y)
            tool.moved = True

        def on_button(_t, _serial, button, state):
            (tool.buttons.add if state else tool.buttons.discard)(button)

        def on_frame(_t, _time):
            # the tablet protocol groups simultaneous changes into frames
            for event in tool.events:
                if event == "down" and not tool.down:
                    tool.down = True
                    eraser = tool.type == TOOL_ERASER or bool(tool.buttons & {BTN_STYLUS, BTN_STYLUS2})
                    self.sink.press(tool.source, tool.x, tool.y, eraser=eraser)
                    tool.moved = False
                elif event == "up" and tool.down:
                    if tool.moved:
                        self.sink.motion(tool.source, tool.x, tool.y)
                    tool.down = False
                    self.sink.release(tool.source)
            tool.events.clear()
            if tool.down and tool.moved:
                self.sink.motion(tool.source, tool.x, tool.y)
            tool.moved = False

        def on_removed(_t):
            # e.g. the tablet was unplugged in the middle of a stroke
            if tool.down:
                tool.down = False
                self.sink.release(tool.source)
            self.pen_near = False
            self.tools.pop(tool_obj, None)

        tool_obj.dispatcher["type"] = on_type
        tool_obj.dispatcher["proximity_in"] = on_proximity_in
        tool_obj.dispatcher["proximity_out"] = on_proximity_out
        tool_obj.dispatcher["down"] = lambda _t, _serial: tool.events.append("down")
        tool_obj.dispatcher["up"] = lambda _t: tool.events.append("up")
        tool_obj.dispatcher["motion"] = on_motion
        tool_obj.dispatcher["button"] = on_button
        tool_obj.dispatcher["frame"] = on_frame
        tool_obj.dispatcher["removed"] = on_removed

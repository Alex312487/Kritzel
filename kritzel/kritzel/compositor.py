"""The connection to the compositor: which global interfaces it offers,
which screens exist, and the seat (input devices)."""

from __future__ import annotations

from .wayland import Connection

# interface -> highest version Kritzel speaks
WANTED = {
    "wl_compositor": 4,
    "wl_shm": 1,
    "wl_seat": 5,
    "zwp_input_method_v1": 1,
    "zwp_input_panel_v1": 1,
    "zwp_tablet_manager_v2": 1,
    "zxdg_output_manager_v1": 2,
    "wp_fractional_scale_manager_v1": 1,
    "wp_viewporter": 1,
    "wp_cursor_shape_manager_v1": 1,
    "xdg_wm_base": 1,
    "zwlr_layer_shell_v1": 1,
    "org_kde_kwin_fake_input": 4,
}
INPUT_METHOD = ("zwp_input_method_v1", "zwp_input_panel_v1")


class NotInputMethod(RuntimeError):
    """The compositor did not offer the input-method interfaces to us."""


class Output:
    """One screen."""

    def __init__(self, obj):
        self.obj = obj
        self.scale = 1              # integer wl_output scale
        self.mode = (0, 0)          # device pixels
        self.logical = None         # logical pixels, from xdg-output
        self.xdg = None

    def logical_size(self) -> tuple[int, int]:
        if self.logical:
            return self.logical
        if self.mode[0]:
            return self.mode[0] // max(1, self.scale), self.mode[1] // max(1, self.scale)
        return 1280, 800


class Session:
    """Connects, binds the globals in WANTED and keeps track of outputs.

    `g` maps interface names to bound objects.  `on_outputs_changed` and
    `on_seat_caps` may be set by whoever is interested."""

    def __init__(self, input_method: bool):
        self.conn = Connection()
        self.g: dict[str, object] = {}
        self.outputs: dict[int, Output] = {}
        self.seat_caps = 0
        self.on_outputs_changed = lambda: None
        self.on_seat_caps = lambda caps: None
        self._input_method = input_method

        self.registry = self.conn.display.get_registry()
        self.registry.dispatcher["global"] = self._on_global
        self.registry.dispatcher["global_remove"] = self._on_global_remove
        self.conn.roundtrip()       # learn the globals
        self.conn.roundtrip()       # and their initial state (outputs, seat)

        for need in ("wl_compositor", "wl_shm"):
            if need not in self.g:
                raise RuntimeError(f"compositor does not offer {need}")
        if input_method and any(name not in self.g for name in INPUT_METHOD):
            raise NotInputMethod()

    def has(self, *interfaces: str) -> bool:
        return all(name in self.g for name in interfaces)

    def first_output(self) -> Output | None:
        return next(iter(self.outputs.values()), None)

    def output_for(self, wl_output) -> Output | None:
        return next((o for o in self.outputs.values() if o.obj is wl_output), None)

    def wait_for_output(self):
        """The input panel role needs an output object."""
        for _ in range(50):
            if self.outputs:
                return
            self.conn.read(block=True)
            self.conn.dispatch()

    # -- registry ----------------------------------------------------------

    def _on_global(self, registry, name, interface, version):
        if interface == "wl_output":
            self._add_output(name, self.conn.bind(registry, name, "wl_output", min(version, 2)))
        elif interface in WANTED and interface not in self.g:
            if interface in INPUT_METHOD and not self._input_method:
                return
            obj = self.conn.bind(registry, name, interface, min(version, WANTED[interface]))
            self.g[interface] = obj
            if interface == "zxdg_output_manager_v1":
                for out in self.outputs.values():
                    self._watch_logical_size(out)
            elif interface == "wl_seat":
                obj.dispatcher["capabilities"] = self._on_caps

    def _on_global_remove(self, _registry, name):
        if self.outputs.pop(name, None) is not None:
            self.on_outputs_changed()

    def _on_caps(self, _seat, caps):
        self.seat_caps = caps
        self.on_seat_caps(caps)

    def _add_output(self, name, obj):
        out = Output(obj)
        self.outputs[name] = out

        def on_mode(_o, flags, w, h, _refresh):
            if flags & 1:           # the current mode
                out.mode = (w, h)

        def on_scale(_o, factor):
            out.scale = factor

        obj.dispatcher["mode"] = on_mode
        obj.dispatcher["scale"] = on_scale
        obj.dispatcher["done"] = lambda _o: self.on_outputs_changed()
        self._watch_logical_size(out)

    def _watch_logical_size(self, out: Output):
        mgr = self.g.get("zxdg_output_manager_v1")
        if mgr is None or out.xdg is not None:
            return
        out.xdg = mgr.get_xdg_output(out.obj)

        def on_logical_size(_x, w, h):
            out.logical = (w, h)
            self.on_outputs_changed()

        out.xdg.dispatcher["logical_size"] = on_logical_size

"""The panel's window: where it is, how big, and getting pixels onto it.

One PanelSurface can appear in three ways:

  docked    an input-panel surface (zwp_input_panel_v1).  KWin puts it at the
            bottom of the screen and makes room for it above.
  floating  a layer-shell surface that never takes the keyboard focus and is
            positioned through its top/left margins, which is what lets the
            user drag it around.
  window    an ordinary window, for `kritzel try`.

Painting is done by the owner: `paint(painter)` is called with a QPainter in
logical pixels whenever the whole panel has to be redrawn.
"""

from __future__ import annotations

import logging
import mmap
import os
import time

from PyQt6.QtGui import QImage, QPainter

log = logging.getLogger("kritzel.surface")

SHM_XRGB8888 = 1
LAYER_OVERLAY = 3
ANCHOR_TOP_LEFT = 1 | 4
MAX_BUFFERS = 4
MOVE_INTERVAL = 0.008       # at most one position update per 8 ms
FRAME_WATCHDOG = 1.0        # a covered surface gets no frame callbacks; do not wait forever


class Buffer:
    """A shared-memory pixel buffer."""

    def __init__(self, shm, width: int, height: int, on_release):
        self.width, self.height = width, height
        self.size = width * height * 4
        fd = os.memfd_create("kritzel", os.MFD_CLOEXEC)
        try:
            os.ftruncate(fd, self.size)
            self.mm = mmap.mmap(fd, self.size)
            pool = shm.create_pool(fd, self.size)
            self.wl = pool.create_buffer(0, width, height, width * 4, SHM_XRGB8888)
            pool.destroy()
        finally:
            os.close(fd)
        self.busy = False               # the compositor is still reading it
        self._on_release = on_release
        self.wl.dispatcher["release"] = self._release

    def _release(self, _buf):
        self.busy = False
        self._on_release()

    def destroy(self):
        self.wl.destroy()
        try:
            self.mm.close()
        except (BufferError, ValueError):
            pass


class PanelSurface:
    def __init__(self, session, loop, cfg, window: bool, paint, on_layout,
                 on_close=lambda: None, on_float_lost=lambda reason: None, on_gone=lambda: None):
        """on_layout(width, height) - the logical size changed
        on_close()                - the try-out window was closed
        on_float_lost(reason)     - floating failed; the panel is docked again
        on_gone()                 - a surface was unmapped or destroyed"""
        self.session, self.loop, self.cfg = session, loop, cfg
        self.g = session.g
        self.window = window
        self.paint, self.on_layout = paint, on_layout
        self.on_close, self.on_float_lost, self.on_gone = on_close, on_float_lost, on_gone

        self.size = (0, 0)              # logical pixels
        self.scale = 1.0
        self.mapped = False
        self.floating = False
        self.float_pos = None           # (left, top) in logical pixels
        self._want_visible = False
        self._fractional = None
        self._entered: list = []        # outputs the surface is on
        self._buffers: list[Buffer] = []
        self._image: QImage | None = None
        self._image_stale = True        # the image needs a full repaint
        self._unsent = False            # the image has changes the compositor has not got
        self._frame_pending = False
        self._frame_cb = None
        self._layer = None
        self._float_objs: list = []
        self._window_size = (min(cfg.max_width or 900, 900), cfg.height)
        self._drag_origin = None
        self._drag_target = None
        self._moved_at = 0.0
        self._move_unconfirmed = False
        self._move_cb = None

        self.surface, self.viewport = self._new_wl_surface()
        self._dock = (self.surface, self.viewport)
        if window:
            self._configured = False
            self._become_window()
        else:
            self._configured = True
            session.wait_for_output()
            out = session.first_output()
            self._role = self.g["zwp_input_panel_v1"].get_input_panel_surface(self.surface)
            self._role.set_toplevel(out.obj if out else None, 0)        # 0 = centred at the bottom
        session.on_outputs_changed = self._resize
        session.conn.roundtrip()
        self._resize(force=True)

    # ------------------------------------------------------------------
    # Public interface
    # ------------------------------------------------------------------

    @property
    def can_float(self) -> bool:
        return not self.window and "zwlr_layer_shell_v1" in self.g

    def set_visible(self, visible: bool, force: bool = False):
        self._want_visible = visible
        if self.floating:
            # a floating surface exists only while it is shown
            if visible and self._layer is None:
                self._create_float()            # mapped once the compositor has configured it
                return
            if not visible:
                if self._layer is not None:
                    self._destroy_float()
                return
        visible = visible and self._configured
        if visible and (not self.mapped or force):
            log.debug("showing panel")
            self.mapped = True
            self._frame_pending = False
            self._image_stale = True
            self._send()
        elif not visible and self.mapped:
            log.debug("hiding panel")
            self._unmap()

    def set_floating(self, floating: bool) -> bool:
        if floating == self.floating:
            return True
        if floating:
            if not self.can_float:
                return False
            if self.mapped:
                self._unmap()
            self.floating = True
            self.surface = self.viewport = None
            self._configured = False
            log.info("floating mode")
        else:
            self._destroy_float()
            self.floating = False
            self.surface, self.viewport = self._dock
            self._configured = True
            self._resize(force=True)
            log.info("docked mode")
        self.set_visible(self._want_visible)
        return True

    def redraw(self):
        """The panel's content changed: repaint all of it."""
        self._image_stale = True
        self._request_send()

    def draw_directly(self, draw):
        """Small additions (the stroke being drawn) are painted straight into
        the image instead of repainting the whole panel: draw(painter)."""
        if self._image is not None and not self._image_stale:
            p = self._painter()
            draw(p)
            p.end()
        self._request_send()

    def begin_drag(self):
        self._drag_origin = self.float_pos
        self._drag_target = None
        self._move_unconfirmed = False

    def drag(self, dx: float, dy: float, relative_to_origin: bool):
        """Moves the floating panel while a finger, pen or mouse drags it.
        (dx, dy) is how far the pointer is from where it grabbed the panel.

        KWin reports touch positions relative to where the panel was when
        the finger went down (relative_to_origin), but mouse and pen
        positions relative to where the panel is now.  In the second case a
        position is only usable once KWin has applied the previous move."""
        if self._layer is None or not self.mapped or self._drag_origin is None:
            return
        if relative_to_origin:
            self._drag_target = (self._drag_origin[0] + dx, self._drag_origin[1] + dy)
            return                      # applied by flush_drag(), once per batch of events
        if self._move_unconfirmed or time.monotonic() - self._moved_at < 0.025:
            return
        if self._move_to((self.float_pos[0] + dx, self.float_pos[1] + dy)):
            self._move_unconfirmed = True
            self._move_cb = cb = self.session.conn.display.sync()
            cb.dispatcher["done"] = self._on_move_confirmed

    def end_drag(self):
        self.flush_drag(now=True)
        self._drag_origin = None
        log.debug("floating panel moved to %s", self.float_pos)

    def flush_drag(self, now: bool = False):
        """Applies the newest drag position.  A touch screen delivers far more
        positions than there are frames; sending each one makes the
        compositor re-arrange its layers every time, which feels laggy."""
        if self._drag_target is None:
            return
        wait = self._moved_at + MOVE_INTERVAL - time.monotonic()
        if wait > 0 and not now:
            self.loop.set_timer("drag", wait, self.flush_drag)
            return
        target, self._drag_target = self._drag_target, None
        self._move_to(target)

    # ------------------------------------------------------------------
    # Wayland surfaces
    # ------------------------------------------------------------------

    def _new_wl_surface(self):
        surface = self.g["wl_compositor"].create_surface()
        surface.dispatcher["enter"] = self._on_enter
        surface.dispatcher["leave"] = self._on_leave
        viewport = None
        self._scale_listener = None
        if self.session.has("wp_viewporter", "wp_fractional_scale_manager_v1"):
            viewport = self.g["wp_viewporter"].get_viewport(surface)
            self._scale_listener = self.g["wp_fractional_scale_manager_v1"].get_fractional_scale(surface)
            self._scale_listener.dispatcher["preferred_scale"] = self._on_preferred_scale
        return surface, viewport

    def _become_window(self):
        if "xdg_wm_base" not in self.g:
            raise RuntimeError("compositor does not offer xdg_wm_base")
        wm = self.g["xdg_wm_base"]
        wm.dispatcher["ping"] = lambda base, serial: base.pong(serial)
        self._xdg_surface = wm.get_xdg_surface(self.surface)
        self._xdg_surface.dispatcher["configure"] = self._on_window_configure
        self._toplevel = self._xdg_surface.get_toplevel()
        self._toplevel.set_title("Kritzel – handwriting try-out")
        self._toplevel.set_app_id("kritzel")
        self._toplevel.dispatcher["configure"] = self._on_window_size
        self._toplevel.dispatcher["close"] = lambda *_: self.on_close()
        self.surface.commit()

    def _on_window_size(self, _toplevel, width, height, _states):
        if width > 0 and height > 0:
            self._window_size = (max(420, width), max(180, height))

    def _on_window_configure(self, xdg_surface, serial):
        xdg_surface.ack_configure(serial)
        self._configured = True
        self._resize()
        self.set_visible(self._want_visible, force=True)

    def _on_enter(self, _surface, wl_output):
        out = self.session.output_for(wl_output)
        if out is not None and out not in self._entered:
            self._entered.append(out)
        self._resize()

    def _on_leave(self, _surface, wl_output):
        self._entered = [o for o in self._entered if o.obj is not wl_output]
        self._resize()

    def _on_preferred_scale(self, _listener, scale_120):
        self._fractional = scale_120 / 120.0
        self._resize()

    def _unmap(self):
        self.mapped = False
        self._frame_pending = False
        self.loop.cancel_timer("frame-watchdog")
        self.surface.attach(None, 0, 0)
        self.surface.commit()
        self._drop_buffers()            # no need to keep pixel memory while hidden
        self.on_gone()

    def _drop_buffers(self):
        for b in self._buffers:
            b.destroy()
        self._buffers = []

    # -- floating ------------------------------------------------------

    def _create_float(self):
        surface, viewport = self._new_wl_surface()
        w, h = self._float_size()
        self.float_pos = self._clamp(self.float_pos)
        # KWin 5 gives the focus to any touched layer surface except docks;
        # the test-suite uses this override to run there.
        scope = os.environ.get("KRITZEL_LAYER_SCOPE", "kritzel")
        layer = self.g["zwlr_layer_shell_v1"].get_layer_surface(surface, None, LAYER_OVERLAY, scope)
        layer.set_size(w, h)
        layer.set_anchor(ANCHOR_TOP_LEFT)
        layer.set_exclusive_zone(-1)                # do not push or get pushed by panels
        layer.set_margin(self.float_pos[1], 0, 0, self.float_pos[0])
        layer.set_keyboard_interactivity(0)         # never take the keyboard focus
        layer.dispatcher["configure"] = self._on_layer_configure
        layer.dispatcher["closed"] = lambda *_: self._float_failed("closed by the compositor")
        surface.commit()
        self._layer = layer
        self._float_objs = [o for o in (viewport, self._scale_listener, surface) if o is not None]
        self.surface, self.viewport = surface, viewport
        self._configured = False
        self.mapped = False
        self._entered = []
        self.loop.set_timer("float-watchdog", 2.0, lambda: self._float_failed("no answer from the compositor"))

    def _on_layer_configure(self, layer, serial, w, h):
        if layer is not self._layer:
            return
        layer.ack_configure(serial)
        self.loop.cancel_timer("float-watchdog")
        log.debug("floating panel configured %sx%s at %s", w, h, self.float_pos)
        first = not self._configured
        self._configured = True
        if first:
            self._resize(force=True)
        self.set_visible(self._want_visible, force=True)

    def _destroy_float(self):
        self.loop.cancel_timer("float-watchdog")
        self.loop.cancel_timer("frame-watchdog")
        self.loop.cancel_timer("drag")
        self._drag_origin = self._drag_target = None
        self._drop_buffers()
        if self._layer is not None:
            self._layer.destroy()
            self._layer = None
        for obj in self._float_objs:
            obj.destroy()
        self._float_objs = []
        self.surface = self.viewport = None
        self.mapped = False
        self._frame_pending = False
        self._configured = False
        self._entered = []
        log.debug("floating panel removed")
        self.on_gone()

    def _float_failed(self, reason: str):
        if self.floating:
            log.warning("floating mode ended: %s", reason)
            self.set_floating(False)
            self.on_float_lost(reason)

    def _move_to(self, pos) -> bool:
        pos = self._clamp(pos)
        if pos == self.float_pos or self._layer is None or not self.mapped:
            return False
        self.float_pos = pos
        self._moved_at = time.monotonic()
        self._layer.set_margin(pos[1], 0, 0, pos[0])
        self.surface.commit()
        return True

    def _on_move_confirmed(self, *_):
        self._move_unconfirmed = False
        self._move_cb = None

    # ------------------------------------------------------------------
    # Geometry
    # ------------------------------------------------------------------

    def _output(self):
        return self._entered[-1] if self._entered else self.session.first_output()

    def _output_size(self) -> tuple[int, int]:
        out = self._output()
        return out.logical_size() if out else (1280, 800)

    def _panel_height(self) -> int:
        return min(self.cfg.height, int(self._output_size()[1] * 0.45))

    def _float_size(self) -> tuple[int, int]:
        return max(200, min(self.cfg.float_width, self._output_size()[0] - 16)), self._panel_height()

    def _clamp(self, pos) -> tuple[int, int]:
        """Keeps the floating panel on the screen; None = default position."""
        ow, oh = self._output_size()
        w, h = self._float_size()
        if pos is None:
            pos = ((ow - w) // 2, oh - h - 48)
        return int(max(0, min(pos[0], ow - w))), int(max(0, min(pos[1], oh - h)))

    def _resize(self, force: bool = False):
        if self.surface is None:
            return
        out = self._output()
        if self.window:
            w, h = self._window_size
        elif self.floating:
            w, h = self._float_size()
        else:
            ow = self._output_size()[0]
            w, h = (min(ow, self.cfg.max_width) if self.cfg.max_width else ow), self._panel_height()
        if self.viewport is not None and self._fractional:
            scale = self._fractional
        else:
            scale = float(max([o.scale for o in self._entered] or [out.scale if out else 1]))
        if not force and (w, h) == self.size and abs(scale - self.scale) < 1e-6 and self._image is not None:
            return
        if self.floating and self._layer is not None:
            self._layer.set_size(w, h)
            self.float_pos = self._clamp(self.float_pos)
            self._layer.set_margin(self.float_pos[1], 0, 0, self.float_pos[0])
        self.size, self.scale = (w, h), scale
        self._drop_buffers()
        self._image = QImage(max(1, round(w * scale)), max(1, round(h * scale)), QImage.Format.Format_RGB32)
        if self.viewport is not None:
            self.viewport.set_destination(w, h)
            self.surface.set_buffer_scale(1)
        else:
            self.surface.set_buffer_scale(int(scale))
        region = self.g["wl_compositor"].create_region()
        region.add(0, 0, w, h)
        self.surface.set_opaque_region(region)
        region.destroy()
        self.on_layout(w, h)
        self._image_stale = True
        if self.mapped:
            self._frame_pending = False
            self._send()

    # ------------------------------------------------------------------
    # Pixels
    # ------------------------------------------------------------------

    def _painter(self) -> QPainter:
        p = QPainter(self._image)
        p.scale(self.scale, self.scale)
        return p

    def _request_send(self):
        self._unsent = True
        if self.mapped and not self._frame_pending:
            self._send()

    def _send(self):
        """Copies the image into a free buffer and hands it to the compositor.
        At most one buffer per displayed frame: the next one goes out when
        the frame callback arrives."""
        if self._image is None or not self.mapped:
            return
        if self._image_stale:
            p = self._painter()
            self.paint(p)
            p.end()
            self._image_stale = False
        buf = next((b for b in self._buffers if not b.busy), None)
        if buf is None:
            if len(self._buffers) >= MAX_BUFFERS:
                self._unsent = True     # retried when a buffer is released
                return
            buf = Buffer(self.g["wl_shm"], self._image.width(), self._image.height(), self._on_buffer_release)
            self._buffers.append(buf)
        bits = self._image.constBits()
        bits.setsize(buf.size)
        buf.mm[: buf.size] = bits
        buf.busy = True
        self.surface.attach(buf.wl, 0, 0)
        if self.g["wl_compositor"].version >= 4:
            self.surface.damage_buffer(0, 0, buf.width, buf.height)
        else:
            self.surface.damage(0, 0, self.size[0], self.size[1])
        self._frame_cb = cb = self.surface.frame()
        cb.dispatcher["done"] = self._on_frame
        self.surface.commit()
        self._frame_pending = True
        self._unsent = False
        self.loop.set_timer("frame-watchdog", FRAME_WATCHDOG, self._on_frame)

    def _on_buffer_release(self):
        if self._unsent and self.mapped and not self._frame_pending:
            self._send()

    def _on_frame(self, *_):
        self.loop.cancel_timer("frame-watchdog")
        self._frame_cb = None
        self._frame_pending = False
        if self._unsent:
            self._send()

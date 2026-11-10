"""Panel layout and drawing.  Everything is painted with QPainter into a
QImage; there are no widgets, because the surface is a raw Wayland input
panel rather than a Qt window."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

from . import layout
from .keys import KEY, enabled_keys, parse_options

BAR_H = 44          # suggestions
SYM_H = 34          # symbol row
SYM_W = 42
SYM_GAP = 4
PAD = 6
GRIP_W = 34
KEY_WIDTHS = {"small": 50.0, "normal": 62.0, "large": 76.0}
MAX_KEYS_SHARE = 0.5        # the keys take at most this share of the panel's width

# what the area below the suggestions shows
WRITE, SYMBOLS, LANGUAGES = "write", "symbols", "languages"

# names of the keys that are not in keys.KEYS start with one of these
SYMBOL = "sym:"             # + the text the key types
LANGUAGE = "lang:"          # + a language code, ALL_LANGUAGES or MORE_LANGUAGES
ALL_LANGUAGES = "*"
MORE_LANGUAGES = "+"


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Chip:
    """A suggestion in the bar."""
    __slots__ = ("rect", "text", "index", "primary")

    def __init__(self, rect, text, index, primary):
        self.rect = rect
        self.text = text
        self.index = index
        self.primary = primary


class PageKey:
    """A key in the area below the suggestions: symbol or language."""
    __slots__ = ("rect", "name", "text", "selected")

    def __init__(self, rect, name, text, selected=False):
        self.rect = rect
        self.name = name
        self.text = text
        self.selected = selected


class View:
    """Layout and painting.  Knows nothing about what the keys do."""

    def __init__(self, theme, cfg):
        self.theme = theme
        self.w = 800.0
        self.h = 280.0
        self.floating = False
        self.page = WRITE
        self.languages: list[tuple[str, str, bool]] = []    # (name, title, in use) for the LANGUAGES page
        self.buttons: dict[str, QRectF] = {}                # the keys of keys.KEYS that are shown
        self.page_keys: list[PageKey] = []
        self.chips: list[Chip] = []
        self.bar = QRectF()             # suggestions; also the handle of the floating panel
        self.body = QRectF()            # everything below the bar, beside the keys
        self.canvas = QRectF()          # the writing area; empty on the other pages
        self.configure(cfg)

    # -- geometry ----------------------------------------------------------

    def configure(self, cfg):
        """Takes over the settings that concern the look."""
        self.rows = layout.arrange(enabled_keys(parse_options(cfg.keys)))
        self.key_width = KEY_WIDTHS[cfg.key_size]
        self.keys_left = cfg.keys_side == "left"
        self.symbol_row = cfg.symbol_row_keys()
        self.symbol_keyboard = cfg.symbol_keyboard_keys()
        self.pen_width = cfg.pen_width
        self.ink_color = cfg.ink_color
        self.guide_line = cfg.guide_line
        self.layout(self.w, self.h)

    def has_key(self, name: str) -> bool:
        return name in self.buttons

    def show_page(self, page: str, languages=()):
        self.page = page
        self.languages = list(languages)
        self.layout(self.w, self.h)

    def layout(self, w: float, h: float):
        self.w, self.h = w, h
        block_w = layout.key_block_width(self.rows, self.key_width, PAD, w * MAX_KEYS_SHARE)
        block_x = 0.0 if self.keys_left else w - block_w
        self.buttons = {name: QRectF(*rect)
                        for name, rect in layout.key_rects(self.rows, block_x, 0, block_w, h, PAD).items()}
        # the keys bring their own padding; the body needs one at the panel's edge
        left = block_w if self.keys_left else PAD
        right = w - PAD if self.keys_left else w - block_w
        self.bar = QRectF(left, 0, right - left, BAR_H)
        self.body = QRectF(left, BAR_H, right - left, h - BAR_H - PAD)
        self.page_keys = []
        self.canvas = QRectF()
        if self.page == SYMBOLS:
            self._place(self.symbol_keyboard, SYMBOL, layout.grid(len(self.symbol_keyboard), *self._body(), SYM_GAP))
        elif self.page == LANGUAGES:
            rects = layout.grid(len(self.languages), *self._body(), PAD, min_key_h=40.0, aspect=3.5)
            for rect, (name, title, in_use) in zip(rects, self.languages):
                self.page_keys.append(PageKey(QRectF(*rect), name, title, in_use))
        else:
            top = self.body.top()
            if self.symbol_row:
                rects = layout.row_of_keys(len(self.symbol_row), self.body.left(), top, self.body.width(), SYM_H,
                                           SYM_GAP, SYM_W, 60.0)
                self._place(self.symbol_row, SYMBOL, rects)
                top += SYM_H + PAD
            self.canvas = QRectF(self.body.left(), top, self.body.width(), self.body.bottom() - top)

    def _body(self):
        return self.body.left(), self.body.top(), self.body.width(), self.body.height()

    def _place(self, texts, prefix, rects):
        for rect, text in zip(rects, texts):
            self.page_keys.append(PageKey(QRectF(*rect), prefix + text, text))

    def button_at(self, x, y):
        """Name of the key at (x, y), else None."""
        point = QPointF(x, y)
        for name, rect in self.buttons.items():
            if rect.contains(point):
                return name
        for key in self.page_keys:
            # the gap after a key belongs to it: nothing is missed between two keys
            if key.rect.adjusted(0, 0, SYM_GAP, 0).contains(point):
                return key.name
        return None

    def chip_at(self, x, y):
        for chip in self.chips:
            if chip.rect.contains(QPointF(x, y)):
                return chip
        return None

    def in_canvas(self, x, y):
        return self.canvas.contains(QPointF(x, y))

    def in_handle(self, x, y):
        """The spot a floating panel is dragged by."""
        return self.bar.contains(QPointF(x, y))

    # -- painting ----------------------------------------------------------

    def font(self, px: float, bold=False) -> QFont:
        f = QFont(self.theme.font_family)
        f.setPixelSize(max(6, round(px)))
        f.setBold(bold)
        return f

    def ink_pen(self) -> QPen:
        pen = QPen(QColor(self.ink_color) if self.ink_color else QColor(*self.theme.colors["text"]))
        pen.setWidthF(self.pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def paint(self, p: QPainter, state):
        """state: object with strokes, candidates, selected, status, pressed,
        hint, preview and language_label attributes (see panel.Panel)."""
        c = self.theme.colors
        p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
        p.setRenderHint(QPainter.RenderHint.TextAntialiasing, True)
        p.fillRect(QRectF(0, 0, self.w, self.h), QColor(*c["window"]))
        border = mix(c["window"], c["text"], 0.18)
        p.setPen(QPen(QColor(*border), 1))
        if self.floating:
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawRect(QRectF(0.5, 0.5, self.w - 1, self.h - 1))
            # grip: the bar is the handle the panel is dragged by
            p.setPen(Qt.PenStyle.NoPen)
            p.setBrush(QColor(*mix(c["window"], c["text"], 0.45)))
            for gx in (8, 16):
                for gy in (14, 22, 30):
                    p.drawEllipse(QPointF(self.bar.left() + gx, gy), 1.7, 1.7)
        else:
            p.drawLine(QPointF(0, 0.5), QPointF(self.w, 0.5))
        if self.page == WRITE:
            self._paint_canvas(p, state)
        self._paint_bar(p, state)
        for key in self.page_keys:
            self._paint_page_key(p, key, state.pressed == key.name)
        for name, rect in self.buttons.items():
            self._paint_button(p, name, rect, state.pressed == name, state.language_label)

    def _paint_canvas(self, p, state):
        c = self.theme.colors
        p.setPen(QPen(QColor(*mix(c["window"], c["text"], 0.18)), 1))
        p.setBrush(QColor(*c["view"]))
        p.drawRoundedRect(self.canvas.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        if self.guide_line:
            p.setPen(QPen(QColor(*mix(c["view"], c["text"], 0.22)), 1, Qt.PenStyle.DashLine))
            gy = round(self.canvas.top() + self.canvas.height() * 0.72) + 0.5
            p.drawLine(QPointF(self.canvas.left() + 14, gy), QPointF(self.canvas.right() - 14, gy))
        if not state.strokes and state.hint:
            p.setPen(QColor(*mix(c["view"], c["text"], 0.38)))
            p.setFont(self.font(15))
            p.drawText(self.canvas.adjusted(0, 0, 0, -self.canvas.height() * 0.3),
                       Qt.AlignmentFlag.AlignCenter, state.hint)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.ink_pen())
        p.save()
        p.setClipRect(self.canvas)
        for st in state.strokes:
            p.drawPath(st.path())
        p.restore()

    def _paint_bar(self, p, state):
        c = self.theme.colors
        self.chips = []
        x = self.bar.left() + (GRIP_W - PAD if self.floating else 0)
        y = 7
        h = BAR_H - 14
        right = self.bar.right() - 2
        if state.status:
            p.setFont(self.font(13))
            fm = QFontMetricsF(p.font())
            sw = fm.horizontalAdvance(state.status) + 8
            p.setPen(QColor(*c["inactive"]))
            p.drawText(QRectF(right - sw, y, sw, h),
                       Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight, state.status)
            right -= sw + PAD
        if state.preview is not None:
            # try-out window: show the text that would have been typed
            p.setFont(self.font(15))
            p.setPen(QColor(*c["text"]))
            fm = QFontMetricsF(p.font())
            text = fm.elidedText(state.preview or "", Qt.TextElideMode.ElideLeft, max(40.0, (right - x) * 0.5))
            p.drawText(QRectF(x, y, right - x, h), Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignLeft, text)
            x += fm.horizontalAdvance(text) + 14
        for i, text in enumerate(state.candidates):
            primary = i == state.selected
            p.setFont(self.font(15, bold=primary))
            fm = QFontMetricsF(p.font())
            avail = right - x
            if avail < 48:
                break
            label = fm.elidedText(text, Qt.TextElideMode.ElideRight, min(avail - 20, 420.0))
            w = fm.horizontalAdvance(label) + 20
            rect = QRectF(x, y, w, h)
            if primary:
                p.setPen(Qt.PenStyle.NoPen)
                p.setBrush(QColor(*c["accent"]))
                p.drawRoundedRect(rect, 5, 5)
                p.setPen(QColor(*c["accent_text"]))
            else:
                p.setPen(QPen(QColor(*mix(c["window"], c["text"], 0.25)), 1))
                p.setBrush(QColor(*c["button"]))
                p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 5, 5)
                p.setPen(QColor(*c["text"]))
            p.drawText(rect, Qt.AlignmentFlag.AlignCenter, label)
            self.chips.append(Chip(rect, text, i, primary))
            x += w + PAD

    def _paint_key_background(self, p, rect, pressed, radius=6, lit=False):
        c = self.theme.colors
        if lit:                     # a key that is switched on, a language in use
            bg = c["accent"] if not pressed else mix(c["accent"], c["button"], 0.3)
        else:
            bg = c["button"] if not pressed else mix(c["button"], c["accent"], 0.45)
        p.setPen(QPen(QColor(*mix(c["window"], c["text"], 0.25)), 1))
        p.setBrush(QColor(*bg))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), radius, radius)

    def _paint_page_key(self, p, key: PageKey, pressed):
        c = self.theme.colors
        self._paint_key_background(p, key.rect, pressed, 5, lit=key.selected)
        p.setPen(QColor(*(c["accent_text"] if key.selected else c["text"])))
        if key.name.startswith(LANGUAGE):
            p.setFont(self.font(15, bold=key.selected))
        else:
            p.setFont(self.font(min(20.0, key.rect.height() * 0.5) if len(key.text) == 1 else 13))
        fm = QFontMetricsF(p.font())
        p.drawText(key.rect, Qt.AlignmentFlag.AlignCenter,
                   fm.elidedText(key.text, Qt.TextElideMode.ElideRight, key.rect.width() - 6))

    def _paint_button(self, p, name, rect, pressed, language_label=""):
        c = self.theme.colors
        lit = (name == "symbols" and self.page == SYMBOLS) or (name == "lang" and self.page == LANGUAGES)
        self._paint_key_background(p, rect, pressed, lit=lit)
        ink = c["accent_text"] if lit else c["text"]
        pen = QPen(QColor(*ink))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        key = KEY[name]
        caption, label = key.caption, key.label
        if name == "float" and self.floating:
            caption = "dock"
        elif name == "symbols":
            label, caption = ("abc", "write") if self.page == SYMBOLS else ("?#1", "symbols")
        elif name == "lang":
            label = language_label
        lift = 7 if caption and rect.height() >= 50 else 0
        if lift:
            p.save()
            p.setFont(self.font(10.5))
            p.setPen(QColor(*(ink if lit else c["inactive"])))
            p.drawText(QRectF(rect.left(), rect.bottom() - 21, rect.width(), 16), Qt.AlignmentFlag.AlignCenter, caption)
            p.restore()
        if label:
            p.setPen(QColor(*ink))
            p.setFont(self.font(15 if len(label) <= 3 else 12, bold=True))
            p.drawText(rect.adjusted(0, 0, 0, -2 * lift), Qt.AlignmentFlag.AlignCenter, label)
            p.restore()
            return
        p.translate(rect.center().x() - 12, rect.center().y() - 12 - lift)   # icons live in a 24x24 box
        path = QPainterPath()
        if name == "backspace":
            path.moveTo(8, 5); path.lineTo(21, 5); path.lineTo(21, 19); path.lineTo(8, 19)
            path.lineTo(2, 12); path.closeSubpath()
            path.moveTo(11, 9); path.lineTo(17, 15); path.moveTo(17, 9); path.lineTo(11, 15)
        elif name == "enter":
            path.moveTo(20, 5); path.lineTo(20, 14); path.lineTo(5, 14)
            path.moveTo(9, 10); path.lineTo(5, 14); path.lineTo(9, 18)
        elif name == "space":
            path.moveTo(3, 11); path.lineTo(3, 16); path.lineTo(21, 16); path.lineTo(21, 11)
        elif name == "delword":
            # backspace shape, wider, with a word ("three letters") inside
            path.moveTo(7, 5); path.lineTo(23, 5); path.lineTo(23, 19); path.lineTo(7, 19)
            path.lineTo(1, 12); path.closeSubpath()
            for lx in (10, 14, 18):
                path.moveTo(lx, 10); path.lineTo(lx, 14)
        elif name == "keyboard":
            path.addRoundedRect(QRectF(2, 6, 20, 13), 2, 2)
            for kx in (6, 10, 14, 18):
                path.moveTo(kx, 10); path.lineTo(kx + 0.01, 10)
            path.moveTo(8, 15); path.lineTo(16, 15)
        elif name == "float":
            if self.floating:       # dock: screen with a bar at the bottom
                path.addRoundedRect(QRectF(3, 4, 18, 16), 2, 2)
                path.moveTo(3, 15); path.lineTo(21, 15)
            else:                   # float: a small window above the surface
                path.addRoundedRect(QRectF(6, 4, 15, 10), 2, 2)
                path.moveTo(3, 9); path.lineTo(3, 20); path.lineTo(17, 20)
        elif name in ("left", "right", "up", "down"):
            path.moveTo(19, 12); path.lineTo(5, 12)
            path.moveTo(11, 6); path.lineTo(5, 12); path.lineTo(11, 18)
            p.translate(12, 12)
            p.rotate({"left": 0, "up": 90, "right": 180, "down": 270}[name])
            p.translate(-12, -12)
        elif name == "settings":
            # three sliders
            for ly, knob in ((6, 15), (12, 8), (18, 13)):
                path.moveTo(3, ly); path.lineTo(knob - 2.6, ly)
                path.moveTo(knob + 2.6, ly); path.lineTo(21, ly)
                path.addEllipse(QPointF(knob, ly), 2.6, 2.6)
        elif name == "copy":
            # two sheets
            path.addRoundedRect(QRectF(8, 8, 12, 13), 2, 2)
            path.moveTo(16, 4.5); path.lineTo(6, 4.5); path.lineTo(4.5, 6); path.lineTo(4.5, 17)
        elif name == "paste":
            # clipboard
            path.addRoundedRect(QRectF(5, 5, 14, 16), 2, 2)
            path.addRoundedRect(QRectF(9, 3, 6, 4), 1, 1)
            path.moveTo(9, 12); path.lineTo(15, 12); path.moveTo(9, 16); path.lineTo(15, 16)
        elif name == "cut":
            # scissors
            path.moveTo(7, 4); path.lineTo(15.5, 15.5)
            path.moveTo(17, 4); path.lineTo(8.5, 15.5)
            path.addEllipse(QPointF(7, 18), 2.6, 2.6)
            path.addEllipse(QPointF(17, 18), 2.6, 2.6)
        elif name == "hide":
            path.moveTo(5, 8); path.lineTo(12, 15); path.lineTo(19, 8)
            path.moveTo(5, 19); path.lineTo(19, 19)
        p.drawPath(path)
        p.restore()

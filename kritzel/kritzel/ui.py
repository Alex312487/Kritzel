"""Panel layout and drawing.  Everything is painted with QPainter into a
QImage; there are no widgets, because the surface is a raw Wayland input
panel rather than a Qt window."""

from __future__ import annotations

from PyQt6.QtCore import QPointF, QRectF, Qt
from PyQt6.QtGui import QColor, QFont, QFontMetricsF, QPainter, QPainterPath, QPen

BAR_H = 44
BTN_W = 62
BTN_COLS = 2
PAD = 6

# name -> (row, column, column span); four rows, two columns
BUTTONS = {
    "backspace": (0, 0, 1), "enter": (0, 1, 1),
    "space": (1, 0, 2),
    "delword": (2, 0, 1), "keyboard": (2, 1, 1),
    "float": (3, 0, 1), "hide": (3, 1, 1),
}
BTN_ROWS = 4
CAPTIONS = {"delword": "word", "keyboard": "keys", "float": "float", "hide": "hide"}
GRIP_W = 34


def mix(a, b, t):
    return tuple(round(a[i] + (b[i] - a[i]) * t) for i in range(3))


class Chip:
    __slots__ = ("rect", "text", "index", "primary")

    def __init__(self, rect, text, index, primary):
        self.rect = rect
        self.text = text
        self.index = index
        self.primary = primary


class View:
    def __init__(self, theme, pen_width: float):
        self.theme = theme
        self.pen_width = pen_width
        self.w = 800
        self.h = 280
        self.buttons: dict[str, QRectF] = {}
        self.canvas = QRectF()
        self.bar = QRectF()
        self.chips: list[Chip] = []
        self.floating = False
        self.layout(self.w, self.h)

    # -- geometry ----------------------------------------------------------

    def layout(self, w: float, h: float):
        self.w, self.h = w, h
        col_w = BTN_COLS * BTN_W + (BTN_COLS + 1) * PAD
        self.bar = QRectF(0, 0, w - col_w, BAR_H)
        self.canvas = QRectF(PAD, BAR_H, w - col_w - PAD, h - BAR_H - PAD)
        bh = (h - (BTN_ROWS + 1) * PAD) / BTN_ROWS
        x0 = w - col_w + PAD
        self.buttons = {}
        for name, (r, c, span) in BUTTONS.items():
            self.buttons[name] = QRectF(x0 + c * (BTN_W + PAD), PAD + r * (bh + PAD),
                                        span * BTN_W + (span - 1) * PAD, bh)

    def button_at(self, x, y):
        for name, rect in self.buttons.items():
            if rect.contains(QPointF(x, y)):
                return name
        return None

    def chip_at(self, x, y):
        for chip in self.chips:
            if chip.rect.contains(QPointF(x, y)):
                return chip
        return None

    def in_canvas(self, x, y):
        return self.canvas.contains(QPointF(x, y))

    # -- painting ----------------------------------------------------------

    def font(self, px: float, bold=False) -> QFont:
        f = QFont(self.theme.font_family)
        f.setPixelSize(max(6, round(px)))
        f.setBold(bold)
        return f

    def ink_pen(self, erasing=False) -> QPen:
        c = self.theme.colors
        pen = QPen(QColor(*(c["text"] if not erasing else c["inactive"])))
        pen.setWidthF(self.pen_width)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        return pen

    def paint(self, p: QPainter, state):
        """state: object with strokes, candidates, selected, status, pressed,
        hint, preview attributes (see panel.Panel)."""
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
            for gx in (14, 22):
                for gy in (14, 22, 30):
                    p.drawEllipse(QPointF(gx, gy), 1.7, 1.7)
            p.setPen(QPen(QColor(*border), 1))
        else:
            p.drawLine(QPointF(0, 0.5), QPointF(self.w, 0.5))

        # writing area
        p.setPen(QPen(QColor(*border), 1))
        p.setBrush(QColor(*c["view"]))
        p.drawRoundedRect(self.canvas.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        guide = QPen(QColor(*mix(c["view"], c["text"], 0.22)), 1, Qt.PenStyle.DashLine)
        p.setPen(guide)
        gy = round(self.canvas.top() + self.canvas.height() * 0.72) + 0.5
        p.drawLine(QPointF(self.canvas.left() + 14, gy), QPointF(self.canvas.right() - 14, gy))
        if not state.strokes and state.hint:
            p.setPen(QColor(*mix(c["view"], c["text"], 0.38)))
            p.setFont(self.font(15))
            p.drawText(self.canvas.adjusted(0, 0, 0, -self.canvas.height() * 0.3),
                       Qt.AlignmentFlag.AlignCenter, state.hint)

        # ink
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.setPen(self.ink_pen())
        p.save()
        p.setClipRect(self.canvas)
        for st in state.strokes:
            p.drawPath(st.path())
        p.restore()

        self._paint_bar(p, state)
        for name, rect in self.buttons.items():
            self._paint_button(p, name, rect, state.pressed == name)

    def _paint_bar(self, p, state):
        c = self.theme.colors
        self.chips = []
        x = PAD + (GRIP_W if self.floating else 0)
        y = 7
        h = BAR_H - 14
        right = self.bar.right() - PAD
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

    def _paint_button(self, p, name, rect, pressed):
        c = self.theme.colors
        bg = c["button"] if not pressed else mix(c["button"], c["accent"], 0.45)
        p.setPen(QPen(QColor(*mix(c["window"], c["text"], 0.25)), 1))
        p.setBrush(QColor(*bg))
        p.drawRoundedRect(rect.adjusted(0.5, 0.5, -0.5, -0.5), 6, 6)
        pen = QPen(QColor(*c["text"]))
        pen.setWidthF(1.7)
        pen.setCapStyle(Qt.PenCapStyle.RoundCap)
        pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        p.setPen(pen)
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.save()
        caption = CAPTIONS.get(name)
        if name == "float" and self.floating:
            caption = "dock"
        lift = 7 if caption and rect.height() >= 50 else 0
        if lift:
            p.save()
            p.setFont(self.font(10.5))
            p.setPen(QColor(*c["inactive"]))
            p.drawText(QRectF(rect.left(), rect.bottom() - 21, rect.width(), 16), Qt.AlignmentFlag.AlignCenter, caption)
            p.restore()
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
        elif name == "hide":
            path.moveTo(5, 8); path.lineTo(12, 15); path.lineTo(19, 8)
            path.moveTo(5, 19); path.lineTo(19, 19)
        p.drawPath(path)
        p.restore()

"""Pen strokes and their conversion into the line image the recognizer reads."""

from __future__ import annotations

import numpy as np
from PyQt6.QtCore import QPointF, Qt
from PyQt6.QtGui import QColor, QImage, QPainter, QPainterPath, QPen

from .recognizer import MODEL_HEIGHT


class Stroke:
    __slots__ = ("points",)

    def __init__(self):
        self.points: list[tuple[float, float]] = []

    def add(self, x: float, y: float) -> bool:
        if self.points:
            px, py = self.points[-1]
            if abs(px - x) + abs(py - y) < 0.4:
                return False
        self.points.append((x, y))
        return True

    def shift(self, dx: float):
        """Moves the stroke sideways."""
        self.points = [(x + dx, y) for x, y in self.points]

    def bounds(self):
        xs = [p[0] for p in self.points]
        ys = [p[1] for p in self.points]
        return min(xs), min(ys), max(xs), max(ys)

    def path(self) -> QPainterPath:
        """Smooth curve through the sampled points (midpoint quadratic spline)."""
        pts = self.points
        path = QPainterPath()
        if not pts:
            return path
        path.moveTo(QPointF(*pts[0]))
        if len(pts) == 1:
            path.lineTo(QPointF(pts[0][0] + 0.01, pts[0][1]))
            return path
        if len(pts) == 2:
            path.lineTo(QPointF(*pts[1]))
            return path
        for i in range(1, len(pts) - 1):
            mx = (pts[i][0] + pts[i + 1][0]) / 2
            my = (pts[i][1] + pts[i + 1][1]) / 2
            path.quadTo(QPointF(*pts[i]), QPointF(mx, my))
        path.lineTo(QPointF(*pts[-1]))
        return path

    def near(self, x: float, y: float, radius: float) -> bool:
        r2 = radius * radius
        pts = self.points
        if len(pts) == 1:
            return (pts[0][0] - x) ** 2 + (pts[0][1] - y) ** 2 <= r2
        for (x1, y1), (x2, y2) in zip(pts, pts[1:]):
            dx, dy = x2 - x1, y2 - y1
            d2 = dx * dx + dy * dy
            t = 0.0 if d2 == 0 else max(0.0, min(1.0, ((x - x1) * dx + (y - y1) * dy) / d2))
            if (x1 + t * dx - x) ** 2 + (y1 + t * dy - y) ** 2 <= r2:
                return True
        return False


def ink_bounds(strokes):
    b = [s.bounds() for s in strokes if s.points]
    if not b:
        return None
    return min(v[0] for v in b), min(v[1] for v in b), max(v[2] for v in b), max(v[3] for v in b)


class LineScale:
    """Remembers how tall the user's writing normally is.

    A text-line recognizer sees every line scaled to the same height, so a
    lone "o", "O" and "0" (or "," and "'") would look identical.  Scaling
    short inputs relative to the usual writing band keeps their size and
    vertical position meaningful.
    """

    def __init__(self, canvas_top: float, canvas_height: float):
        self.reset(canvas_top, canvas_height)

    def reset(self, canvas_top: float, canvas_height: float):
        """Starts again with the band a writer typically uses in a writing
        area at canvas_top, canvas_height (panel coordinates)."""
        self.top = canvas_top + canvas_height * 0.22
        self.bottom = canvas_top + canvas_height * 0.78
        self.samples = 0

    @property
    def height(self) -> float:
        return max(8.0, self.bottom - self.top)

    def learn(self, y0: float, y1: float, aspect: float):
        # only full-looking lines (several characters wide) teach us the band
        if aspect < 2.0:
            return
        a = 0.5 if self.samples < 2 else 0.25
        self.top += (y0 - self.top) * a
        self.bottom += (y1 - self.bottom) * a
        self.samples += 1

    def frame(self, y0: float, y1: float, aspect: float):
        """Vertical range [top, bottom] that should map onto the model height."""
        h = y1 - y0
        if aspect >= 2.0 and h >= 0.5 * self.height:
            return y0, y1
        if h >= 0.85 * self.height:
            return y0, y1
        top = min(y0, self.top)
        bottom = max(y1, self.bottom)
        return top, bottom


def render_line(strokes, scale: LineScale | None = None, pen_ratio: float = 0.045) -> np.ndarray | None:
    """Rasterise strokes into the (48, W) grayscale image the model expects:
    black ink on white, cropped to the writing, with a small margin."""
    b = ink_bounds(strokes)
    if b is None:
        return None
    x0, y0, x1, y1 = b
    w = max(1.0, x1 - x0)
    h = max(1.0, y1 - y0)
    if scale is not None:
        top, bottom = scale.frame(y0, y1, w / h)
    else:
        top, bottom = y0, y1
    band = max(4.0, bottom - top)
    pad_v = 5.0                                  # pixels of margin at 48 px height
    s = (MODEL_HEIGHT - 2 * pad_v) / band
    pad_h = 8.0
    width = int(min(4096, max(MODEL_HEIGHT, w * s + 2 * pad_h)))

    img = QImage(width, MODEL_HEIGHT, QImage.Format.Format_Grayscale8)
    img.fill(QColor(255, 255, 255))
    p = QPainter(img)
    p.setRenderHint(QPainter.RenderHint.Antialiasing, True)
    pen = QPen(QColor(0, 0, 0))
    pen.setWidthF(max(1.2, MODEL_HEIGHT * pen_ratio))
    pen.setCapStyle(Qt.PenCapStyle.RoundCap)
    pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
    p.setPen(pen)
    p.translate(pad_h, pad_v)
    p.scale(s, s)
    p.translate(-x0, -top)
    pen.setWidthF(pen.widthF() / s)
    p.setPen(pen)
    for st in strokes:
        if st.points:
            p.drawPath(st.path())
    p.end()
    return qimage_to_gray(img)


def qimage_to_gray(img: QImage) -> np.ndarray:
    img = img.convertToFormat(QImage.Format.Format_Grayscale8)
    ptr = img.constBits()
    ptr.setsize(img.sizeInBytes())
    arr = np.frombuffer(ptr, dtype=np.uint8).reshape(img.height(), img.bytesPerLine())
    return arr[:, : img.width()].copy()

"""Strokes that are commands rather than writing.

    scratch-out   a zigzag over ink that is waiting: that ink is rubbed out
    split         a tall vertical line through waiting ink: a gap opens there,
                  so that two words that ran together are read as two
    delete word   a long line drawn from right to left
    space         a long line drawn from left to right
    enter         down, then a long line to the left (the shape on the key)

The last three only count in an empty writing area, the first two only over
ink.  Each is far from anything one writes: the lines are longer than a dash
ever is, the split is taller than any letter, the zigzag runs back and forth
over ink that is already there.  A stroke that is not clearly a gesture is
ink.

Pure geometry: classify() knows nothing about the panel.
"""

from __future__ import annotations

import math

SCRATCH, SPLIT, DELETE_WORD, SPACE, ENTER = "scratch", "split", "delete_word", "space", "enter"

# all lengths are in units of `band`, the usual height of the user's writing
LINE_MIN_LENGTH = 1.5           # delete word, space: longer than any dash
LINE_MAX_WIDTH_SHARE = 0.5      # ... but never asking for more than half the writing area
LINE_MAX_SLOPE = 0.25           # how far a "horizontal" line may rise or fall
STRAIGHT_MAX_DETOUR = 1.15      # path length / distance between the ends
STRAIGHT_MAX_BULGE = 0.1        # largest distance from the direct line / its length
SPLIT_MIN_HEIGHT_SHARE = 0.75   # of the writing area's height: taller than any letter
SPLIT_MAX_LEAN = 0.2
SPLIT_GAP = 0.6                 # the gap a split opens
SCRATCH_MIN_PASSES = 4          # back and forth, and again
SCRATCH_PASS_SHARE = 0.35       # a pass crosses at least this share of the scratch's width
SCRATCH_MIN_WIDTH = 0.3
ENTER_MIN_FOOT = 1.0            # the line to the left
ENTER_MIN_STEM = 0.25           # the line down
ENTER_FOOT_TO_STEM = 1.5        # a "J" has a long stem and a short foot; this is the opposite


class Gesture:
    def __init__(self, kind: str, strokes=(), x: float = 0.0):
        self.kind = kind
        self.strokes = tuple(strokes)   # SCRATCH: indices of the strokes it covers
        self.x = x                      # SPLIT: where the gap opens


def classify(points, canvas, band: float, ink=()):
    """The gesture drawn by `points`, or None if they are ink.

    canvas: (left, top, width, height) of the writing area
    band:   the usual height of the user's writing (ink.LineScale.height)
    ink:    bounds (x0, y0, x1, y1) of the strokes waiting to be recognised
    """
    if len(points) < 2:
        return None
    ink = list(ink)
    if ink:
        return _scratch(points, band, ink) or _split(points, canvas, band, ink)
    return _line(points, canvas, band) or _enter(points, band)


def split_shifts(bounds, x: float, band: float, canvas) -> list[float]:
    """How far each stroke moves when a gap opens at `x`: the strokes left
    of it to the left, the others to the right, as far as the writing area
    has room."""
    left, _top, width, _height = canvas
    x0 = min(b[0] for b in bounds)
    x1 = max(b[2] for b in bounds)
    gap = SPLIT_GAP * band
    room_left = max(0.0, x0 - left - 2.0)
    room_right = max(0.0, left + width - x1 - 2.0)
    to_right = min(room_right, max(gap / 2, gap - room_left))
    to_left = min(room_left, gap - to_right)
    return [to_right if (b[0] + b[2]) / 2 > x else -to_left for b in bounds]


# -- the single shapes -------------------------------------------------------

def _bounds(points):
    xs = [p[0] for p in points]
    ys = [p[1] for p in points]
    return min(xs), min(ys), max(xs), max(ys)


def _length(a, b) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])


def _is_straight(points) -> bool:
    a, b = points[0], points[-1]
    direct = _length(a, b)
    if direct <= 0.0:
        return False
    path = sum(_length(p, q) for p, q in zip(points, points[1:]))
    if path > STRAIGHT_MAX_DETOUR * direct:
        return False
    dx, dy = (b[0] - a[0]) / direct, (b[1] - a[1]) / direct
    bulge = max(abs((p[0] - a[0]) * dy - (p[1] - a[1]) * dx) for p in points)
    return bulge <= STRAIGHT_MAX_BULGE * direct


def _line(points, canvas, band):
    """A long horizontal line: to the left deletes a word, to the right is a space."""
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    needed = min(LINE_MIN_LENGTH * band, LINE_MAX_WIDTH_SHARE * canvas[2])
    if abs(dx) < needed or abs(dy) > LINE_MAX_SLOPE * abs(dx) or not _is_straight(points):
        return None
    return Gesture(DELETE_WORD if dx < 0 else SPACE)


def _enter(points, band):
    """Down, then left: two straight lines that meet in a corner."""
    a, b = points[0], points[-1]
    direct = _length(a, b)
    if direct <= 0.0:
        return None
    # the corner is the point farthest from the direct line between the ends
    dx, dy = (b[0] - a[0]) / direct, (b[1] - a[1]) / direct
    corner = max(range(len(points)), key=lambda i: abs((points[i][0] - a[0]) * dy - (points[i][1] - a[1]) * dx))
    stem, foot = points[: corner + 1], points[corner:]
    if len(stem) < 2 or len(foot) < 2 or not (_is_straight(stem) and _is_straight(foot)):
        return None
    stem_dx, stem_dy = stem[-1][0] - stem[0][0], stem[-1][1] - stem[0][1]
    foot_dx, foot_dy = foot[-1][0] - foot[0][0], foot[-1][1] - foot[0][1]
    goes_down = stem_dy >= ENTER_MIN_STEM * band and abs(stem_dx) <= 0.5 * stem_dy
    goes_left = -foot_dx >= ENTER_MIN_FOOT * band and abs(foot_dy) <= LINE_MAX_SLOPE * -foot_dx
    if goes_down and goes_left and -foot_dx >= ENTER_FOOT_TO_STEM * stem_dy:
        return Gesture(ENTER)
    return None


def _split(points, canvas, band, ink):
    """A straight line from the top of the writing area to its bottom, with
    ink on both sides."""
    dx = points[-1][0] - points[0][0]
    dy = points[-1][1] - points[0][1]
    if abs(dy) < SPLIT_MIN_HEIGHT_SHARE * canvas[3] or abs(dx) > SPLIT_MAX_LEAN * abs(dy) or not _is_straight(points):
        return None
    x = (points[0][0] + points[-1][0]) / 2
    centres = [(b[0] + b[2]) / 2 for b in ink]
    if min(centres) < x < max(centres):
        return Gesture(SPLIT, x=x)
    return None


def _passes(points, least: float) -> int:
    """How often the stroke crosses from side to side: the runs in one
    horizontal direction that are at least `least` long."""
    passes, direction = 0, 0
    turned_at = reach = points[0][0]        # where the current run began / how far it has got
    for x, _y in points[1:]:
        if direction >= 0 and x > reach or direction <= 0 and x < reach:
            if direction == 0:
                direction = 1 if x > reach else -1
            reach = x
        elif abs(x - reach) >= least * 0.3:     # it has clearly turned round
            if abs(reach - turned_at) >= least:
                passes += 1
            turned_at, reach, direction = reach, x, -direction
    if abs(reach - turned_at) >= least:
        passes += 1
    return passes


def _scratch(points, band, ink):
    x0, y0, x1, y1 = _bounds(points)
    width, height = x1 - x0, y1 - y0
    if width < SCRATCH_MIN_WIDTH * band or width < height:
        return None
    if _passes(points, SCRATCH_PASS_SHARE * width) < SCRATCH_MIN_PASSES:
        return None
    covered = [i for i, b in enumerate(ink) if _covers((x0, y0, x1, y1), b)]
    return Gesture(SCRATCH, strokes=covered) if covered else None


def _covers(scratch, stroke) -> bool:
    """The scratch lies over the middle of the stroke, or over most of it."""
    x0, y0, x1, y1 = scratch
    sx0, sy0, sx1, sy1 = stroke
    if y1 < sy0 or y0 > sy1:
        return False
    if x0 <= (sx0 + sx1) / 2 <= x1 and y0 <= (sy0 + sy1) / 2 <= y1:
        return True
    overlap = min(x1, sx1) - max(x0, sx0)
    return overlap > 0 and overlap >= 0.6 * (sx1 - sx0)

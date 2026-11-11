"""Where things go on the panel.  Pure arithmetic on numbers and tuples, so
that every combination of keys can be checked without a screen.

Rectangles are (x, y, width, height) tuples.
"""

from __future__ import annotations

import itertools
import math

MAX_ROWS = 4


def units(keys):
    """Groups keys that must stay together: [[key, ...], ...]."""
    result = []
    for key in keys:
        if key.joined and result and result[-1][-1].group == key.group:
            result[-1].append(key)
        else:
            result.append([key])
    return result


def arrange(keys, max_rows: int = MAX_ROWS):
    """Splits the keys, in their order, into at most max_rows rows.

    Of all ways to do that it takes the one with the narrowest widest row
    (so the keys take as little room from the writing area as possible),
    then the one that separates the fewest keys of the same group, then the
    most even one.  Returns [[key, ...], ...]."""
    blocks = units(keys)
    if not blocks:
        return []
    weights = [sum(key.weight for key in block) for block in blocks]
    groups = [(block[0].group, block[-1].group) for block in blocks]
    rows = min(max_rows, len(blocks))
    best = None
    for cuts in itertools.combinations(range(1, len(blocks)), rows - 1):
        bounds = (0, *cuts, len(blocks))
        row_weights = [sum(weights[a:b]) for a, b in zip(bounds, bounds[1:])]
        split_groups = sum(1 for cut in cuts if groups[cut - 1][1] == groups[cut][0])
        cost = (max(row_weights), split_groups, sum(w * w for w in row_weights))
        if best is None or cost < best[0]:
            best = (cost, bounds)
    bounds = best[1]
    return [[key for block in blocks[a:b] for key in block] for a, b in zip(bounds, bounds[1:])]


def widest(rows) -> int:
    """Width of the widest row, in units of a normal key."""
    return max((sum(key.weight for key in row) for row in rows), default=0)


def key_block_width(rows, key_width: float, pad: float, limit: float) -> float:
    """Width of the block of keys: as wide as its widest row needs, but not
    more than `limit`."""
    n = widest(rows)
    if n == 0:
        return 0.0
    return min(limit, n * key_width + (n + 1) * pad)


def key_rects(rows, x: float, y: float, w: float, h: float, pad: float) -> dict:
    """{key name: rectangle} for rows of keys inside the block (x, y, w, h).
    Every row fills the whole width; a key's width follows its weight."""
    rects = {}
    if not rows:
        return rects
    row_h = (h - (len(rows) + 1) * pad) / len(rows)
    for r, row in enumerate(rows):
        total = sum(key.weight for key in row)
        unit = (w - (len(row) + 1) * pad) / total
        left = x + pad
        for key in row:
            width = unit * key.weight
            rects[key.name] = (left, y + pad + r * (row_h + pad), width, row_h)
            left += width + pad
    return rects


def row_of_keys(count: int, x: float, y: float, w: float, h: float, gap: float, key_w: float, max_key_w: float):
    """A single row (the symbol row): as many keys of about key_w as fit."""
    if count <= 0 or w <= 0:
        return []
    n = max(1, min(count, int((w + gap) // (key_w + gap))))
    width = min(max_key_w, (w - (n - 1) * gap) / n)
    return [(x + i * (width + gap), y, width, h) for i in range(n)]


def grid(count: int, x: float, y: float, w: float, h: float, gap: float,
         min_key_h: float = 34.0, aspect: float = 1.3, max_rows: int = 6):
    """A grid of `count` equal keys filling (x, y, w, h), row by row.  The
    number of rows is chosen so that the keys come closest to `aspect`
    (width / height) without getting lower than min_key_h."""
    if count <= 0 or w <= 0 or h <= 0:
        return []
    best = None
    for rows in range(1, max_rows + 1):
        key_h = (h - (rows - 1) * gap) / rows
        if rows > 1 and key_h < min_key_h:
            break
        cols = math.ceil(count / rows)
        key_w = (w - (cols - 1) * gap) / cols
        if key_w <= 0:
            continue
        score = abs(math.log(key_w / key_h / aspect))
        if best is None or score < best[0]:
            best = (score, rows, cols, key_w, key_h)
    if best is None:
        return []
    _, rows, cols, key_w, key_h = best
    return [(x + (i % cols) * (key_w + gap), y + (i // cols) * (key_h + gap), key_w, key_h) for i in range(count)]

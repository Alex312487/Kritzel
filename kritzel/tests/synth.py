"""Synthetic handwriting for tests: single-stroke Hershey script fonts turned
into pen strokes, with a little jitter and slant so it is not pixel-perfect."""
import math, random
from HersheyFonts import HersheyFonts
from kritzel.ink import Stroke

def strokes_for(text, font="cursive", height=90.0, x0=40.0, baseline=170.0, jitter=0.6, slant=0.0, seed=1):
    rnd = random.Random(seed)
    hf = HersheyFonts(); hf.load_default_font(font); hf.normalize_rendering(height)
    out = []
    for raw in hf.strokes_for_text(text):
        st = Stroke()
        pts = list(raw)
        for (ax, ay), (bx, by) in zip(pts, pts[1:]):
            n = max(1, int(math.hypot(bx-ax, by-ay) / 4))
            for i in range(n):
                t = i / n
                x = ax + (bx-ax)*t; y = ay + (by-ay)*t
                st.add(x0 + x + slant*y + rnd.gauss(0, jitter), baseline - y + rnd.gauss(0, jitter))
        if pts:
            x, y = pts[-1]; st.add(x0 + x + slant*y, baseline - y)
        if st.points: out.append(st)
    return out

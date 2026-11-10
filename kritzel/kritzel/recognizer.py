"""Offline handwriting recognition.

Runs a PP-OCRv6 text-line recognition model (CTC) with onnxruntime.  The
model is multilingual (English, German and ~45 other Latin-script languages,
plus Chinese/Japanese) and ships its own character table in the ONNX
metadata, so numpy and onnxruntime are the only dependencies.
"""

from __future__ import annotations

import math
import os
import re
from collections import defaultdict

import numpy as np

MODEL_HEIGHT = 48          # input height the model was trained with
MIN_MODEL_WIDTH = 320      # narrower lines are padded up to this width


class Recognizer:
    def __init__(self, model_path: str, threads: int = 2, lexicon=None, scripts: str = "western"):
        import onnxruntime as ort

        opts = ort.SessionOptions()
        opts.intra_op_num_threads = max(1, threads)
        opts.inter_op_num_threads = 1
        opts.log_severity_level = 3
        self.session = ort.InferenceSession(
            model_path, sess_options=opts, providers=["CPUExecutionProvider"]
        )
        self.input_name = self.session.get_inputs()[0].name
        meta = self.session.get_modelmeta().custom_metadata_map
        if "character" not in meta:
            raise RuntimeError(f"{model_path}: no character table in model metadata")
        # index 0 is the CTC blank, the last index is the space character
        self.chars = [""] + meta["character"].splitlines() + [" "]
        self.lexicon = lexicon
        self.model_path = model_path
        # The model also knows ~18,000 CJK characters and assorted symbols.
        # Unless asked otherwise, only Latin-script output is allowed, which
        # removes look-alikes such as fullwidth letters or circled digits.
        self.mask = None
        if scripts != "all":
            limit = 0x100 if scripts == "western" else 0x250
            self.mask = np.array([_is_latin(c, limit) for c in self.chars], dtype=np.float32)
            self.mask[0] = 1.0

    # -- inference ---------------------------------------------------------

    def probabilities(self, gray: np.ndarray) -> np.ndarray:
        """gray: uint8 array (48, W), white paper = 255, ink = 0.
        Returns per-frame class probabilities, shape (T, n_classes)."""
        if gray.shape[0] != MODEL_HEIGHT:
            raise ValueError(f"line image must be {MODEL_HEIGHT}px high")
        h, w = gray.shape
        x = gray.astype(np.float32) / 255.0
        x = (x - 0.5) / 0.5
        width = max(MIN_MODEL_WIDTH, w)
        batch = np.zeros((1, 3, h, width), dtype=np.float32)
        batch[0, :, :, :w] = x[None, :, :]
        out = self.session.run(None, {self.input_name: batch})[0][0]
        if len(self.chars) != out.shape[1]:
            raise RuntimeError(
                f"character table ({len(self.chars)}) does not match model output ({out.shape[1]})"
            )
        if self.mask is not None:
            out = out * self.mask[None, :]
        return out

    def recognize(self, gray: np.ndarray, n_best: int = 5) -> list[tuple[str, float]]:
        """Returns up to n_best (text, confidence) pairs, best first."""
        probs = self.probabilities(gray)
        cands = ctc_beam_search(probs, self.chars, beam_width=16, top_k=5, n_best=max(n_best * 3, 8))
        merged: dict[str, float] = {}
        for text, logp in cands:
            text = _tidy(text)
            if text:
                merged[text] = max(merged.get(text, -math.inf), logp)
        if not merged:
            return []
        scored = []
        for text, logp in merged.items():
            bonus = self.lexicon.bonus(text) if self.lexicon else 0.0
            scored.append((logp + bonus, logp, text))
        scored.sort(reverse=True)
        return [(text, math.exp(min(0.0, logp))) for _, logp, text in scored[:n_best]]


_EXTRA = set("\u0152\u0153\u0160\u0161\u017d\u017e\u0178\u2018\u2019\u201a\u201c\u201d\u201e\u2013\u2014\u2026\u20ac\u2022\u00b0")


def _is_latin(ch: str, limit: int = 0x250) -> bool:
    """western: ASCII + Latin-1 (German, French, Spanish, Nordic, ...);
    latin: additionally Latin Extended (Polish, Czech, Turkish, ...)."""
    return all(ord(c) < limit or c in _EXTRA for c in ch)


def _tidy(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def ctc_beam_search(probs, chars, beam_width=16, top_k=5, n_best=8):
    """CTC prefix beam search over the top_k classes of every frame.

    Returns [(text, log_probability)], best first.  Greedy decoding is the
    special case of the best beam; the remaining beams are what the panel
    offers as alternatives.
    """
    T = probs.shape[0]
    k = min(top_k, probs.shape[1])
    top_idx = np.argpartition(probs, -k, axis=1)[:, -k:]
    top_p = np.take_along_axis(probs, top_idx, axis=1)

    NEG = -math.inf

    def logadd(a, b):
        if a == NEG:
            return b
        if b == NEG:
            return a
        m = max(a, b)
        return m + math.log1p(math.exp(-abs(a - b)))

    # prefix (tuple of class ids) -> (log p ending in blank, log p ending in non-blank)
    beams = {(): (0.0, NEG)}
    for t in range(T):
        idx = top_idx[t]
        ps = top_p[t]
        # frames that are almost certainly blank cannot change the ranking
        blank_pos = np.where(idx == 0)[0]
        if blank_pos.size and ps[blank_pos[0]] > 0.9995:
            lp = math.log(float(ps[blank_pos[0]]))
            beams = {p: (logadd(pb, pnb) + lp, NEG) for p, (pb, pnb) in beams.items()}
            continue
        nxt = defaultdict(lambda: (NEG, NEG))
        for prefix, (pb, pnb) in beams.items():
            total = logadd(pb, pnb)
            for c, p in zip(idx.tolist(), ps.tolist()):
                if p < 1e-5:
                    continue
                lp = math.log(p)
                if c == 0:
                    b, nb = nxt[prefix]
                    nxt[prefix] = (logadd(b, total + lp), nb)
                    continue
                new = prefix + (c,)
                b, nb = nxt[new]
                if prefix and prefix[-1] == c:
                    # a repeated class only starts a new character after a blank
                    nxt[new] = (b, logadd(nb, pb + lp))
                    b2, nb2 = nxt[prefix]
                    nxt[prefix] = (b2, logadd(nb2, pnb + lp))
                else:
                    nxt[new] = (b, logadd(nb, total + lp))
        ranked = sorted(nxt.items(), key=lambda kv: logadd(*kv[1]), reverse=True)
        beams = dict(ranked[:beam_width])
    ranked = sorted(beams.items(), key=lambda kv: logadd(*kv[1]), reverse=True)
    return [("".join(chars[c] for c in prefix), logadd(*score)) for prefix, score in ranked[:n_best]]


class Lexicon:
    """Optional word lists (system hunspell dictionaries) used to break ties
    between recognition candidates.  It never invents text; it only prefers a
    candidate whose words exist over one whose words do not."""

    SEARCH_DIRS = ("/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts")

    def __init__(self, languages, weight: float = 1.5):
        self.words: set[str] = set()
        self.weight = weight
        self.loaded: list[str] = []
        for lang in languages:
            path = self._find(lang)
            if path:
                self._load(path)
                self.loaded.append(path)

    def _find(self, lang: str):
        lang = lang.replace("-", "_")
        for d in self.SEARCH_DIRS:
            if not os.path.isdir(d):
                continue
            exact = os.path.join(d, lang + ".dic")
            if os.path.isfile(exact):
                return exact
            short = lang.split("_")[0]
            for name in sorted(os.listdir(d)):
                if name.endswith(".dic") and name.split("_")[0] == short:
                    return os.path.join(d, name)
        return None

    def _load(self, path: str):
        enc = "utf-8"
        aff = path[:-4] + ".aff"
        try:
            with open(aff, "rb") as f:
                for line in f:
                    if line.startswith(b"SET "):
                        enc = line.split()[1].decode("ascii", "ignore")
                        break
        except OSError:
            pass
        try:
            with open(path, "r", encoding=enc, errors="ignore") as f:
                next(f, None)  # first line is the entry count
                for line in f:
                    word = line.split("/", 1)[0].strip()
                    if len(word) > 1:
                        self.words.add(word)
                        self.words.add(word.lower())
        except OSError:
            pass

    _WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)

    _SUFFIXES = ("s", "es", "e", "en", "er", "n", "ed", "d", "ing", "st", "t", "em", "ern", "ly")

    def known(self, word: str) -> bool:
        for w in (word, word.lower()):
            if w in self.words:
                return True
            for suf in self._SUFFIXES:
                if len(w) > len(suf) + 2 and w.endswith(suf) and w[: -len(suf)] in self.words:
                    return True
        return False

    def bonus(self, text: str) -> float:
        """Score adjustment in log-probability units: every word that is in
        none of the dictionaries costs `weight`.  Splitting or joining words
        is never rewarded, so the picture of the ink still decides."""
        if not self.words:
            return 0.0
        unknown = sum(1 for w in self._WORD.findall(text) if not self.known(w))
        return -self.weight * unknown

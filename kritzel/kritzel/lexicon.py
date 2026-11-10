"""Word lists that help to choose between look-alike readings.

Two sources: the system's spell-check dictionaries (hunspell) for the
language the user writes in, and the user's own word list (names, technical
terms).  A Lexicon does three things with a reading of the recogniser:

    bonus()         prefers a reading whose words exist over one whose words
                    do not
    alternatives()  offers the personal word that is one slip away from what
                    was read ("Kritzal" -> "Kritzel"), and likewise words of
                    the dictionary ("helle" -> "hello" with the English one)
"""

from __future__ import annotations

import os
import re

SEARCH_DIRS = ("/usr/share/hunspell", "/usr/share/myspell", "/usr/share/myspell/dicts")

PERSONAL_BONUS = 0.5        # a personal word is preferred by this share of the weight
CASE_FIX_COST = 0.3         # "kritzel" -> "Kritzel"
EDIT_COST = 1.0             # one wrong, missing or extra letter
LOOKALIKE_COST = 0.8        # a letter mistaken for one that looks similar in handwriting
LENGTH_COST = 1.1           # a letter too many or too few
CORRECTIONS_PER_WORD = 2

# groups of letters that handwriting recognition mixes up
_LOOKALIKES = ("aoec", "nuhm", "ilt", "rv", "uvw", "gqy", "ij", "hkb", "cl", "ft", "sz", "dcl", "mw", "äa", "öo", "üu")
LOOKALIKE = {(a, b) for group in _LOOKALIKES for a in group for b in group if a != b}

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)
# Dictionaries list base forms; these endings make a rough guess at inflected
# ones.  Languages without an entry get all of them.
_SUFFIXES = {
    "en": ("s", "es", "ed", "d", "ing", "ly", "er", "est"),
    "de": ("e", "en", "er", "n", "s", "es", "st", "t", "em", "ern"),
}
_ALL_SUFFIXES = tuple(sorted({suffix for group in _SUFFIXES.values() for suffix in group}))
_files: dict[str, frozenset] = {}       # dictionary path -> words; filled by the recogniser thread
_letters: dict[str, str] = {}           # dictionary path -> the letters its words are made of


def installed_dictionaries() -> list[str]:
    """Language codes with a hunspell dictionary on this system, e.g. ["de_DE", "en_US"]."""
    found = set()
    for d in SEARCH_DIRS:
        try:
            names = os.listdir(d)
        except OSError:
            continue
        found.update(n[:-4] for n in names if n.endswith(".dic") and not n.startswith("hyph_"))
    return sorted(found)


def find_dictionary(lang: str):
    """Path of the dictionary for "de_DE" (or, failing that, any "de_*")."""
    lang = lang.replace("-", "_")
    for d in SEARCH_DIRS:
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


def _read_dictionary(path: str) -> frozenset:
    if path in _files:
        return _files[path]
    # German writes every noun with a capital, and the recogniser often
    # misses capitals: there "haus" has to count as "Haus".  Elsewhere a
    # capitalised entry is a name, and "helle" is not "Helle".
    fold_case = os.path.basename(path).lower().startswith("de")
    encoding = "utf-8"
    try:
        with open(path[:-4] + ".aff", "rb") as f:
            for line in f:
                if line.startswith(b"SET "):
                    encoding = line.split()[1].decode("ascii", "ignore")
                    break
    except OSError:
        pass
    words = set()
    try:
        with open(path, "r", encoding=encoding, errors="ignore") as f:
            next(f, None)               # first line is the entry count
            for line in f:
                word = line.split("/", 1)[0].strip()
                if len(word) > 1:
                    words.add(word)
                    if fold_case:
                        words.add(word.lower())
    except (OSError, LookupError):
        pass
    _files[path] = frozenset(words)
    _letters[path] = "".join(sorted({c for w in words for c in w if c.isalpha() and c.islower()}))
    return _files[path]


def edit_distance_at_most(a: str, b: str, limit: int) -> bool:
    """Levenshtein distance(a, b) <= limit."""
    if abs(len(a) - len(b)) > limit:
        return False
    row = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        prev, row[0] = row[0], i
        for j, cb in enumerate(b, 1):
            prev, row[j] = row[j], min(row[j] + 1, row[j - 1] + 1, prev + (ca != cb))
        if min(row) > limit:
            return False
    return row[-1] <= limit


class Lexicon:
    def __init__(self, languages=(), weight: float = 1.5, personal=(), corrections: str = "suggest"):
        """corrections: what to do with a word that is in no dictionary but
        one slip away from a dictionary word - "off": nothing, "suggest":
        offer the dictionary word as another reading, "auto": prefer it."""
        self.weight = weight
        self.corrections = corrections
        self.loaded: list[str] = []             # dictionary files in use
        self.words: set[str] | frozenset = set()
        sets, suffixes = [], set()
        for lang in languages:
            path = find_dictionary(lang)
            if path and path not in self.loaded:
                sets.append(_read_dictionary(path))
                self.loaded.append(path)
                suffixes.update(_SUFFIXES.get(lang.replace("-", "_").split("_")[0].lower(), _ALL_SUFFIXES))
        self.suffixes = tuple(sorted(suffixes)) or _ALL_SUFFIXES
        if len(sets) == 1:
            self.words = sets[0]
        elif sets:
            self.words = frozenset().union(*sets)
        self.letters = "".join(sorted(set("".join(_letters.get(path, "") for path in self.loaded))))
        self.personal: dict[str, str] = {}      # lower case -> as the user spells it
        for word in personal:
            word = word.strip()
            if word:
                self.personal.setdefault(word.lower(), word)

    def __bool__(self):
        return bool(self.words or self.personal)

    # -- lookups -----------------------------------------------------------

    def is_personal(self, word: str) -> bool:
        return word.lower() in self.personal

    def known(self, word: str) -> bool:
        if self.is_personal(word):
            return True
        for w in (word, word.lower()):
            if w in self.words:
                return True
            for suffix in self.suffixes:
                if len(w) > len(suffix) + 2 and w.endswith(suffix) and w[: -len(suffix)] in self.words:
                    return True
        return False

    def bonus(self, text: str) -> float:
        """Score adjustment in log-probability units: every word that is in
        none of the dictionaries costs `weight`, every personal word earns a
        little.  Splitting or joining words is never rewarded, so the picture
        of the ink still decides."""
        score = 0.0
        for word in _WORD.findall(text):
            spelling = self.personal.get(word.lower())
            if spelling is not None:
                # "kwin" is fine, but the user's own "KWin" is better
                if spelling == word or spelling.islower():
                    score += PERSONAL_BONUS * self.weight
            elif self.words and not self.known(word):
                score -= self.weight
        return score

    # -- personal words ----------------------------------------------------

    def _nearest_personal(self, word: str):
        """(personal spelling, cost) for a word that is not in a dictionary
        but nearly a personal word; None otherwise."""
        lower = word.lower()
        exact = self.personal.get(lower)
        if exact is not None:
            # only add capitals the user's spelling has ("kritzel" -> "Kritzel"),
            # never remove one: the word may start a sentence
            wants_fix = exact != word and exact != lower and not word.isupper()
            return (exact, CASE_FIX_COST) if wants_fix else None
        if len(word) < 4 or self.known(word):
            return None
        limit = 2 if len(word) >= 8 else 1
        best = None
        for key, spelling in self.personal.items():
            if len(key) < 4:
                continue
            for distance in range(1, limit + 1):
                if edit_distance_at_most(lower, key, distance):
                    if best is None or distance < best[1]:
                        best = (spelling, distance)
                    break
        if best is None:
            return None
        return best[0], EDIT_COST * best[1]

    def personalise(self, text: str):
        """If words of `text` are near misses of personal words, returns
        (corrected text, cost); otherwise None."""
        if not self.personal:
            return None
        cost = 0.0

        def fix(match):
            nonlocal cost
            hit = self._nearest_personal(match.group(0))
            if hit is None:
                return match.group(0)
            cost += hit[1]
            return hit[0]

        fixed = _WORD.sub(fix, text)
        return (fixed, cost) if fixed != text else None

    # -- dictionary words --------------------------------------------------

    def _nearest_dictionary_words(self, word: str) -> list[tuple[str, float]]:
        """Up to CORRECTIONS_PER_WORD (dictionary word, cost) for a word that
        is in no dictionary, cheapest first.  The cost says how likely the
        slip is: look-alike letters are the likeliest."""
        if len(word) < 3 or not self.words or self.known(word):
            return []
        lower = word.lower()
        found: dict[str, float] = {}

        def consider(candidate: str, cost: float):
            if candidate in self.words and cost < found.get(candidate, 99.0):
                found[candidate] = cost

        for i, old in enumerate(lower):
            head, tail = lower[:i], lower[i + 1:]
            consider(head + tail, LENGTH_COST)                                  # a letter too many
            for new in self.letters:
                if new != old:
                    consider(head + new + tail, LOOKALIKE_COST if (old, new) in LOOKALIKE else EDIT_COST)
                consider(head + new + old + tail, LENGTH_COST)                  # a letter missing
        for new in self.letters:
            consider(lower + new, LENGTH_COST)
        ranked = sorted(found.items(), key=lambda item: (item[1], item[0]))[:CORRECTIONS_PER_WORD]
        if word[0].isupper():
            ranked = [(w[0].upper() + w[1:], cost) for w, cost in ranked]
        return ranked

    def _correction_penalty(self, cost: float) -> float:
        """How much a corrected reading loses against the reading itself.
        The uncorrected reading already loses `weight` for its unknown word,
        so "suggest" must cost a little more than that and "auto" less."""
        if self.corrections == "auto":
            return 0.5 * self.weight * cost
        return self.weight + 0.2 + 0.1 * cost

    def alternatives(self, text: str) -> list[tuple[str, float]]:
        """Other readings of `text` that the word lists suggest, as
        (text, penalty): near misses of personal words always, of dictionary
        words if corrections are on."""
        result = []
        personal = self.personalise(text)
        if personal is not None:
            result.append(personal)
            text = personal[0]              # dictionary corrections build on it
        if self.corrections == "off" or not self.words:
            return result
        base = personal[1] if personal else 0.0
        unknown = [(m, self._nearest_dictionary_words(m.group(0))) for m in _WORD.finditer(text)]
        unknown = [(m, fixes) for m, fixes in unknown if fixes]
        if not unknown:
            return result
        # every unknown word replaced by its nearest dictionary word ...
        pieces, penalty, end = [], base, 0
        for m, fixes in unknown:
            pieces += [text[end:m.start()], fixes[0][0]]
            penalty += self._correction_penalty(fixes[0][1])
            end = m.end()
        result.append(("".join(pieces) + text[end:], penalty))
        # ... and for a single unknown word also its second nearest
        if len(unknown) == 1 and len(unknown[0][1]) > 1:
            m, fixes = unknown[0]
            result.append((text[:m.start()] + fixes[1][0] + text[m.end():], base + self._correction_penalty(fixes[1][1])))
        return result

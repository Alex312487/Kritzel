"""Words learnt from the user's corrections.

When the user taps another suggestion, the words of that reading which the
reading typed at first did not have are noted.  A word picked LEARNED_AFTER
times counts like a personal word from then on (lexicon.Lexicon): it is
preferred when the writing could be read that way.  A pick the user goes
back on is taken back (retract), so that trying the suggestions out teaches
nothing.

The words live in ~/.local/state/kritzel/learned.json, readable by the user
alone.  What is kept is deliberately little: single words made of letters,
a limited number of them, and nothing at all from fields that hold no prose
(passwords, addresses, numbers) - the panel does not call note() there.
"""

from __future__ import annotations

import json
import os
import re

from .config import STATE_DIR, write_atomically

LEARNED_FILE = os.path.join(STATE_DIR, "learned.json")
LEARNED_AFTER = 2           # picks until a word counts
MAX_WORDS = 400             # the least picked, oldest words make room for new ones
MAX_WORD_LENGTH = 40
MAX_COUNT = 99
MAX_FILE_BYTES = 200_000

_WORD = re.compile(r"[^\W\d_]{2,}", re.UNICODE)


def words_of(text: str) -> list[str]:
    return _WORD.findall(text)


def _acceptable(word) -> bool:
    return isinstance(word, str) and len(word) <= MAX_WORD_LENGTH and _WORD.fullmatch(word) is not None


class LearnedWords:
    def __init__(self, path: str = LEARNED_FILE):
        self.path = path
        self.counts: dict[str, int] = {}        # word -> how often it was picked; oldest first
        self.load()

    def load(self):
        """Reads the file.  It is only as trustworthy as any file: whatever
        is not a word with a count is left out."""
        self.counts = {}
        try:
            if os.path.getsize(self.path) > MAX_FILE_BYTES:
                return
            with open(self.path, encoding="utf-8") as f:
                stored = json.load(f)
        except (OSError, ValueError, RecursionError):
            return
        words = stored.get("words") if isinstance(stored, dict) else None
        if not isinstance(words, dict):
            return
        for word, count in words.items():
            if _acceptable(word) and isinstance(count, int) and not isinstance(count, bool) and count > 0:
                self.counts[word] = min(count, MAX_COUNT)
            if len(self.counts) >= MAX_WORDS:
                break

    def save(self):
        try:
            write_atomically(self.path, json.dumps({"words": self.counts}, ensure_ascii=False))
        except OSError:
            pass                                # learning is a convenience; the panel goes on without

    @staticmethod
    def _corrections(chosen: str, instead_of: str, keep_capitals: bool) -> list[str]:
        """The words that picking `chosen` over `instead_of` speaks for."""
        had = set(words_of(instead_of))
        words = []
        for position, word in enumerate(words_of(chosen)):
            if word in had or not _acceptable(word):
                continue
            if position == 0 and not keep_capitals and word[1:].islower():
                word = word.lower()
            words.append(word)
        return words

    def note(self, chosen: str, instead_of: str, keep_capitals: bool = True) -> bool:
        """The user picked the reading `chosen` where `instead_of` had been
        typed.  keep_capitals=False: the reading starts a sentence, so the
        capital of its first word says nothing about the word.  Returns True
        if the words that count have changed."""
        changed = False
        for word in self._corrections(chosen, instead_of, keep_capitals):
            count = min(self.counts.pop(word, 0) + 1, MAX_COUNT)    # popped: the newest are last
            self.counts[word] = count
            changed = changed or count == LEARNED_AFTER
        self._make_room()
        self.save()
        return changed

    def retract(self, chosen: str, instead_of: str, keep_capitals: bool = True) -> bool:
        """Takes back note() with the same arguments: the user has picked
        yet another reading, or gone back to the first.  Returns True if the
        words that count have changed."""
        changed = False
        for word in self._corrections(chosen, instead_of, keep_capitals):
            count = self.counts.get(word, 0) - 1
            if count < 0:
                continue
            changed = changed or count == LEARNED_AFTER - 1
            if count:
                self.counts[word] = count
            else:
                del self.counts[word]
        self.save()
        return changed

    def _make_room(self):
        excess = len(self.counts) - MAX_WORDS
        if excess > 0:
            # sorted() is stable: among equally often picked words the oldest go first
            for word in sorted(self.counts, key=self.counts.get)[:excess]:
                del self.counts[word]

    def words(self) -> list[str]:
        """The words that count."""
        return [word for word, count in self.counts.items() if count >= LEARNED_AFTER]

    def forget(self, words) -> None:
        for word in words:
            self.counts.pop(word, None)
        self.save()

    def forget_all(self) -> None:
        self.counts = {}
        self.save()

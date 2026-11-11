"""What the panel typed, newest last, so that it can be swapped or undone.

Every recognised chunk of writing is one Insertion.  The newest one is what
the suggestions belong to and what the word key removes.  Once it is
removed, the one before it is the newest again and its suggestions come
back: the user can walk back through the last few words.

The history only describes the end of the text before the cursor.  Anything
else that changes the text (a key, a paste, the user's own editing) empties
it.
"""

from __future__ import annotations

from .compose import Context

HISTORY_LENGTH = 10


class Insertion:
    def __init__(self, span: str, own_length: int, context: Context, trailing_space: bool,
                 candidates=(), selected: int = -1, ate_space: bool = False):
        self.span = span                    # the text before the cursor that a swap replaces
        self.own_length = own_length        # how much of it this insertion typed; the rest is
        #                                     the automatic space of the insertion before
        self.context = context              # the text field before it; another reading is
        #                                     composed for the same situation
        self.trailing_space = trailing_space    # it ends with an automatic space
        self.candidates = list(candidates)  # the readings that were offered
        self.selected = selected            # index of the one that is in the text field
        self.typed_first = selected         # index of the one the panel typed by itself
        self.ate_space = ate_space          # it took the place of the automatic space before it
        self.seen_in_field = False          # the application has reported it back
        self.restored = False               # it is the newest again because a later one was undone

    def swapped(self, span: str, own_length: int, trailing_space: bool, selected: int, ate_space: bool) -> "Insertion":
        """This insertion with another of its readings in the text field."""
        other = Insertion(span, own_length, self.context, trailing_space, self.candidates, selected, ate_space)
        other.typed_first = self.typed_first
        return other


class History:
    def __init__(self, length: int = HISTORY_LENGTH):
        self.length = length
        self._items: list[Insertion] = []

    def __len__(self):
        return len(self._items)

    @property
    def last(self) -> Insertion | None:
        return self._items[-1] if self._items else None

    def add(self, insertion: Insertion):
        self._items.append(insertion)
        del self._items[: -self.length]

    def replace_last(self, insertion: Insertion):
        self._items[-1] = insertion

    def clear(self):
        self._items.clear()

    def undo(self) -> Insertion | None:
        """The newest insertion has been deleted from the text field.
        Returns the one before it, which is the newest now, described as the
        text field holds it after that deletion."""
        if not self._items:
            return None
        gone = self._items.pop()
        previous = self.last
        if previous is None:
            return None
        if gone.ate_space:
            if not previous.trailing_space:         # cannot be: whose space was it then?
                self.clear()
                return None
            # its automatic space went with the insertion that had replaced it
            previous.span = previous.span[:-1]
            previous.own_length -= 1
            previous.trailing_space = False
        previous.seen_in_field = False
        previous.restored = True
        return previous

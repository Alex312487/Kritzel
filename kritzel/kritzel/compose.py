"""From a recognised reading to the text that is typed: automatic spaces
and capital letters.  Pure functions, no state.

Automatic spaces come in two flavours (setting `auto_space`):

    after    "hello" is typed as "hello ".  Writing "," next removes that
             space again: "hello, ".  Nothing is ever put in front of a
             word, so a word can be continued or repaired letter by letter.
    before   "world" is typed as " world" if the text before the cursor
             needs it.  (The behaviour up to version 1.3.)
"""

from __future__ import annotations

NO_SPACE_AFTER = set("([{<\"'/\\@#-_„“‚‘–—")
NO_SPACE_BEFORE = set(".,;:!?)]}%\"'…’”")
CLOSES_UP = set(".,;:!?)]}%…")          # symbol keys that take the place of an automatic space
SENTENCE_END = set(".!?")


class Context:
    """What is known about the text field at the moment something is typed."""

    def __init__(self, before: str = "", before_is_known: bool = False, space_pending: bool = False):
        self.before = before                    # text before the cursor, as far as we know it
        self.before_is_known = before_is_known  # the application reported it (it is not a guess)
        self.space_pending = space_pending      # `before` ends with an automatic space of ours


class Typed:
    """How one reading is typed."""

    def __init__(self, text: str, eats_space: bool = False, trailing_space: bool = False):
        self.text = text                        # goes into the text field
        self.eats_space = eats_space            # the automatic space before the cursor goes first
        self.trailing_space = trailing_space    # text ends with an automatic space


def needs_space(before: str, text: str) -> bool:
    """Should a space go between the existing text and the new chunk?"""
    if not before or not text:
        return False
    if before[-1].isspace() or before[-1] in NO_SPACE_AFTER:
        return False
    return text[0] not in NO_SPACE_BEFORE


def starts_sentence(context: Context) -> bool:
    before = context.before.rstrip(" ")
    if not before:
        return context.before_is_known          # an empty field, not an unknown one
    return before[-1] in SENTENCE_END or before[-1] == "\n"


def compose(reading: str, context: Context, auto_space: str = "after", capitalize: bool = False) -> Typed:
    """auto_space is "after", "before" or "off"."""
    if not reading:
        return Typed("")
    if capitalize and reading[0].islower() and starts_sentence(context):
        reading = reading[0].upper() + reading[1:]
    if auto_space == "after":
        eats = context.space_pending and reading[0] in NO_SPACE_BEFORE
        trailing = reading[-1] not in NO_SPACE_AFTER
        return Typed(reading + (" " if trailing else ""), eats, trailing)
    if auto_space == "before" and needs_space(context.before, reading):
        return Typed(" " + reading)
    return Typed(reading)


def compose_symbol(symbol: str, context: Context, auto_space: str = "after") -> Typed:
    """A key of the symbol row.  In running text ("hello " with its automatic
    space) a closing mark takes the place of that space: "hello, "."""
    if auto_space == "after" and context.space_pending and symbol and symbol[0] in CLOSES_UP:
        return Typed(symbol + " ", eats_space=True, trailing_space=True)
    return Typed(symbol)

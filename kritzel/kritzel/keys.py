"""The keys of the panel: which exist, in which order they are laid out and
what each one does.  Adding a key means adding a line to KEYS (and, for a
new kind of action, a branch in Panel._do_key); the settings window lists it
by itself.

Every key except Backspace and Settings can be switched off; the `keys`
setting lists the switched-on OPTIONS.
"""

from __future__ import annotations

# what a key does: (kind, argument)
KEYSYM = "keysym"       # press a key that needs no modifier; argument: name in textfield.KEYSYMS
SHORTCUT = "shortcut"   # a key combination; argument: name in fakekeys.SHORTCUTS
TEXT = "text"           # type the argument
COMMAND = "command"     # something the panel does itself; argument: name of the command

# groups: keys of a group stay in one row if at all possible (layout.arrange)
EDIT, CURSOR, CLIPBOARD, SPACE, PANEL = "edit", "cursor", "clipboard", "space", "panel"


class Key:
    def __init__(self, name, group, action, option=None, label="", caption="", weight=1, repeats=False, joined=False):
        self.name = name            # also the name of its icon in ui.py
        self.group = group
        self.action = action
        self.option = option        # the entry of the `keys` setting that switches it on; None = always there
        self.label = label          # text on the key instead of an icon
        self.caption = caption      # small text under the icon
        self.weight = weight        # width in units of a normal key
        self.repeats = repeats      # acts when pressed and again while held
        self.joined = joined        # must not be separated from the key before it


# in layout order: rows are filled from this list, left to right, top to bottom
KEYS = [
    Key("backspace", EDIT, (KEYSYM, "backspace"), repeats=True),
    Key("delword", EDIT, (COMMAND, "delete_word"), "word", caption="word"),
    Key("delete", EDIT, (KEYSYM, "delete"), "delete", label="Del", repeats=True),
    Key("tab", EDIT, (KEYSYM, "tab"), "tab", label="Tab"),
    Key("enter", EDIT, (KEYSYM, "enter"), "enter"),
    Key("home", CURSOR, (KEYSYM, "home"), "home_end", label="Home"),
    Key("left", CURSOR, (KEYSYM, "left"), "arrows", repeats=True),
    Key("up", CURSOR, (KEYSYM, "up"), "arrows", repeats=True, joined=True),
    Key("down", CURSOR, (KEYSYM, "down"), "arrows", repeats=True, joined=True),
    Key("right", CURSOR, (KEYSYM, "right"), "arrows", repeats=True, joined=True),
    Key("end", CURSOR, (KEYSYM, "end"), "home_end", label="End"),
    Key("copy", CLIPBOARD, (SHORTCUT, "copy"), "copy", caption="copy"),
    Key("cut", CLIPBOARD, (SHORTCUT, "cut"), "cut", caption="cut", joined=True),
    Key("paste", CLIPBOARD, (SHORTCUT, "paste"), "paste", caption="paste", joined=True),
    Key("space", SPACE, (TEXT, " "), "space", weight=2),
    Key("lang", PANEL, (COMMAND, "language"), "language", caption="language"),
    Key("symbols", PANEL, (COMMAND, "symbols"), "symbols", caption="symbols"),
    Key("keyboard", PANEL, (COMMAND, "keyboard"), "keyboard", caption="keys"),
    Key("float", PANEL, (COMMAND, "float"), "float", caption="float"),
    Key("settings", PANEL, (COMMAND, "settings"), caption="settings"),
    Key("hide", PANEL, (COMMAND, "hide"), "hide", caption="hide"),
]
KEY = {key.name: key for key in KEYS}

# option -> (title, explanation) for the settings window, in the order shown there
OPTIONS = {
    "enter": ("Enter", ""),
    "space": ("Space", "With automatic spaces you rarely need it."),
    "word": ("Delete word", "Removes what was typed last, or the word before the cursor."),
    "arrows": ("Arrow keys", "Move the cursor."),
    "copy": ("Copy", ""),
    "paste": ("Paste", ""),
    "cut": ("Cut", ""),
    "home_end": ("Home and End", "Jump to the start or end of the line."),
    "delete": ("Delete", "Removes the character after the cursor."),
    "tab": ("Tab", ""),
    "language": ("Language", "Chooses the language you are writing in."),
    "symbols": ("Symbol keyboard", "Swaps the writing area for a keyboard of digits and symbols."),
    "keyboard": ("Normal keyboard", "Switches to Plasma's ordinary on-screen keyboard."),
    "float": ("Float / dock", "Turns the panel into a small window you can drag around."),
    "hide": ("Hide", "Puts the panel away until the next text field is tapped."),
}
DEFAULT_OPTIONS = ("enter", "space", "word", "arrows", "copy", "paste", "language", "symbols", "keyboard",
                   "float", "hide")


def parse_options(text: str) -> list[str]:
    """The `keys` setting -> the known options in it, without duplicates."""
    seen = []
    for word in text.replace(",", " ").split():
        word = word.lower()
        if word in OPTIONS and word not in seen:
            seen.append(word)
    return seen


def enabled_keys(options) -> list[Key]:
    """The keys on the panel, in layout order, for the switched-on options."""
    options = set(options)
    return [key for key in KEYS if key.option is None or key.option in options]

"""User settings and the KDE colours/fonts.

    ~/.config/kritzel/config.ini   settings (written by `kritzel settings`, or by hand)
    ~/.config/kritzel/words.txt    personal words, one per line
    ~/.local/state/kritzel/        things the program remembers itself
"""

from __future__ import annotations

import configparser
import json
import os
import re

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _xdg(var: str, default: str) -> str:
    return os.environ.get(var) or os.path.expanduser(default)


CONFIG_DIR = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "kritzel")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")
WORDS_FILE = os.path.join(CONFIG_DIR, "words.txt")
STATE_DIR = os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state"), "kritzel")
STATE_FILE = os.path.join(STATE_DIR, "state.json")

DEFAULT_SYMBOLS = ". , ? ! ' \" - : ; ( ) @ / & + = % # * _ € $ < > [ ] { } \\ | ~ ^"
# the symbol keyboard: four rows of eleven on a panel of standard height
DEFAULT_SYMBOL_KEYS = ("1 2 3 4 5 6 7 8 9 0 = "
                       ". , ? ! ' \" - : ; ( ) "
                       "@ # € $ % & + * / \\ _ "
                       "< > [ ] { } | ~ ^ ° §")
DEFAULT_KEYS = "enter space word arrows copy paste language symbols keyboard float hide"


class Option:
    """One setting: where it lives in the file, its type, default and limits.

    kind is "int", "float", "bool", "choice" or "text".  For a choice,
    `legacy` maps values that older versions wrote to today's."""

    def __init__(self, section, key, kind, default, lo=None, hi=None, choices=(), legacy=None):
        self.section, self.key, self.kind, self.default = section, key, kind, default
        self.lo, self.hi, self.choices, self.legacy = lo, hi, choices, legacy or {}

    def parse(self, text: str):
        """The value for `text` from the file; the default if it makes no sense."""
        text = text.strip()
        try:
            if self.kind == "bool":
                return text.lower() in ("1", "true", "yes", "on")
            if self.kind == "int":
                return max(self.lo, min(self.hi, int(text)))
            if self.kind == "float":
                return max(self.lo, min(self.hi, float(text)))
            if self.kind == "choice":
                text = self.legacy.get(text.lower(), text.lower())
                return text if text in self.choices else self.default
        except ValueError:
            return self.default
        return text

    def format(self, value) -> str:
        """The text for `value` in the file."""
        if self.kind == "bool":
            return "true" if value else "false"
        if self.kind == "float":
            return f"{value:.2f}".rstrip("0").rstrip(".")
        return str(value)


# Every setting there is.  To add one: a line here, a commented line in
# data/config.ini, a widget in settings.py, and the code that uses cfg.<key>.
OPTIONS = [
    # -- [panel]: how the panel looks and when it appears
    Option("panel", "height", "int", 280, 160, 900),                # logical pixels
    Option("panel", "max_width", "int", 0, 0, 10000),               # docked; 0 = full screen width
    Option("panel", "float_width", "int", 760, 420, 10000),
    Option("panel", "show", "choice", "touch", choices=("touch", "always")),
    Option("panel", "remember_floating", "bool", False),            # float again after a restart
    # -- [keys]: what is on the panel besides the writing area
    Option("keys", "keys", "text", DEFAULT_KEYS),                   # the optional keys that are shown (see keys.py)
    Option("keys", "keys_side", "choice", "right", choices=("right", "left")),
    Option("keys", "key_size", "choice", "normal", choices=("small", "normal", "large")),
    Option("keys", "key_repeat", "bool", True),                     # Backspace and arrows repeat while held
    Option("keys", "symbol_row", "bool", False),                    # a thin row of symbol keys above the writing area
    Option("keys", "symbols", "text", DEFAULT_SYMBOLS),             # its keys, separated by spaces
    Option("keys", "symbol_keys", "text", DEFAULT_SYMBOL_KEYS),     # the keys of the symbol keyboard
    Option("keys", "symbols_stay", "bool", True),                   # false: back to writing after one symbol
    # -- [look]
    Option("look", "suggestions", "int", 5, 1, 8),                  # how many readings are offered
    Option("look", "pen_width", "float", 2.6, 1.0, 8.0),
    Option("look", "ink_color", "text", ""),                        # "" = the theme's text colour, or #rrggbb
    Option("look", "guide_line", "bool", True),
    # -- [input]: pen and fingers
    Option("input", "finger_writes", "bool", True),                 # false: fingers only press buttons
    Option("input", "palm_rejection", "bool", True),                # fingers cannot write while the pen is near
    # -- [recognition]: from ink to text
    Option("recognition", "delay_ms", "int", 800, 150, 5000),       # pause after the last stroke
    Option("recognition", "scripts", "choice", "western", choices=("western", "latin", "all")),
    Option("recognition", "languages", "text", "auto"),             # hunspell dictionaries, e.g. "en_US, de_DE"
    Option("recognition", "language_button", "choice", "list", choices=("list", "next")),
    Option("recognition", "language_all", "bool", False),           # "all my languages together" is a choice too
    Option("recognition", "language_letters", "bool", True),        # only the letters of the language in use
    Option("recognition", "dictionary_weight", "float", 1.5, 0.0, 10.0),
    Option("recognition", "corrections", "choice", "suggest", choices=("off", "suggest", "auto")),
    Option("recognition", "auto_insert", "bool", True),             # false: tap a suggestion to type it
    Option("recognition", "auto_space", "choice", "after", choices=("after", "before", "off"),
           legacy={"true": "after", "yes": "after", "on": "after", "1": "after",
                   "false": "off", "no": "off", "0": "off"}),
    Option("recognition", "capitalize", "bool", False),             # capital letter at the start of a sentence
    Option("recognition", "replace", "choice", "auto", choices=("auto", "keys")),
    Option("recognition", "model", "text", "small"),                # small | /path/to/model.onnx
    Option("recognition", "threads", "int", 2, 1, 16),
]
OPTION = {o.key: o for o in OPTIONS}
# settings that older versions kept in another section of the file.  (The
# symbol row moved too but is deliberately not listed: version 1.3 wrote
# "on" into every file, and since 1.4 it is off unless asked for.)
MOVED = {"pen_width": "panel", "symbols": "panel"}


class Config:
    """The settings, one attribute per Option key (cfg.height, cfg.show, ...)."""

    def __init__(self, path: str = CONFIG_FILE):
        cp = configparser.ConfigParser(interpolation=None)
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, OSError, UnicodeDecodeError):
            pass
        for o in OPTIONS:
            raw = cp.get(o.section, o.key, fallback=None)
            if raw is None and o.key in MOVED:
                raw = cp.get(MOVED[o.key], o.key, fallback=None)
            setattr(self, o.key, o.default if raw is None else o.parse(raw))
        if not re.fullmatch(r"#[0-9a-fA-F]{6}", self.ink_color):
            self.ink_color = ""

    def text(self, key: str) -> str:
        """The value of a setting as it is written in the file."""
        return OPTION[key].format(getattr(self, key))

    def symbol_row_keys(self) -> list[str]:
        """The keys of the symbol row; none if the row is switched off."""
        if not self.symbol_row:
            return []
        return self.symbols.split()[:60] or DEFAULT_SYMBOLS.split()

    def symbol_keyboard_keys(self) -> list[str]:
        return self.symbol_keys.split()[:120] or DEFAULT_SYMBOL_KEYS.split()

    def model_path(self) -> str:
        m = self.model
        if os.sep in m or m.endswith(".onnx"):
            return os.path.expanduser(m)
        return os.path.join(APP_DIR, "models", f"PP-OCRv6_rec_{m}.onnx")

    def dictionary_languages(self) -> list[str]:
        if self.languages.lower() in ("", "none", "off"):
            return []
        if self.languages.lower() != "auto":
            return [s.strip() for s in self.languages.replace(";", ",").split(",") if s.strip()]
        # auto: the session language plus English
        langs = []
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            v = os.environ.get(var, "").split(".")[0]
            if v and v not in ("C", "POSIX"):
                langs.append(v)
                break
        if not any(l.startswith("en") for l in langs):
            langs.append("en_US")
        return langs


def save_config(values: dict[str, str], path: str = CONFIG_FILE):
    """Writes the settings file from {key: text}; keys that are left out
    keep their defaults."""
    lines = ["# Kritzel settings, written by `kritzel settings`.",
             "# The file may also be edited by hand; `kritzel reload` applies it.",
             "# data/config.ini in the installation explains every line."]
    section = None
    for o in OPTIONS:
        if o.key not in values:
            continue
        if o.section != section:
            section = o.section
            lines += ["", f"[{section}]"]
        lines.append(f"{o.key} = {values[o.key]}")
    _write_atomically(path, "\n".join(lines) + "\n")


def load_words(path: str = WORDS_FILE) -> list[str]:
    """The personal word list."""
    try:
        with open(path, encoding="utf-8") as f:
            words = [line.strip() for line in f]
    except (OSError, UnicodeDecodeError):
        return []
    seen, result = set(), []
    for word in words:
        if word and not word.startswith("#") and word not in seen:
            seen.add(word)
            result.append(word)
    return result


def save_words(words, path: str = WORDS_FILE):
    _write_atomically(path, "".join(w + "\n" for w in words))


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError):
        return {}


def save_state(state: dict):
    try:
        _write_atomically(STATE_FILE, json.dumps(state))
    except OSError:
        pass


def _write_atomically(path: str, text: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)


# -- KDE look ----------------------------------------------------------------

BREEZE_LIGHT = {
    "window": (239, 240, 241),
    "view": (255, 255, 255),
    "text": (35, 38, 41),
    "button": (252, 252, 252),
    "accent": (61, 174, 233),
    "accent_text": (255, 255, 255),
    "inactive": (112, 125, 138),
}


def _kde_ini(path: str):
    cp = configparser.RawConfigParser(strict=False, delimiters=("=",))
    cp.optionxform = str
    try:
        cp.read(path, encoding="utf-8")
    except (configparser.Error, OSError, UnicodeDecodeError):
        return None
    return cp


def _rgb(cp, section, key):
    try:
        parts = [int(x) for x in cp.get(section, key).split(",")[:3]]
        if len(parts) == 3:
            return tuple(max(0, min(255, p)) for p in parts)
    except (configparser.Error, ValueError, AttributeError):
        pass
    return None


class Theme:
    """Colours and UI font of the running Plasma session, read from kdeglobals
    (falling back to the named colour scheme, then to Breeze Light)."""

    def __init__(self):
        self.colors = dict(BREEZE_LIGHT)
        self.font_family = "Noto Sans"
        self.path = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "kdeglobals")
        self.mtime = None
        self.reload()

    def changed(self) -> bool:
        try:
            return os.path.getmtime(self.path) != self.mtime
        except OSError:
            return False

    def reload(self):
        try:
            self.mtime = os.path.getmtime(self.path)
        except OSError:
            self.mtime = None
        sources = []
        g = _kde_ini(self.path)
        if g is not None:
            sources.append(g)
            try:
                self.font_family = g.get("General", "font").split(",")[0] or self.font_family
            except configparser.Error:
                pass
            try:
                scheme = g.get("General", "ColorScheme")
            except configparser.Error:
                scheme = "BreezeLight"
            for base in (_xdg("XDG_DATA_HOME", "~/.local/share"), "/usr/share"):
                s = _kde_ini(os.path.join(base, "color-schemes", scheme + ".colors"))
                if s is not None and s.sections():
                    sources.append(s)
                    break
        wanted = {
            "window": ("Colors:Window", "BackgroundNormal"),
            "view": ("Colors:View", "BackgroundNormal"),
            "text": ("Colors:Window", "ForegroundNormal"),
            "button": ("Colors:Button", "BackgroundNormal"),
            "accent": ("Colors:Selection", "BackgroundNormal"),
            "accent_text": ("Colors:Selection", "ForegroundNormal"),
            "inactive": ("Colors:Window", "ForegroundInactive"),
        }
        colors = dict(BREEZE_LIGHT)
        for name, (section, key) in wanted.items():
            for src in sources:
                c = _rgb(src, section, key)
                if c:
                    colors[name] = c
                    break
        self.colors = colors

"""User settings and the KDE colours/fonts.

    ~/.config/kritzel/config.ini   settings (written by `kritzel settings`, or by hand)
    ~/.config/kritzel/words.txt    personal words, one per line
    ~/.local/state/kritzel/        things the program remembers itself
"""

from __future__ import annotations

import configparser
import json
import math
import os
import re
import tempfile

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def xdg_dir(var: str, default: str) -> str:
    """The directory an XDG_* variable names.  A relative path is not valid
    there (XDG Base Directory specification) and is ignored: it would mean
    another directory wherever the program happens to be started."""
    value = os.environ.get(var, "")
    return value if os.path.isabs(value) else os.path.expanduser(default)


CONFIG_DIR = os.path.join(xdg_dir("XDG_CONFIG_HOME", "~/.config"), "kritzel")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.ini")
WORDS_FILE = os.path.join(CONFIG_DIR, "words.txt")
STATE_DIR = os.path.join(xdg_dir("XDG_STATE_HOME", "~/.local/state"), "kritzel")
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
                value = float(text)
                if math.isnan(value):
                    return self.default
                return max(self.lo, min(self.hi, value))
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
    Option("look", "hide_passwords", "bool", True),                 # dots instead of readings in password fields
    # -- [input]: pen and fingers
    Option("input", "finger_writes", "bool", True),                 # false: fingers only press buttons
    Option("input", "palm_rejection", "bool", True),                # fingers cannot write while the pen is near
    Option("input", "gestures", "bool", True),                      # scratch out, split, delete word, space, enter
    # -- [recognition]: from ink to text
    Option("recognition", "delay_ms", "int", 800, 150, 5000),       # pause after the last stroke
    Option("recognition", "delay_auto", "bool", False),             # fit that pause to the writer (pace.py)
    Option("recognition", "preview", "bool", True),                 # show the reading while the pause runs
    Option("recognition", "scripts", "choice", "western", choices=("western", "latin", "all")),
    Option("recognition", "languages", "text", "auto"),             # hunspell dictionaries, e.g. "en_US, de_DE"
    Option("recognition", "language_button", "choice", "list", choices=("list", "next")),
    Option("recognition", "language_all", "bool", False),           # "all my languages together" is a choice too
    Option("recognition", "language_letters", "bool", True),        # only the letters of the language in use
    Option("recognition", "dictionary_weight", "float", 1.5, 0.0, 10.0),
    Option("recognition", "corrections", "choice", "suggest", choices=("off", "suggest", "auto")),
    Option("recognition", "context", "bool", True),                 # the text before the cursor helps to read
    Option("recognition", "number_fields", "bool", True),           # number fields: only digits are recognised
    Option("recognition", "learn", "bool", True),                   # learn the words picked from the suggestions
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
# Settings that describe this computer rather than the user's taste; they stay
# behind when settings are exported or imported (transfer.py).  `model` also
# names a file the program loads, which no file from elsewhere should decide.
MACHINE_SETTINGS = ("model", "threads")
# settings that older versions kept in another section of the file.  (The
# symbol row moved too but is deliberately not listed: version 1.3 wrote
# "on" into every file, and since 1.4 it is off unless asked for.)
MOVED = {"pen_width": "panel", "symbols": "panel"}

# "de", "de_DE", "en-GB", "sr_Latn_RS", "en_GB-large": whatever a dictionary may be
# called, but nothing with a path separator in it and nothing starting with a dot
_LANGUAGE_CODE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.@+-]{0,63}")


def is_language_code(code: str) -> bool:
    return _LANGUAGE_CODE.fullmatch(code) is not None


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
            self._set(o, raw)

    @classmethod
    def from_values(cls, values: dict[str, str]) -> "Config":
        """The settings for {key: text as in the file}; keys that are left
        out have their defaults."""
        cfg = cls(os.devnull)
        for key, text in values.items():
            cfg._set(OPTION[key], text)
        return cfg

    def _set(self, option: Option, raw):
        value = option.default if raw is None else option.parse(raw)
        if option.key == "ink_color" and not re.fullmatch(r"#[0-9a-fA-F]{6}", value):
            value = ""
        setattr(self, option.key, value)

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
        """The language codes of the `languages` setting.  Anything that is
        not shaped like a language code is left out: the codes become file
        names (see lexicon.find_dictionary)."""
        if self.languages.lower() in ("", "none", "off"):
            return []
        if self.languages.lower() != "auto":
            codes = (s.strip() for s in self.languages.replace(";", ",").split(","))
            return [code for code in codes if is_language_code(code)]
        # auto: the session language plus English
        langs = []
        for var in ("LC_ALL", "LC_MESSAGES", "LANG"):
            v = os.environ.get(var, "").split(".")[0].split("@")[0]
            if v and v not in ("C", "POSIX"):
                if is_language_code(v):
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
        lines.append(f"{o.key} = {one_line(values[o.key])}")
    write_atomically(path, "\n".join(lines) + "\n")


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
    words = (one_line(w) for w in words)
    write_atomically(path, "".join(w + "\n" for w in words if w))


def load_state() -> dict:
    try:
        with open(STATE_FILE, encoding="utf-8") as f:
            state = json.load(f)
        return state if isinstance(state, dict) else {}
    except (OSError, ValueError, RecursionError):
        return {}


def save_state(state: dict):
    try:
        write_atomically(STATE_FILE, json.dumps(state))
    except OSError:
        pass


def one_line(text: str) -> str:
    """`text` with line breaks and other control characters turned into spaces.  A value
    with a line break in it would otherwise add lines of its own to the
    file, that is: other settings."""
    return "".join(" " if ord(c) < 32 or ord(c) == 127 else c for c in str(text)).strip()


def ensure_private_dir(path: str):
    """Creates a directory of Kritzel's own, for the user alone."""
    os.makedirs(path, mode=0o700, exist_ok=True)


def write_atomically(path: str, text: str):
    """Replaces the file at `path` in one step, so that a reader never sees
    half of it.  The file is readable by the user alone: the settings name
    the user's languages and words, the state files are nobody's business.

    A file that is a symbolic link (settings kept in a dotfiles repository)
    stays one; its target is replaced."""
    path = os.path.realpath(path)
    directory = os.path.dirname(path)
    ensure_private_dir(directory)
    # a name nobody can predict, created exclusively and with mode 0600
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=os.path.basename(path) + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


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


def _is_file_name(name: str) -> bool:
    return bool(name) and name == os.path.basename(name) and name not in (".", "..") and "\0" not in name


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
        self.path = os.path.join(xdg_dir("XDG_CONFIG_HOME", "~/.config"), "kdeglobals")
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
            # the name becomes a file name; one with a path in it is not a scheme
            bases = (xdg_dir("XDG_DATA_HOME", "~/.local/share"), "/usr/share") if _is_file_name(scheme) else ()
            for base in bases:
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

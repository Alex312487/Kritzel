"""User settings (~/.config/kritzel/config.ini) and the KDE colours/fonts."""

from __future__ import annotations

import configparser
import os

APP_DIR = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _xdg(var: str, default: str) -> str:
    return os.environ.get(var) or os.path.expanduser(default)


CONFIG_FILE = os.path.join(_xdg("XDG_CONFIG_HOME", "~/.config"), "kritzel", "config.ini")
STATE_DIR = os.path.join(_xdg("XDG_STATE_HOME", "~/.local/state"), "kritzel")

DEFAULTS = {
    "panel": {
        "height": "280",            # logical pixels
        "max_width": "0",           # 0 = full screen width
        "float_width": "760",       # width of the floating panel
        "pen_width": "2.6",
    },
    "recognition": {
        "model": "small",           # small | medium | /path/to/model.onnx
        "languages": "auto",        # hunspell dictionaries, e.g. "en_US, de_DE"
        "scripts": "western",       # western | latin | all
        "delay_ms": "800",          # pause after the last stroke before recognising
        "auto_insert": "true",      # false: tap the suggestion to insert it
        "auto_space": "true",
        "replace": "auto",          # auto | keys - how a tapped suggestion replaces the text
        "dictionary_weight": "1.5",
        "threads": "2",
    },
}


class Config:
    def __init__(self, path: str = CONFIG_FILE):
        cp = configparser.ConfigParser(interpolation=None)
        cp.read_dict(DEFAULTS)
        try:
            cp.read(path, encoding="utf-8")
        except (configparser.Error, OSError):
            pass
        g = cp.get

        def num(section, key, cast, lo, hi):
            try:
                v = cast(g(section, key))
            except ValueError:
                v = cast(DEFAULTS[section][key])
            return max(lo, min(hi, v))

        def flag(section, key):
            return g(section, key).strip().lower() in ("1", "true", "yes", "on")

        self.height = num("panel", "height", int, 160, 900)
        self.max_width = num("panel", "max_width", int, 0, 10000)
        self.float_width = num("panel", "float_width", int, 420, 10000)
        self.pen_width = num("panel", "pen_width", float, 1.0, 8.0)
        self.model = g("recognition", "model").strip()
        self.languages = g("recognition", "languages").strip()
        self.scripts = g("recognition", "scripts").strip().lower()
        self.delay_ms = num("recognition", "delay_ms", int, 150, 5000)
        self.auto_insert = flag("recognition", "auto_insert")
        self.auto_space = flag("recognition", "auto_space")
        self.replace = "keys" if g("recognition", "replace").strip().lower() == "keys" else "auto"
        self.dictionary_weight = num("recognition", "dictionary_weight", float, 0.0, 10.0)
        self.threads = num("recognition", "threads", int, 1, 16)

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

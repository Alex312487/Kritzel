"""Which language is being written right now.

The recogniser reads every language at once.  Choosing a language narrows
it down in two ways: only the spell-check dictionary of that language judges
(and corrects) the readings, and of the letters beyond a-z only those of that
language can be recognised.  The user names the languages they write in
(settings); the language key on the panel picks one of them.
"""

from __future__ import annotations

from .config import load_state, save_state
from .keys import parse_options
from .lexicon import base_language as base, find_dictionary, installed_dictionaries

ALL = None      # value of LanguageChoice.current for "all my languages together"

# language -> (its own name, the lower-case letters it uses beyond a-z)
KNOWN = {
    "en": ("English", ""),
    "de": ("Deutsch", "äöüß"),
    "fr": ("Français", "àâæçéèêëîïôœùûüÿ"),
    "es": ("Español", "áéíóúüñ"),
    "it": ("Italiano", "àèéìíîòóùú"),
    "pt": ("Português", "áâãàçéêíóôõúü"),
    "nl": ("Nederlands", "áéèëïóöúü"),
    "sv": ("Svenska", "åäöé"),
    "da": ("Dansk", "æøåé"),
    "nb": ("Norsk bokmål", "æøåéòóô"),
    "nn": ("Norsk nynorsk", "æøåéòóô"),
    "fi": ("Suomi", "äöåšž"),
    "pl": ("Polski", "ąćęłńóśźż"),
    "cs": ("Čeština", "áčďéěíňóřšťúůýž"),
    "sk": ("Slovenčina", "áäčďéíĺľňóôŕšťúýž"),
    "hu": ("Magyar", "áéíóöőúüű"),
    "ro": ("Română", "ăâîșț"),
    "tr": ("Türkçe", "çğıöşüâîû"),
    "hr": ("Hrvatski", "čćđšž"),
    "sl": ("Slovenščina", "čšž"),
    "ca": ("Català", "àçéèíïóòúü"),
}


def name(code: str) -> str:
    """"de_DE" -> "Deutsch (DE)", "xx_YY" -> "xx_YY"."""
    parts = code.replace("-", "_").split("_")
    known = KNOWN.get(parts[0].lower())
    if known is None:
        return code
    return f"{known[0]} ({parts[1].upper()})" if len(parts) > 1 else known[0]


def short_names(languages) -> dict[str, str]:
    """Key labels: {"de_DE": "DE", "en_US": "EN"}; languages that differ
    only by country get the country instead ({"en_US": "US", "en_GB": "GB"})."""
    parts = {code: code.replace("-", "_").split("_") for code in languages}
    counts: dict[str, int] = {}
    for p in parts.values():
        counts[p[0]] = counts.get(p[0], 0) + 1
    return {code: (p[1] if counts[p[0]] > 1 and len(p) > 1 else p[0]).upper() for code, p in parts.items()}


def letters_of(languages):
    """The letters beyond a-z that these languages use; None if that is not
    known for one of them (then nothing can be ruled out)."""
    letters = set()
    for code in languages:
        known = KNOWN.get(base(code))
        if known is None:
            return None
        letters.update(known[1])
    return "".join(sorted(letters))


def available() -> list[str]:
    """Everything that can be ticked in the settings: the installed
    dictionaries, then the known languages without one."""
    codes = installed_dictionaries()
    covered = {base(code) for code in codes}
    return codes + [code for code in KNOWN if code not in covered]


def usable(code: str) -> bool:
    """Choosing this language changes something."""
    return find_dictionary(code) is not None or base(code) in KNOWN


class LanguageChoice:
    def __init__(self, cfg):
        self.codes: list[str] = []      # the user's languages
        self.current = ALL              # one of them, or ALL
        self.offers_all = False         # "all together" can be chosen as well
        remembered = load_state().get("language", "first")
        # the state file is only as trustworthy as any file: null means ALL, anything but a string nothing
        self.update(cfg, remembered if remembered is ALL or isinstance(remembered, str) else "first")

    def update(self, cfg, preferred="keep"):
        """The settings changed.  The language in use stays if it still can."""
        if preferred == "keep":
            preferred = self.current
        seen, self.codes = set(), []
        for code in cfg.dictionary_languages():
            key = find_dictionary(code) or base(code)
            if usable(code) and key not in seen:
                seen.add(key)
                self.codes.append(code)
        has_key = "language" in parse_options(cfg.keys)
        self.offers_all = cfg.language_all or not has_key
        if not has_key or len(self.codes) < 2:
            self.current = ALL              # nothing to choose (or no key to choose with)
        elif preferred in self.codes or (preferred is ALL and self.offers_all):
            self.current = preferred
        else:
            self.current = self.codes[0]

    def choices(self) -> list:
        """What the language key offers."""
        if len(self.codes) < 2:
            return list(self.codes)
        return self.codes + ([ALL] if self.offers_all else [])

    def active(self) -> list[str]:
        """The languages in use."""
        return self.codes if self.current is ALL else [self.current]

    def select(self, choice):
        """Uses `choice` from now on and remembers it across restarts."""
        if choice in self.choices():
            self.current = choice
            save_state({**load_state(), "language": choice})

    def can_switch(self) -> bool:
        return len(self.choices()) > 1

    def next(self):
        choices = self.choices()
        if len(choices) > 1:
            self.select(choices[(choices.index(self.current) + 1) % len(choices)] if self.current in choices
                        else choices[0])

    def title(self, choice) -> str:
        return "All together" if choice is ALL else name(choice)

    @property
    def label(self) -> str:
        """What the language key shows."""
        if not self.codes:
            return "–"
        names = short_names(self.codes)
        if self.current is not ALL:
            return names[self.current]
        return "+".join(names.values()) if len(names) <= 2 else "all"

"""Settings and personal words as one file, to carry them to another computer.

The file is JSON.  Reading one is reading something from elsewhere, so
nothing in it is taken on trust:

    * only known settings are taken, each through the same parser as the
      settings file (limits, known choices), and never the ones that
      describe a computer (config.MACHINE_SETTINGS: `model` names a file the
      program would load);
    * values and words are single lines of limited length;
    * the file itself has a size limit.

load() returns values for the settings window; nothing is saved until the
user applies them there.
"""

from __future__ import annotations

import json
import os

from .config import MACHINE_SETTINGS, OPTION, OPTIONS, Config, is_language_code, one_line, write_atomically

FORMAT = "kritzel-settings"
VERSION = 1
SUFFIX = ".kritzel.json"
MAX_FILE_BYTES = 1_000_000
MAX_VALUE_LENGTH = 2000
MAX_WORDS = 5000
MAX_WORD_LENGTH = 64


class NotASettingsFile(ValueError):
    """The message is fit to be shown to the user."""


def dump(values: dict[str, str], words) -> str:
    """values: {key: text as in the settings file}."""
    settings = {o.key: values[o.key] for o in OPTIONS if o.key in values and o.key not in MACHINE_SETTINGS}
    return json.dumps({"format": FORMAT, "version": VERSION, "settings": settings, "words": list(words)},
                      ensure_ascii=False, indent=1) + "\n"


def save(path: str, values: dict[str, str], words):
    """The file holds the user's own words: like everything Kritzel writes
    it is for the user alone."""
    write_atomically(path, dump(values, words))


def parse(text: str) -> tuple[dict[str, str], list[str]]:
    """-> ({key: text as in the settings file}, personal words)."""
    try:
        data = json.loads(text)
    except (ValueError, RecursionError):
        raise NotASettingsFile("This is not a Kritzel settings file.") from None
    if not isinstance(data, dict) or data.get("format") != FORMAT:
        raise NotASettingsFile("This is not a Kritzel settings file.")
    if not isinstance(data.get("version"), int) or data["version"] > VERSION:
        raise NotASettingsFile("This file was written by a newer version of Kritzel.")
    settings, words = data.get("settings", {}), data.get("words", [])
    if not isinstance(settings, dict) or not isinstance(words, list):
        raise NotASettingsFile("This is not a Kritzel settings file.")
    return _checked_settings(settings), _checked_words(words)


def load(path: str) -> tuple[dict[str, str], list[str]]:
    try:
        if os.path.getsize(path) > MAX_FILE_BYTES:
            raise NotASettingsFile("The file is too large to be a Kritzel settings file.")
        with open(path, encoding="utf-8") as f:
            return parse(f.read(MAX_FILE_BYTES + 1))
    except (OSError, UnicodeDecodeError) as e:
        raise NotASettingsFile(f"The file could not be read: {e}") from None


def _checked_settings(settings: dict) -> dict[str, str]:
    values = {}
    for key, text in settings.items():
        if key not in OPTION or key in MACHINE_SETTINGS or not isinstance(text, str):
            continue
        values[key] = one_line(text)[:MAX_VALUE_LENGTH]
    # through the parser of the settings file: numbers within their limits, known choices only
    cfg = Config.from_values(values)
    values = {key: cfg.text(key) for key in values}
    if "languages" in values and values["languages"].lower() not in ("auto", "none", "off", ""):
        codes = (code.strip() for code in values["languages"].replace(";", ",").split(","))
        values["languages"] = ", ".join(code for code in codes if is_language_code(code)) or "none"
    return values


def _checked_words(words: list) -> list[str]:
    result, seen = [], set()
    for word in words:
        if not isinstance(word, str):
            continue
        word = one_line(word)
        if word and len(word) <= MAX_WORD_LENGTH and not word.startswith("#") and word not in seen:
            seen.add(word)
            result.append(word)
        if len(result) >= MAX_WORDS:
            break
    return result

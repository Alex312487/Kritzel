"""The settings window (`kritzel settings`, or the settings button on the panel).

An ordinary Qt Widgets program, separate from the panel process.  It edits
config.ini and the personal word list; Apply writes both and tells the
running panel to read them again.

A page is a Page subclass that builds its widgets in __init__.  Most
settings need one line there: the helpers check(), number(), choice() and
text() create the widget and tie it to the setting of that name in
config.OPTIONS, which is all that loading and saving need.  A setting with
an unusual widget calls bind() with its own read and write functions.
"""

from __future__ import annotations

import os
import sys

from PyQt6.QtCore import QLocale, Qt, QTimer
from PyQt6.QtGui import QColor, QIcon, QPainter
from PyQt6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QColorDialog, QComboBox, QDialog, QDialogButtonBox, QDoubleSpinBox,
    QFileDialog, QFormLayout, QFrame, QGridLayout, QGroupBox, QHBoxLayout, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QPushButton, QScrollArea, QSlider, QSpinBox, QStackedWidget, QVBoxLayout, QWidget,
)

from . import control, keys, languages, switch, transfer
from .config import (APP_DIR, CONFIG_FILE, DEFAULT_SYMBOL_KEYS, DEFAULT_SYMBOLS, OPTION, STATE_DIR, WORDS_FILE,
                     Config, Theme, load_words, save_config, save_words)
from .learning import LEARNED_AFTER, LEARNED_FILE, LearnedWords
from .lexicon import find_dictionary
from .ui import View


def plain_label(text: str = "") -> QLabel:
    """A label that shows its text as it is.  Left to itself QLabel guesses
    whether a text is HTML, and paths, error messages and the like come
    from outside the program."""
    label = QLabel()
    label.setTextFormat(Qt.TextFormat.PlainText)
    label.setText(text)
    return label


def note(text: str) -> QLabel:
    """Explanatory text under a setting."""
    label = plain_label(text)
    label.setWordWrap(True)
    font = label.font()
    font.setPointSizeF(font.pointSizeF() * 0.92)
    label.setFont(font)
    return label


def language_name(code: str) -> str:
    """"de_DE" -> "Deutsch (Deutschland) – de_DE"; languages without an
    installed dictionary say so."""
    locale = QLocale(code)
    name = locale.nativeLanguageName() if locale.language() != QLocale.Language.C else ""
    if name:
        name = name[0].upper() + name[1:]
        territory = locale.nativeTerritoryName() if "_" in code else ""
        name = f"{name} ({territory}) – {code}" if territory else f"{name} – {code}"
    else:
        name = code
    if find_dictionary(code) is None:
        name += "   (letters only; no dictionary installed)"
    return name


class Page(QWidget):
    """One page of the window.  `bindings` maps a setting's key to the
    functions that read its value from the page's widget and write it there."""

    title = ""
    icon = ""

    def __init__(self):
        super().__init__()
        self.bindings: dict[str, tuple] = {}
        self.column = QVBoxLayout(self)
        self.column.setContentsMargins(0, 0, 0, 0)
        self.column.setSpacing(12)

    def bind(self, key: str, read, write):
        assert key in OPTION, key
        self.bindings[key] = (read, write)

    def load(self, cfg: Config):
        for key, (_read, write) in self.bindings.items():
            write(getattr(cfg, key))

    def values(self) -> dict[str, str]:
        """{key: text for the settings file}"""
        return {key: OPTION[key].format(read()) for key, (read, _write) in self.bindings.items()}

    # -- building blocks ---------------------------------------------------

    def group(self, title: str) -> QFormLayout:
        box = QGroupBox(title)
        form = QFormLayout(box)
        self.column.addWidget(box)
        return form

    def check(self, key: str, label: str) -> QCheckBox:
        box = QCheckBox(label)
        self.bind(key, box.isChecked, box.setChecked)
        return box

    def number(self, key: str, suffix: str = "", step=None, zero_means: str = ""):
        """A spin box with the limits of the setting.  zero_means: what the
        box shows for 0 if that value has a special meaning."""
        option = OPTION[key]
        box = QDoubleSpinBox() if option.kind == "float" else QSpinBox()
        if option.kind == "float":
            box.setDecimals(1)
        box.setRange(option.lo, option.hi)
        if step:
            box.setSingleStep(step)
        box.setSuffix(suffix)
        if zero_means:
            box.setSpecialValueText(zero_means)
        self.bind(key, box.value, box.setValue)
        return box

    def choice(self, key: str, labels: dict[str, str]) -> QComboBox:
        """labels: {value in the file: text in the list}"""
        assert set(labels) == set(OPTION[key].choices), key
        box = QComboBox()
        for value, label in labels.items():
            box.addItem(label, value)
        self.bind(key, box.currentData, lambda value: box.setCurrentIndex(max(0, box.findData(value))))
        return box

    def text(self, key: str, placeholder: str = "") -> QLineEdit:
        box = QLineEdit()
        box.setPlaceholderText(placeholder)
        box.setClearButtonEnabled(True)
        self.bind(key, lambda: " ".join(box.text().split()) or OPTION[key].default, box.setText)
        return box


# ---------------------------------------------------------------------------
# Pages
# ---------------------------------------------------------------------------

class PanelPage(Page):
    title = "Panel"
    icon = "input-tablet"

    def __init__(self):
        super().__init__()
        form = self.group("When the panel appears")
        form.addRow("Show it:", self.choice("show", {
            "touch": "When a text field is tapped with a finger or the pen",
            "always": "Whenever a text field gets the focus, also by mouse or keyboard"}))
        form.addRow(note("`kritzel show` brings the panel up at any time; you can bind it to a keyboard shortcut."))
        form.addRow(self.check("remember_floating", "After a restart, float again where the panel was left"))

        form = self.group("Size")
        form.addRow("Height:", self.number("height", " px", 10))
        form.addRow("Width when docked:", self.number("max_width", " px", 20, zero_means="Full width of the screen"))
        form.addRow("Width when floating:", self.number("float_width", " px", 20))
        form.addRow(note("The panel never takes more than 45 % of the screen's height."))

        form = self.group("Suggestions")
        form.addRow("Readings offered:", self.number("suggestions"))
        form.addRow(self.check("hide_passwords", "In password fields, show dots instead of what was read"))
        form.addRow(note("Keeps a password from being read off the panel by somebody next to you."))
        self.column.addStretch(1)


class PanelPreview(QWidget):
    """A picture of the panel as the settings in the window would make it."""

    def __init__(self, current_values):
        super().__init__()
        self.current_values = current_values    # function returning {key: text}
        self.shown = None
        self.setMinimumHeight(150)
        self.theme = Theme()
        timer = QTimer(self)
        timer.timeout.connect(self._refresh)
        timer.start(300)

    def _refresh(self):
        if self.isVisible() and self.current_values() != self.shown:
            self.update()

    def paintEvent(self, _event):
        self.shown = self.current_values()
        cfg = Config.from_values(self.shown)
        view = View(self.theme, cfg)
        # the panel as on a screen 1100 wide, scaled to fit
        width, height = 1100.0, float(cfg.height)
        view.layout(width, height)
        scale = min(self.width() / width, self.height() / height)
        p = QPainter(self)
        p.translate((self.width() - width * scale) / 2, 0)
        p.scale(scale, scale)
        view.paint(p, self)
        p.end()

    # what ui.View.paint wants to know about the panel's state
    strokes, candidates, selected, status, pressed = [], ["Hello", "Hallo"], 0, "", None
    hint, preview, language_label, pending, secret = "Write here", None, "EN", "", False


class KeysPage(Page):
    title = "Keys"
    icon = "input-keyboard"

    def __init__(self, current_values):
        super().__init__()
        self.column.addWidget(PanelPreview(current_values))

        box = QGroupBox("Keys on the panel")
        grid = QGridLayout(box)
        self.options: dict[str, QCheckBox] = {}
        for i, (option, (title, explanation)) in enumerate(keys.OPTIONS.items()):
            check = QCheckBox(title)
            check.setToolTip(explanation)
            self.options[option] = check
            grid.addWidget(check, i // 3, i % 3)
        grid.addWidget(note("Backspace and Settings are always there. The keys arrange themselves so that the "
                            "writing area stays as wide as possible; the picture above shows the result."),
                       grid.rowCount(), 0, 1, 3)
        self.column.addWidget(box)
        self.bind("keys", lambda: " ".join(o for o, check in self.options.items() if check.isChecked()),
                  self._show_keys)

        form = self.group("Arrangement")
        form.addRow("Keys are on the:", self.choice("keys_side", {"right": "Right", "left": "Left (for left-handers)"}))
        form.addRow("Key size:", self.choice("key_size", {"small": "Small", "normal": "Normal", "large": "Large"}))
        form.addRow(self.check("key_repeat", "Backspace, Delete and the arrow keys repeat while held"))

        form = self.group("Symbols")
        form.addRow(note("The symbol keyboard takes the place of the writing area while its key is switched on; "
                         "the panel keeps its size."))
        form.addRow("Symbol keyboard:", self._symbols_field("symbol_keys", DEFAULT_SYMBOL_KEYS)[0])
        form.addRow(self.check("symbols_stay", "Stay on the symbol keyboard after a symbol"))
        symbol_row = self.check("symbol_row", "Also show a thin row of symbol keys above the writing area")
        form.addRow(symbol_row)
        row, widgets = self._symbols_field("symbols", DEFAULT_SYMBOLS)
        form.addRow("Symbol row:", row)
        for widget in widgets:
            symbol_row.toggled.connect(widget.setEnabled)
            widget.setEnabled(False)
        form.addRow(note("Separate the keys with spaces. A key may type several characters, for example \"...\" "
                         "or \"@gmail.com\". The row shows as many keys as fit, starting from the left."))
        self.column.addStretch(1)

    def _show_keys(self, text: str):
        on = keys.parse_options(text)
        for option, check in self.options.items():
            check.setChecked(option in on)

    def _symbols_field(self, key: str, standard: str):
        field = self.text(key)
        reset = QPushButton("Standard")
        reset.clicked.connect(lambda: field.setText(standard))
        row = QHBoxLayout()
        row.addWidget(field, 1)
        row.addWidget(reset)
        return row, (field, reset)


class WritingPage(Page):
    title = "Pen and ink"
    icon = "draw-freehand"

    def __init__(self):
        super().__init__()
        form = self.group("Ink")
        form.addRow("Thickness:", self.number("pen_width", " px", 0.2))
        self.theme_color = QCheckBox("Use the text colour of the colour scheme")
        self.pick_color = QPushButton()
        self.pick_color.clicked.connect(self._pick_color)
        self.theme_color.toggled.connect(lambda on: self.pick_color.setEnabled(not on))
        self.color = "#1d63c4"
        form.addRow("Colour:", self.theme_color)
        form.addRow("", self.pick_color)
        self.bind("ink_color", lambda: "" if self.theme_color.isChecked() else self.color, self._show_color)
        form.addRow(self.check("guide_line", "Show a guide line to write on"))

        form = self.group("Pen and fingers")
        form.addRow(self.check("finger_writes", "Fingers can write too"))
        form.addRow(note("Switch this off if you only write with the pen: fingers then just press buttons, "
                         "pick suggestions and move the floating panel."))
        form.addRow(self.check("palm_rejection", "Ignore fingers in the writing area while the pen is near"))
        form.addRow(note("Keeps the hand that rests on the screen from drawing."))

        form = self.group("Gestures")
        form.addRow(self.check("gestures", "Some strokes are commands"))
        form.addRow(note(
            "Over writing that is not typed yet:\n"
            "    •  a zigzag across it rubs it out\n"
            "    •  a line from the top of the writing area to its bottom opens a gap between two words "
            "that ran together\n"
            "In the empty writing area:\n"
            "    •  a long line to the left deletes the last word (like the word key)\n"
            "    •  a long line to the right types a space\n"
            "    •  down, then a long line to the left, is Enter"))
        self.column.addStretch(1)

    def _show_color(self, value: str):
        self.theme_color.setChecked(not value)
        self.pick_color.setEnabled(bool(value))
        if value:
            self.color = value
        self.pick_color.setText(f"Choose…   ({self.color})")
        self.pick_color.setStyleSheet(f"border-left: 28px solid {self.color}; padding: 4px 10px;")

    def _pick_color(self):
        color = QColorDialog.getColor(QColor(self.color), self, "Ink colour")
        if color.isValid():
            self._show_color(color.name())


class RecognitionPage(Page):
    title = "Recognition"
    icon = "edit-find"

    def __init__(self):
        super().__init__()
        form = self.group("Reading")
        form.addRow("Pause before recognising:", self.number("delay_ms", " ms", 50))
        form.addRow(note("How long the panel waits after your last stroke. Raise it if words get split "
                         "because you write slowly; lower it if the text takes too long to appear."))
        form.addRow(self.check("delay_auto", "Fit the pause to my writing"))
        form.addRow(note("The panel measures the breaks you make while writing and waits longer if you write "
                         "slowly, shorter if you write fast. The pause above is where it starts from."))
        form.addRow(self.check("preview", "Show what was read while the pause still runs"))
        form.addRow(note("The reading appears in grey at the right of the suggestions as soon as you lift the "
                         "pen, and is typed when the pause is over."))
        form.addRow("Characters:", self.choice("scripts", {
            "western": "Western European (English, German, French, Spanish, Nordic …)",
            "latin": "All Latin alphabets (adds Polish, Czech, Turkish, Hungarian …)",
            "all": "Everything the model knows (adds Chinese and Japanese)"}))
        form.addRow(note("Fewer possible characters mean fewer mix-ups. The letters of the language you "
                         "chose are always included."))
        form.addRow(self.check("number_fields", "In number fields, recognise only digits"))
        form.addRow(note("Fields for numbers, phone numbers, dates and times, where the application says so. "
                         "An \"l\" or an \"O\" cannot be read there."))
        form.addRow(self.check("context", "Let the text before the cursor help"))
        form.addRow(note("Letters written right after a word are read as the rest of that word, and in the "
                         "middle of a sentence \"some\" is preferred to \"Some\"."))

        form = self.group("Typing")
        form.addRow(self.check("auto_insert", "Type the recognised text right away"))
        form.addRow(note("Tap another suggestion to swap the text. When this is off, nothing is typed "
                         "until you tap one of the suggestions."))
        form.addRow("Automatic spaces:", self.choice("auto_space", {
            "after": "After each word",
            "before": "Before each word, where one is missing",
            "off": "Off"}))
        form.addRow(note("\"After\" lets you continue or repair a word letter by letter: delete the space and "
                         "the wrong letter, write the right one. A comma or full stop written next closes up to "
                         "the word. No spaces are added in address, number and password fields."))
        form.addRow(self.check("capitalize", "Start sentences with a capital letter"))
        self.column.addStretch(1)


class LanguagesPage(Page):
    title = "Languages"
    icon = "preferences-desktop-locale"

    def __init__(self):
        super().__init__()
        self.column.addWidget(note(
            "Tick the languages you write in; the language key on the panel then lets you pick the one you are "
            "writing right now. Its spell-check dictionary judges and corrects what was read, and only its "
            "letters are recognised (no \"ö\" in English, no \"é\" in German)."))

        box = QGroupBox("Languages you write in")
        v = QVBoxLayout(box)
        self.auto = QCheckBox("Automatic: the system language and English")
        v.addWidget(self.auto)
        self.list = QListWidget()
        self.list.setAlternatingRowColors(True)
        v.addWidget(self.list, 1)
        self.list.setMinimumHeight(170)
        v.addWidget(note("Dictionaries come with the hunspell packages, for example German:  "
                         "sudo dnf install hunspell-de   – then open this window again."))
        self.auto.toggled.connect(lambda on: self.list.setEnabled(not on))
        self.column.addWidget(box, 1)

        form = self.group("Language key")
        form.addRow("A tap on it:", self.choice("language_button", {
            "list": "Shows my languages to pick from",
            "next": "Switches to the next language"}))
        form.addRow(self.check("language_all", "Offer \"all my languages together\" as well"))
        form.addRow(self.check("language_letters", "Recognise only the letters of the language in use"))
        form.addRow(note("Without the language key (see Keys), all ticked languages are used together."))
        self.bind("languages", self._read_languages, lambda _value: None)   # written in load(): needs the whole Config

        form = self.group("Dictionary")
        self.weight = QSlider(Qt.Orientation.Horizontal)
        self.weight.setRange(0, 40)             # tenths
        self.weight.setPageStep(5)
        self.weight_label = plain_label()
        self.weight_label.setMinimumWidth(40)
        self.weight.valueChanged.connect(
            lambda tenths: self.weight_label.setText(f"{tenths / 10:.1f}" if tenths else "off"))
        row = QHBoxLayout()
        row.addWidget(QLabel("off"))
        row.addWidget(self.weight, 1)
        row.addWidget(QLabel("strong"))
        row.addWidget(self.weight_label)
        form.addRow("Influence:", row)
        self.bind("dictionary_weight", lambda: self.weight.value() / 10,
                  lambda value: self.weight.setValue(round(min(4.0, value) * 10)))
        form.addRow(note("How much a reading made of dictionary words is preferred. Strong corrects more "
                         "misreadings but makes it harder to write words that are in no dictionary; add "
                         "those to your personal words."))
        form.addRow("Words not in the dictionary:", self.choice("corrections", {
            "off": "Leave as read",
            "suggest": "Suggest similar dictionary words",
            "auto": "Replace by a similar dictionary word"}))

    def load(self, cfg: Config):
        super().load(cfg)
        chosen = [c.replace("-", "_") for c in cfg.dictionary_languages()]
        codes = languages.available()
        for code in chosen:                     # keep what the file names even if nothing is known about it
            if code not in codes:
                codes.append(code)
        self.list.clear()
        for code in codes:
            item = QListWidgetItem(language_name(code))
            item.setData(Qt.ItemDataRole.UserRole, code)
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if code in chosen else Qt.CheckState.Unchecked)
            self.list.addItem(item)
        self.auto.setChecked(cfg.languages.lower() == "auto")
        self.list.setEnabled(not self.auto.isChecked())

    def _read_languages(self) -> str:
        if self.auto.isChecked():
            return "auto"
        items = [self.list.item(i) for i in range(self.list.count())]
        checked = (i.data(Qt.ItemDataRole.UserRole) for i in items if i.checkState() == Qt.CheckState.Checked)
        return ", ".join(checked) or "none"


class PersonalWordsPage(Page):
    """Edits words.txt rather than settings, so it has words() and
    load_words() instead of bindings."""

    title = "Personal words"
    icon = "tools-check-spelling"

    def __init__(self):
        super().__init__()
        self.column.addWidget(note(
            "Names, places, technical terms: words that are in no dictionary. Kritzel prefers them when the "
            "writing could be read that way, and offers them when it reads something that is one letter off. "
            "Write them the way you want them typed, including capital letters."))
        row = QHBoxLayout()
        self.entry = QLineEdit()
        self.entry.setPlaceholderText("New word")
        self.entry.setClearButtonEnabled(True)
        self.add_button = QPushButton(QIcon.fromTheme("list-add"), "Add")
        row.addWidget(self.entry, 1)
        row.addWidget(self.add_button)
        self.column.addLayout(row)
        self.list = QListWidget()
        self.list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list.setAlternatingRowColors(True)
        self.list.setSortingEnabled(True)
        self.column.addWidget(self.list, 1)
        row = QHBoxLayout()
        self.count = note("")
        self.remove_button = QPushButton(QIcon.fromTheme("list-remove"), "Remove selected")
        row.addWidget(self.count, 1)
        row.addWidget(self.remove_button)
        self.column.addLayout(row)

        self.entry.returnPressed.connect(self.add)
        self.entry.textChanged.connect(lambda text: self.add_button.setEnabled(bool(text.strip())))
        self.add_button.clicked.connect(self.add)
        self.add_button.setEnabled(False)
        self.remove_button.clicked.connect(self.remove_selected)
        self.list.itemSelectionChanged.connect(self._update)
        self._build_learned_words()

    # -- words learnt from the user's picks (learning.py) -----------------------

    def _build_learned_words(self):
        box = QGroupBox("Learnt words")
        v = QVBoxLayout(box)
        v.addWidget(self.check("learn", "Learn the words I pick from the suggestions"))
        v.addWidget(note(
            f"A word you pick {LEARNED_AFTER} times in place of what Kritzel had typed is preferred from then on, "
            "like a personal word. Nothing is learnt in password, address and number fields."))
        self.learned_list = QListWidget()
        self.learned_list.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.learned_list.setAlternatingRowColors(True)
        self.learned_list.setSortingEnabled(True)
        self.learned_list.setMaximumHeight(110)
        v.addWidget(self.learned_list)
        row = QHBoxLayout()
        self.learned_count = note("")
        self.keep_button = QPushButton(QIcon.fromTheme("list-add"), "Make personal words")
        self.keep_button.setToolTip("Moves the selected words to your personal words above.")
        self.forget_button = QPushButton(QIcon.fromTheme("list-remove"), "Forget selected")
        self.forget_all_button = QPushButton("Forget all")
        for widget, stretch in ((self.learned_count, 1), (self.keep_button, 0), (self.forget_button, 0),
                                (self.forget_all_button, 0)):
            row.addWidget(widget, stretch)
        v.addLayout(row)
        self.column.addWidget(box)
        self.forgotten: set[str] = set()        # removed here; removed from the file by save_learned()
        self.keep_button.clicked.connect(self._keep_learned)
        self.forget_button.clicked.connect(lambda: self._forget_learned(self.learned_list.selectedItems()))
        self.forget_all_button.clicked.connect(lambda: self._forget_learned(self._learned_items()))
        self.learned_list.itemSelectionChanged.connect(self._update_learned)
        self.load_learned()

    def _learned_items(self):
        return [self.learned_list.item(i) for i in range(self.learned_list.count())]

    def load_learned(self):
        self.forgotten.clear()
        self.learned_list.clear()
        self.learned_list.addItems(LearnedWords().words())
        self._update_learned()

    def _update_learned(self):
        n = self.learned_list.count()
        self.learned_count.setText("Nothing learnt yet" if n == 0 else f"{n} word{'s' if n != 1 else ''}")
        selected = bool(self.learned_list.selectedItems())
        self.keep_button.setEnabled(selected)
        self.forget_button.setEnabled(selected)
        self.forget_all_button.setEnabled(n > 0)

    def _forget_learned(self, items):
        for item in items:
            self.forgotten.add(item.text())
            self.learned_list.takeItem(self.learned_list.row(item))
        self._update_learned()

    def _keep_learned(self):
        items = self.learned_list.selectedItems()
        existing = set(self._listed())
        self.list.addItems([item.text() for item in items if item.text() not in existing])
        self._forget_learned(items)
        self._update()

    def save_learned(self):
        """Takes the words removed in the window out of the file.  The file
        is read afresh: the panel may have learnt more in the meantime."""
        if self.forgotten:
            LearnedWords().forget(self.forgotten)
            self.forgotten.clear()

    def _update(self):
        n = self.list.count()
        self.count.setText("No words yet" if n == 0 else f"{n} word{'s' if n != 1 else ''}")
        self.remove_button.setEnabled(bool(self.list.selectedItems()))

    def add(self):
        """Adds what is in the entry field; several words may be entered at once."""
        existing = set(self._listed())
        for word in self.entry.text().replace(",", " ").split():
            if word not in existing:
                existing.add(word)
                self.list.addItem(word)
        self.entry.clear()
        self._update()

    def remove_selected(self):
        for item in self.list.selectedItems():
            self.list.takeItem(self.list.row(item))
        self._update()

    def load_words(self, words):
        self.list.clear()
        self.list.addItems(words)
        self._update()

    def _listed(self) -> list[str]:
        return [self.list.item(i).text() for i in range(self.list.count())]

    def words(self) -> list[str]:
        self.add()                              # a word still in the entry field counts
        return self._listed()


class AdvancedPage(Page):
    title = "Advanced"
    icon = "preferences-other"

    def __init__(self, export_settings, import_settings):
        """export_settings, import_settings: what the two buttons do (the
        window knows all pages)."""
        super().__init__()
        form = self.group("On-screen keyboard")
        self.keyboard_state = plain_label()
        self.keyboard_state.setWordWrap(True)
        form.addRow(self.keyboard_state)
        self.use_kritzel = QPushButton("Use handwriting")
        self.use_other = QPushButton("Use the normal keyboard")
        self.use_kritzel.clicked.connect(lambda: self._switch(switch.use_kritzel))
        self.use_other.clicked.connect(lambda: self._switch(switch.use_other))
        row = QHBoxLayout()
        row.addWidget(self.use_kritzel)
        row.addWidget(self.use_other)
        row.addStretch(1)
        form.addRow(row)

        form = self.group("Compatibility")
        form.addRow("Replacing a word:", self.choice("replace", {
            "auto": "Automatic", "keys": "Always with Backspace keys"}))
        form.addRow(note("How a tapped suggestion replaces the word that was typed. Try the second choice "
                         "if replacing misbehaves in some application."))

        form = self.group("Recognition model")
        self.model = QComboBox()
        self.model.addItem("Standard (included)", "small")
        self.model.addItem("Other file", "file")
        form.addRow("Model:", self.model)
        self.model_file = QLineEdit()
        self.model_file.setPlaceholderText("/path/to/model.onnx")
        self.browse = QPushButton("Choose…")
        self.browse.clicked.connect(self._browse)
        row = QHBoxLayout()
        row.addWidget(self.model_file, 1)
        row.addWidget(self.browse)
        form.addRow("File:", row)
        form.addRow(note("Another PP-OCR text-line recognition model in ONNX format; see the README."))
        self.model.currentIndexChanged.connect(self._model_changed)
        self.bind("model", self._read_model, self._show_model)
        form.addRow("Processor threads:", self.number("threads"))

        form = self.group("Another computer")
        export_button = QPushButton(QIcon.fromTheme("document-export"), "Export…")
        import_button = QPushButton(QIcon.fromTheme("document-import"), "Import…")
        export_button.clicked.connect(export_settings)
        import_button.clicked.connect(import_settings)
        row = QHBoxLayout()
        row.addWidget(export_button)
        row.addWidget(import_button)
        row.addStretch(1)
        form.addRow(row)
        form.addRow(note("Export writes the settings as they are shown in this window, and your personal words, "
                         "into one file. Import shows the settings of such a file here; they are used once you "
                         "press Apply or OK. The recognition model and the processor threads belong to a "
                         "computer and are not carried over."))

        form = self.group("Files")
        for label, path in (("Settings:", CONFIG_FILE), ("Personal words:", WORDS_FILE),
                            ("Learnt words:", LEARNED_FILE), ("Log:", os.path.join(STATE_DIR, "kritzel.log"))):
            value = plain_label(path)
            value.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            form.addRow(label, value)
        self.column.addStretch(1)
        self._show_keyboard_state()

    def _model_changed(self):
        other = self.model.currentData() == "file"
        self.model_file.setEnabled(other)
        self.browse.setEnabled(other)

    def _read_model(self) -> str:
        if self.model.currentData() == "file":
            return self.model_file.text().strip() or "small"
        return "small"

    def _show_model(self, value: str):
        standard = value == "small"
        self.model.setCurrentIndex(self.model.findData("small" if standard else "file"))
        self.model_file.setText("" if standard else value)
        self._model_changed()

    def _browse(self):
        path, _ = QFileDialog.getOpenFileName(self, "Recognition model", self.model_file.text() or APP_DIR,
                                              "ONNX models (*.onnx)")
        if path:
            self.model_file.setText(path)

    def _show_keyboard_state(self):
        try:
            current = switch.kread()
        except OSError:
            current = None
        ours = switch.is_ours(current)
        if current is None:
            self.keyboard_state.setText("This does not look like a Plasma session.")
        elif ours:
            self.keyboard_state.setText("Kritzel is Plasma's on-screen keyboard.")
        else:
            self.keyboard_state.setText("Kritzel is not Plasma's on-screen keyboard at the moment.")
        self.use_kritzel.setEnabled(current is not None and not ours)
        self.use_other.setEnabled(current is not None and ours)

    def _switch(self, action):
        try:
            _ok, message = action()
        except Exception as e:  # noqa: BLE001 - shown in the window
            message = str(e)
        self._show_keyboard_state()
        self.keyboard_state.setText(self.keyboard_state.text() + "\n" + message.splitlines()[0])


# ---------------------------------------------------------------------------
# Window
# ---------------------------------------------------------------------------

class SettingsWindow(QDialog):
    def __init__(self, page: str = ""):
        """page: title of the page to open with, e.g. "languages"."""
        super().__init__()
        self.setWindowTitle("Kritzel Settings")
        self.setWindowIcon(QIcon.fromTheme("draw-freehand"))
        self.resize(900, 680)

        self.words_page = PersonalWordsPage()
        self.pages = [PanelPage(), KeysPage(self.values), WritingPage(), RecognitionPage(), LanguagesPage(),
                      self.words_page, AdvancedPage(self.export_settings, self.import_settings)]
        self.sidebar = QListWidget()
        self.sidebar.setFixedWidth(190)
        self.sidebar.setIconSize(self.sidebar.iconSize() * 1.5)
        self.sidebar.setSpacing(2)
        self.stack = QStackedWidget()
        for p in self.pages:
            self.sidebar.addItem(QListWidgetItem(QIcon.fromTheme(p.icon), p.title))
            scroll = QScrollArea()              # pages may be taller than a small screen
            scroll.setWidgetResizable(True)
            scroll.setFrameShape(QFrame.Shape.NoFrame)
            scroll.setWidget(p)
            self.stack.addWidget(scroll)
        self.sidebar.currentRowChanged.connect(self.stack.setCurrentIndex)
        titles = [p.title.lower() for p in self.pages]
        self.sidebar.setCurrentRow(titles.index(page.lower()) if page.lower() in titles else 0)

        standard = QDialogButtonBox.StandardButton
        self.buttons = QDialogButtonBox(standard.Ok | standard.Apply | standard.Cancel | standard.RestoreDefaults)
        self.buttons.accepted.connect(self.accept)
        self.buttons.rejected.connect(self.reject)
        self.buttons.button(standard.Apply).clicked.connect(self.apply)
        self.buttons.button(standard.RestoreDefaults).clicked.connect(self.restore_defaults)
        self.message = note("")

        top = QHBoxLayout()
        top.addWidget(self.sidebar)
        top.addWidget(self.stack, 1)
        bottom = QHBoxLayout()
        bottom.addWidget(self.message, 1)
        bottom.addWidget(self.buttons)
        layout = QVBoxLayout(self)
        layout.addLayout(top, 1)
        layout.addLayout(bottom)

        self.load(Config())
        self.words_page.load_words(load_words())

    def load(self, cfg: Config):
        for p in self.pages:
            p.load(cfg)

    def values(self) -> dict[str, str]:
        merged: dict[str, str] = {}
        for p in self.pages:
            merged.update(p.values())
        return merged

    def restore_defaults(self):
        """Puts the standard settings into the window; nothing is saved
        until Apply or OK.  The personal words are left alone."""
        self.load(Config(os.devnull))
        self.message.setText("Standard settings shown – Apply or OK to use them.")

    def export_settings(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export settings", os.path.expanduser("~/kritzel" + transfer.SUFFIX),
                                              f"Kritzel settings (*{transfer.SUFFIX})")
        if not path:
            return
        try:
            transfer.save(path, self.values(), self.words_page.words())
        except OSError as e:
            self.message.setText(f"Could not export: {e}")
        else:
            self.message.setText("Settings and personal words exported.")

    def import_settings(self):
        """Shows the settings of a file in the window, like restore_defaults()
        shows the standard ones.  transfer.load() has checked every value."""
        path, _ = QFileDialog.getOpenFileName(self, "Import settings", os.path.expanduser("~"),
                                              f"Kritzel settings (*{transfer.SUFFIX});;All files (*)")
        if not path:
            return
        try:
            values, words = transfer.load(path)
        except transfer.NotASettingsFile as e:
            self.message.setText(str(e))
            return
        # what the file does not say (always: the settings of this computer) stays as it is
        self.load(Config.from_values({**self.values(), **values}))
        self.words_page.load_words(words)
        self.message.setText("Imported settings shown – Apply or OK to use them.")

    def apply(self) -> bool:
        try:
            save_config(self.values())
            save_words(self.words_page.words())
            self.words_page.save_learned()
        except OSError as e:
            self.message.setText(f"Could not save: {e}")
            return False
        running = control.reload_panel()
        self.message.setText("Saved and applied." if running else "Saved.")
        return True

    def accept(self):
        if self.apply():
            super().accept()


def run(page: str = "") -> int:
    QApplication.setDesktopFileName("kritzel-settings")
    app = QApplication(sys.argv[:1])
    app.setApplicationName("kritzel")
    app.setApplicationDisplayName("Kritzel")
    control.write_pid(control.SETTINGS)
    try:
        window = SettingsWindow(page)
        window.show()
        app.exec()
    finally:
        control.remove_pid(control.SETTINGS)
    return 0

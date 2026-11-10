"""Unit tests for the parts that need no compositor.  Run: python3 tests/test_units.py"""
import os, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from kritzel.config import Config, load_words, save_config, save_words
from kritzel.eventloop import Loop
import itertools
from kritzel import keys, languages, layout
from kritzel.compose import Context, compose, compose_symbol, needs_space
from kritzel.languages import LanguageChoice, short_names
from kritzel.lexicon import Lexicon, edit_distance_at_most
from kritzel.recognizer import ctc_beam_search
from kritzel.textfield import BACKSPACE, ENTER, PreviewField, TextField, last_word_length


class FakeContext:
    """Records what a TextField sends to the compositor."""
    def __init__(self):
        self.sent = []
        self.dispatcher = {}
    def commit_string(self, serial, text): self.sent.append(("commit", text))
    def keysym(self, serial, t, sym, state, mods):
        if state: self.sent.append(("key", sym))
    def delete_surrounding_text(self, index, length): self.sent.append(("delete", index, length))
    def destroy(self): self.sent.append(("destroy",))
    def report(self, text, cursor=None):
        self.dispatcher["surrounding_text"](self, text, len(text.encode()) if cursor is None else cursor, 0)


def field():
    loop = Loop(); f = TextField(loop); ctx = FakeContext(); f.attach(ctx)
    return loop, f, ctx


def test_last_word_length():
    assert last_word_length("hello world") == 5
    assert last_word_length("hello world  ") == 7
    assert last_word_length("hello!") == 1
    assert last_word_length("") == 1
    assert last_word_length("Grüße") == 5


def test_commit_and_keys():
    _, f, ctx = field()
    f.commit("Hi"); f.key(BACKSPACE); f.key(ENTER)
    assert ctx.sent == [("commit", "Hi"), ("key", 0xFF08), ("key", 0xFF0D)]
    assert f.before_cursor() == ""


def test_before_cursor_prefers_report():
    _, f, ctx = field()
    f.commit("abc")
    assert f.before_cursor() == "abc" and f.reported_before_cursor() is None
    ctx.report("xyz abc")
    assert f.before_cursor() == "xyz abc"
    ctx.report("äbc", cursor=2)             # byte offset: just the "ä"
    assert f.before_cursor() == "ä"


def test_empty_report_from_app_without_text_input_is_ignored():
    _, f, ctx = field()
    f.commit("abc"); ctx.report("")
    assert f.reported_before_cursor() is None and f.before_cursor() == "abc"


def test_replace_through_text_input_when_reported():
    _, f, ctx = field()
    f.commit(" helle"); ctx.report("say helle"); ctx.sent.clear()
    assert f.replace(" helle", " hällo")
    assert ctx.sent == [("delete", -6, 6), ("commit", " hällo")]
    ctx.report("say hällo"); ctx.sent.clear()
    assert f.replace(" hällo", " hi")       # bytes, not characters
    assert ctx.sent[0] == ("delete", -7, 7)


def test_replace_with_keys_holds_the_text_back():
    loop, f, ctx = field()
    f.commit("helle"); ctx.sent.clear()
    assert f.replace("helle", "hello")
    assert ctx.sent == [("key", 0xFF08)] * 5, "the new text must not be sent together with the keys"
    assert loop.has_timer("held-back-text")
    loop.set_timer("stop", 0.4, loop.stop); loop.run()
    assert ctx.sent[-1] == ("commit", "hello")


def test_held_back_text_keeps_its_place():
    _, f, ctx = field()
    f.commit("helle"); ctx.sent.clear()
    f.replace("helle", "hello"); f.commit("!")
    assert ctx.sent[-2:] == [("commit", "hello"), ("commit", "!")]


def test_stale_report_falls_back_to_keys():
    _, f, ctx = field()
    ctx.report("old text"); f.commit(" new"); ctx.report("old text"); ctx.sent.clear()
    f.replace(" new", " NEW")
    assert ctx.sent == [("key", 0xFF08)] * 4


def test_detach():
    _, f, ctx = field()
    assert f.detach(ctx) and not f.active and ("destroy",) in ctx.sent
    other = FakeContext()
    assert not f.detach(other)


def test_preview_field():
    f = PreviewField()
    f.commit("hello wrld"); assert f.replace(" wrld", " world") and f.text == "hello world"
    f.backspace(6); assert f.text == "hello"
    f.key(BACKSPACE); assert f.text == "hell"
    assert not f.replace("xyz", "abc")


def test_loop_timers_and_calls():
    loop = Loop(); seen = []
    loop.set_timer("b", 0.05, lambda: seen.append("b"))
    loop.set_timer("a", 0.01, lambda: seen.append("a"))
    loop.set_timer("gone", 0.02, lambda: seen.append("gone")); loop.cancel_timer("gone")
    loop.set_timer("stop", 0.1, loop.stop)
    threading.Thread(target=lambda: loop.call_soon_threadsafe(seen.append, "thread")).start()
    loop.run()
    assert sorted(seen) == ["a", "b", "thread"] and seen.index("a") < seen.index("b")


def test_loop_survives_failing_callback():
    loop = Loop(); seen = []
    loop.set_timer("bad", 0.0, lambda: 1 / 0)
    loop.set_timer("good", 0.02, lambda: (seen.append(1), loop.stop()))
    import logging; logging.disable(logging.CRITICAL); loop.run(); logging.disable(logging.NOTSET)
    assert seen == [1]


def test_ctc_beam_search():
    chars = ["", "a", "b", " "]
    def frames(*ids):
        p = np.full((len(ids), 4), 0.01, dtype=np.float32)
        for t, i in enumerate(ids): p[t, i] = 0.97
        return p
    assert ctc_beam_search(frames(1, 1, 0, 1, 2), chars)[0][0] == "aab"     # repeat needs a blank between
    assert ctc_beam_search(frames(0, 0, 0), chars)[0][0] == ""
    p = frames(1, 0, 2); p[0, 1], p[0, 2] = 0.5, 0.48
    texts = [t for t, _ in ctc_beam_search(p, chars)]
    assert texts[0] == "ab" and "bb" not in texts[:1] and "b" in texts      # alternatives are offered


def test_lexicon_penalises_unknown_words():
    lex = Lexicon([]); lex.words = {"guten", "morgen"}
    assert lex.bonus("Guten Morgen") == 0 and lex.bonus("Duten Morgen") < 0
    assert lex.bonus("12:30") == 0


def test_arrow_keys():
    _, f, ctx = field()
    for name in ("left", "up", "right", "down"): f.key(name)
    assert ctx.sent == [("key", 0xFF51), ("key", 0xFF52), ("key", 0xFF53), ("key", 0xFF54)]
    p = PreviewField(); p.commit("abc"); p.key("left"); assert p.text == "abc"


def test_edit_distance():
    assert edit_distance_at_most("kritzel", "kritzel", 0) and edit_distance_at_most("kritzal", "kritzel", 1)
    assert edit_distance_at_most("kritze", "kritzel", 1) and edit_distance_at_most("kkritzel", "kritzel", 1)
    assert not edit_distance_at_most("kratzal", "kritzel", 1) and edit_distance_at_most("kratzal", "kritzel", 2)


def test_personal_words():
    lex = Lexicon([], 1.5, ["Kritzel", "KWin", "foo"]); lex.words = {"guten", "morgen", "kratzen"}
    assert lex.known("kritzel") and lex.bonus("Kritzel") > 0 > lex.bonus("Kritzal")
    assert lex.personalise("Guten Kritzal") == ("Guten Kritzel", 1.0)       # one letter off
    assert lex.personalise("kwin")[0] == "KWin"                             # the user's capitals
    assert lex.bonus("KWin") - lex.personalise("kwin")[1] > lex.bonus("kwin")
    assert lex.personalise("Foo") is None, "a capital at the start of a sentence stays"
    assert lex.personalise("kratzen") is None, "dictionary words are left alone"
    assert lex.personalise("fox") is None, "short words must match exactly"
    assert lex.personalise("Guten Morgen") is None
    assert not Lexicon([]) and Lexicon([], 1.5, ["x"])


def test_language_labels():
    assert short_names(["de_DE", "en_US"]) == {"de_DE": "DE", "en_US": "EN"}
    assert short_names(["en_US", "en_GB", "fr"]) == {"en_US": "US", "en_GB": "GB", "fr": "FR"}
    assert languages.name("de_DE") == "Deutsch (DE)" and languages.name("fr") == "Français" and languages.name("xx_YY") == "xx_YY"
    assert languages.letters_of(["en_US"]) == "" and set(languages.letters_of(["de_DE", "en"])) == set("äöüß")
    assert languages.letters_of(["de", "xx"]) is None, "an unknown language rules nothing out"


def config_from(text):
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
        f.write(text)
    cfg = Config(f.name); os.unlink(f.name)
    return cfg


def test_space_after_each_word():
    typed = compose("hello", Context(""), "after")
    assert (typed.text, typed.eats_space, typed.trailing_space) == ("hello ", False, True)
    # the user's case: "hell" is in the field (the "e" and the space were deleted), "o" is written
    assert compose("o", Context("hell"), "after").text == "o ", "nothing is ever put in front"
    # a comma or full stop closes up to the word before
    typed = compose(", and", Context("hello ", space_pending=True), "after")
    assert typed.eats_space and typed.text == ", and "
    assert not compose("and", Context("hello ", space_pending=True), "after").eats_space
    assert not compose(", and", Context("hello "), "after").eats_space, "only our own space is taken back"
    assert compose("(", Context(""), "after").text == "(", "no space after an opening bracket"


def test_other_space_modes_and_capitals():
    assert compose("world", Context("hello"), "before").text == " world"
    assert compose("world", Context("hello "), "before").text == "world"
    assert compose("world", Context("hello"), "off").text == "world"
    assert compose("hello", Context("", True), "after", capitalize=True).text == "Hello "
    assert compose("hello", Context("Done. "), "off", capitalize=True).text == "Hello"
    assert compose("hello", Context("and "), "off", capitalize=True).text == "hello"
    assert compose("hello", Context(""), "off", capitalize=True).text == "hello", "unknown text is not an empty field"
    assert needs_space("Hello", "world") and not needs_space("Hello", ", world") and not needs_space("(", "note")


def test_symbol_keys_and_spaces():
    typed = compose_symbol(".", Context("hello ", space_pending=True), "after")
    assert typed.eats_space and typed.text == ". " and typed.trailing_space
    assert compose_symbol("5", Context("hello ", space_pending=True), "after").text == "5"
    assert compose_symbol("(", Context("hello ", space_pending=True), "after").text == "("
    assert compose_symbol(".", Context("hello "), "after").text == "."
    assert compose_symbol(".", Context("hello ", space_pending=True), "off").text == "."


def all_key_sets():
    options = list(keys.OPTIONS)
    for n in range(len(options) + 1):
        yield from itertools.combinations(options, n)


def test_every_combination_of_keys_is_laid_out_sensibly():
    count = 0
    for options in all_key_sets():
        count += 1
        shown = keys.enabled_keys(options)
        rows = layout.arrange(shown)
        flat = [key for row in rows for key in row]
        assert flat == shown, "all keys, in their order"
        assert 1 <= len(rows) <= 4 and all(rows)
        names = [key.name for key in flat]
        assert names[0] == "backspace" and "settings" in names
        for row in rows:                                    # joined keys share a row
            for key in row:
                if key.joined and flat[flat.index(key) - 1].group == key.group:
                    assert flat[flat.index(key) - 1] in row, (options, key.name)
        assert layout.widest(rows) <= 7, (options, layout.widest(rows))
        rects = layout.key_rects(rows, 500, 0, 300, 280, 6)
        assert set(rects) == set(names)
        boxes = list(rects.values())
        assert all(x >= 500 and x + w <= 800.001 and y >= 0 and y + h <= 280.001 and w >= 30 and h >= 30 for x, y, w, h in boxes), options
        for (x1, y1, w1, h1), (x2, y2, w2, h2) in itertools.combinations(boxes, 2):
            assert x1 + w1 <= x2 + 0.001 or x2 + w2 <= x1 + 0.001 or y1 + h1 <= y2 + 0.001 or y2 + h2 <= y1 + 0.001, options
    assert count == 2 ** len(keys.OPTIONS)
    default = [[key.name for key in row] for row in layout.arrange(keys.enabled_keys(keys.DEFAULT_OPTIONS))]
    assert default == [["backspace", "delword", "enter"], ["left", "up", "down", "right"],
                       ["copy", "paste", "space", "lang"], ["symbols", "keyboard", "float", "settings", "hide"]], default
    assert keys.parse_options("Arrows, hide nonsense hide") == ["arrows", "hide"]


def test_grids():
    for count, w, h in ((44, 930, 230), (44, 380, 230), (3, 930, 230), (1, 100, 60), (120, 300, 150)):
        cells = layout.grid(count, 10, 50, w, h, 4)
        assert len(cells) == count and all(x >= 10 - 0.001 and y >= 50 - 0.001 and x + cw <= 10 + w + 0.001 and y + ch <= 50 + h + 0.001
                                           for x, y, cw, ch in cells), (count, w, h)
    assert len({(round(x), round(y)) for x, y, _, _ in layout.grid(44, 0, 0, 930, 230, 4)}) == 44
    assert layout.grid(0, 0, 0, 100, 100, 4) == [] and layout.grid(5, 0, 0, 0, 0, 4) == []
    assert len(layout.row_of_keys(30, 0, 0, 480, 34, 4, 42, 60)) == 10


def view_for(text, width=1280, page=None):
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PyQt6.QtGui import QGuiApplication
    globals()["_app"] = QGuiApplication.instance() or QGuiApplication([])
    from kritzel.config import Theme
    from kritzel import ui
    v = ui.View(Theme(), config_from(text)); v.layout(width, 280)
    if page:
        v.show_page(page, [("lang:en", "English", True), ("lang:de", "Deutsch", False), ("lang:+", "More", False)])
    return v


def test_panel_has_room_for_everything():
    from kritzel import ui
    everything = "[keys]\nkeys=" + " ".join(keys.OPTIONS) + "\nsymbol_row=true\n"
    for text in ("", everything, "[keys]\nkeys=\n", everything + "keys_side=left\nkey_size=large\n", "[keys]\nkey_size=small\n"):
        for width in (420, 760, 1280, 2560):
            v = view_for(text, width)
            rects = list(v.buttons.values()) + [k.rect for k in v.page_keys] + [v.canvas]
            assert all(-0.01 <= r.left() and r.right() <= width + 0.01 and 0 <= r.top() and r.bottom() <= 280.01 for r in rects), (text, width)
            assert not any(a.intersects(b) for i, a in enumerate(rects) for b in rects[i + 1:]), (text, width)
            assert v.canvas.width() >= 150 and v.canvas.height() >= 180, (text, width, v.canvas)
            assert min(r.width() for r in v.buttons.values()) >= 26, (text, width)
            for name, r in v.buttons.items():
                assert v.button_at(r.center().x(), r.center().y()) == name
            assert v.button_at(v.canvas.center().x(), v.canvas.center().y()) is None
            for page in (ui.SYMBOLS, ui.LANGUAGES):
                size = (v.w, v.h, dict(v.buttons))
                v.show_page(page, [("lang:en", "English", True), ("lang:+", "More", False)])
                assert (v.w, v.h, dict(v.buttons)) == size, "another page never changes the panel or its keys"
                assert v.canvas.isEmpty() and v.page_keys and not v.in_canvas(v.body.center().x(), v.body.center().y())
                assert all(v.body.adjusted(-0.01, -0.01, 0.01, 0.01).contains(k.rect) for k in v.page_keys), (text, width, page)
                assert all(v.button_at(k.rect.center().x(), k.rect.center().y()) == k.name for k in v.page_keys)
                v.show_page(ui.WRITE)
    assert view_for("").canvas.height() == 230 and not view_for("").page_keys, "no symbol row unless asked for"
    left = view_for("[keys]\nkeys_side=left\n")
    assert left.buttons["backspace"].right() < left.canvas.left()


def test_settings_file_round_trip():
    d = tempfile.mkdtemp(); path = os.path.join(d, "sub", "config.ini")
    save_config({"height": "300", "symbols": "# ; = : % ...", "symbol_row": "true", "languages": "de_DE, en_US", "keys": ""}, path)
    cfg = Config(path)
    assert cfg.height == 300 and cfg.symbol_row_keys() == ["#", ";", "=", ":", "%", "..."] and cfg.languages == "de_DE, en_US"
    assert cfg.show == "touch" and cfg.delay_ms == 800 and cfg.keys == "" and keys.parse_options(cfg.keys) == []
    assert all(cfg.text(o.key) == o.format(getattr(cfg, o.key)) for o in __import__("kritzel.config").config.OPTIONS)
    words = os.path.join(d, "words.txt"); save_words(["Kritzel", "Über"], words)
    assert load_words(words) == ["Kritzel", "Über"] and load_words(os.path.join(d, "none")) == []


def test_every_setting_is_documented_with_its_default():
    from kritzel.config import OPTIONS
    here = os.path.dirname(os.path.abspath(__file__))
    template = open(os.path.join(here, "..", "data", "config.ini"), encoding="utf-8").read()
    section = None
    documented = {}
    for line in template.splitlines():
        if line.startswith("["):
            section = line.strip("[]")
        elif line.startswith("#") and "=" in line and not line.startswith("# "):
            key, _, value = line[1:].partition("=")
            documented[key.strip()] = (section, value.strip())
    for o in OPTIONS:
        assert documented.get(o.key) == (o.section, o.format(o.default)), (o.key, documented.get(o.key))
    assert len(documented) == len(OPTIONS)


def test_settings_of_version_1_3_are_understood():
    old = config_from("[panel]\nheight = 300\npen_width = 3.0\nsymbol_row = true\nsymbols = a b\n"
                      "[recognition]\nauto_space = true\nlanguages = de_DE\n")
    assert old.height == 300 and old.pen_width == 3.0 and old.symbols == "a b" and old.auto_space == "after"
    assert old.symbol_row is False, "1.3 wrote the symbol row into every file; it is off unless asked for again"
    assert config_from("[recognition]\nauto_space = false\n").auto_space == "off"
    new = Config(os.devnull)
    assert new.symbol_row is False and new.auto_space == "after" and "arrows" in new.keys and "copy" in new.keys


def test_language_choice():
    import kritzel.languages as L
    saved = {}
    L.load_state, L.save_state = (lambda: dict(saved)), (lambda state: (saved.clear(), saved.update(state)))
    L.find_dictionary = lambda code: "/dict/" + code[:2] if code[:2] in ("en", "de") else None
    two = config_from("[recognition]\nlanguages = en_US, de_DE, fr\n")
    choice = LanguageChoice(two)
    assert choice.codes == ["en_US", "de_DE", "fr"] and choice.current == "en_US" and choice.active() == ["en_US"]
    assert choice.choices() == ["en_US", "de_DE", "fr"] and choice.label == "EN"
    choice.select("de_DE")
    assert choice.active() == ["de_DE"] and saved["language"] == "de_DE" and LanguageChoice(two).current == "de_DE"
    choice.next(); assert choice.current == "fr"
    choice.next(); assert choice.current == "en_US"
    choice.select("xx"); assert choice.current == "en_US", "only what is offered can be chosen"
    with_all = config_from("[recognition]\nlanguages = en_US, de_DE\nlanguage_all = true\n")
    choice.update(with_all); assert choice.choices() == ["en_US", "de_DE", L.ALL]
    choice.select(L.ALL); assert choice.active() == ["en_US", "de_DE"] and choice.label == "EN+DE"
    choice.update(two); assert choice.current == "en_US", "'all' is no longer offered"
    no_key = config_from("[keys]\nkeys = enter\n[recognition]\nlanguages = en_US, de_DE\n")
    choice.update(no_key); assert choice.active() == ["en_US", "de_DE"], "without the key: all languages"
    one = config_from("[recognition]\nlanguages = de_DE\n")
    choice.update(one); assert choice.active() == ["de_DE"] and choice.choices() == ["de_DE"] and not choice.can_switch()
    none = config_from("[recognition]\nlanguages = none\n")
    choice.update(none); assert choice.active() == [] and choice.label == "–" and choice.choices() == []
    unknown = config_from("[recognition]\nlanguages = xx_YY, en_US\n")
    choice.update(unknown); assert choice.codes == ["en_US"], "a language nothing is known about is left out"


def test_dictionary_corrections():
    def lexicon(mode):
        lex = Lexicon([], 1.5, corrections=mode); lex.words = frozenset({"hello", "world", "help"}); lex.letters = "abcdefghijklmnopqrstuvwxyz"
        return lex
    assert lexicon("off").alternatives("helle world") == []
    suggest = dict(lexicon("suggest").alternatives("helle world"))
    assert "hello world" in suggest
    # "suggest": the reading itself stays first; "auto": the dictionary word wins
    assert lexicon("suggest").bonus("helle world") - 0 > lexicon("suggest").bonus("hello world") - suggest["hello world"]
    auto = dict(lexicon("auto").alternatives("helle world"))
    assert lexicon("auto").bonus("helle world") < lexicon("auto").bonus("hello world") - auto["hello world"]
    assert lexicon("suggest").alternatives("hello world") == [], "nothing to correct"
    assert dict(lexicon("suggest").alternatives("Helle"))  and list(dict(lexicon("suggest").alternatives("Helle")))[0][0] == "H"


def test_letters_of_the_language():
    from kritzel.recognizer import Recognizer
    r = Recognizer.__new__(Recognizer)
    r.chars = ["", "a", "ö", "é", "ł", "1", "中", " "]
    r._script_mask = np.array([1, 1, 1, 1, 0, 1, 0, 1], dtype=np.float32)      # "western"
    r.set_letters("äöüß"); assert list(r.mask) == [1, 1, 1, 0, 0, 1, 0, 1], "German: ö but no é"
    r.set_letters(""); assert list(r.mask) == [1, 1, 0, 0, 0, 1, 0, 1], "English: neither"
    r.set_letters("ąćęłńóśźż"); assert list(r.mask) == [1, 1, 0, 0, 1, 1, 0, 1], "Polish letters count even with 'western'"
    r.set_letters(None); assert list(r.mask) == [1, 1, 1, 1, 0, 1, 0, 1]


def test_extra_keys_and_shortcuts():
    from kritzel import fakekeys
    from kritzel.textfield import KEYSYMS
    for key in keys.KEYS:
        kind, argument = key.action
        assert kind in (keys.KEYSYM, keys.SHORTCUT, keys.TEXT, keys.COMMAND)
        assert kind != keys.KEYSYM or argument in KEYSYMS, key.name
        assert kind != keys.SHORTCUT or argument in fakekeys.SHORTCUTS, key.name
        assert key.option is None or key.option in keys.OPTIONS, key.name
    assert set(keys.DEFAULT_OPTIONS) <= set(keys.OPTIONS) and " ".join(keys.DEFAULT_OPTIONS) == Config(os.devnull).keys

    class Device:
        version = 4
        def __init__(self): self.sent = []
        def authenticate(self, *a): self.sent.append("auth")
        def keyboard_key(self, key, state): self.sent.append((key, state))
    class Session: g = {}
    assert not fakekeys.FakeKeyboard(Session()).available and not fakekeys.FakeKeyboard(Session()).press("copy")
    Session.g = {"org_kde_kwin_fake_input": Device()}
    kb = fakekeys.FakeKeyboard(Session()); assert kb.press("paste") and kb.press("cut")
    assert Session.g["org_kde_kwin_fake_input"].sent == ["auth", (42, 1), (110, 1), (110, 0), (42, 0), (42, 1), (111, 1), (111, 0), (42, 0)]
    Session.g["org_kde_kwin_fake_input"].version = 3
    assert not fakekeys.FakeKeyboard(Session()).available


def test_config_defaults_and_clamping():
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
        f.write("[panel]\nheight = 99999\nmax_width = abc\n[recognition]\nreplace = KEYS\nauto_insert = no\n[look]\nink_color = red\n[keys]\nkey_size = huge\n")
    cfg = Config(f.name); os.unlink(f.name)
    assert cfg.height == 900 and cfg.max_width == 0 and cfg.replace == "keys" and cfg.auto_insert is False
    assert cfg.ink_color == "" and cfg.key_size == "normal"
    assert Config("/nonexistent").delay_ms == 800


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1; print("FAIL", name, f"{type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)

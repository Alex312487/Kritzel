"""Unit tests for the parts that need no compositor.  Run: python3 tests/test_units.py"""
import os, sys, tempfile, threading
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import numpy as np
from kritzel.config import Config
from kritzel.eventloop import Loop
from kritzel.recognizer import Lexicon, ctc_beam_search
from kritzel.textfield import BACKSPACE, ENTER, PreviewField, TextField, last_word_length, needs_space


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


def test_needs_space():
    assert needs_space("Hello", "world")
    assert not needs_space("", "world")
    assert not needs_space("Hello ", "world")
    assert not needs_space("Hello", ", world")
    assert not needs_space("(", "note")
    assert not needs_space("Hello\n", "world")


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


def test_config_defaults_and_clamping():
    with tempfile.NamedTemporaryFile("w", suffix=".ini", delete=False) as f:
        f.write("[panel]\nheight = 99999\nmax_width = abc\n[recognition]\nreplace = KEYS\nauto_insert = no\n")
    cfg = Config(f.name); os.unlink(f.name)
    assert cfg.height == 900 and cfg.max_width == 0 and cfg.replace == "keys" and cfg.auto_insert is False
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

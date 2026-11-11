"""Unit tests for what version 1.5 added: gestures, the history of
insertions, learnt words, the fitted pause, hints from the text field,
export and import.  No compositor needed.  Run: python3 tests/test_features.py"""
import json, math, os, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
_home = tempfile.mkdtemp(prefix="kritzel-test.")      # nothing here reads or writes the real settings
os.environ.update(XDG_CONFIG_HOME=_home + "/config", XDG_STATE_HOME=_home + "/state")
import numpy as np
from kritzel import gestures, transfer
from kritzel.compose import Context, mid_sentence, unfinished_word
from kritzel.config import APP_DIR, OPTIONS, Config
from kritzel.eventloop import Loop
from kritzel.history import History, Insertion
from kritzel.learning import LEARNED_AFTER, MAX_WORDS, LearnedWords
from kritzel.lexicon import Lexicon
from kritzel.ink import Stroke
from kritzel.pace import FACTOR, HIGHEST, LOWEST, MIN_SAMPLES, Pace
from kritzel.reader import InkReader
from kritzel.recognizer import Hint, Recognizer
from kritzel.textfield import TextField

CANVAS = (6.0, 44.0, 900.0, 230.0)      # left, top, width, height of a writing area
BAND = 106.0                            # the usual height of writing in it


# -- gestures -----------------------------------------------------------------

def line(a, b, steps=30, wobble=0.0):
    return [(a[0] + (b[0] - a[0]) * i / steps, a[1] + (b[1] - a[1]) * i / steps + wobble * math.sin(i)) for i in range(steps + 1)]


def zigzag(x0, x1, y, passes=5):
    points = []
    for k in range(passes):
        ends = ((x1, y + 4 * k), (x0, y + 4 * k + 2)) if k % 2 else ((x0, y + 4 * k), (x1, y + 4 * k + 2))
        points += line(*ends, steps=10)
    return points


def kind(points, ink=()):
    gesture = gestures.classify(points, CANVAS, BAND, ink)
    return gesture.kind if gesture else None


INK = [(100, 120, 160, 200), (170, 120, 230, 200), (300, 120, 380, 200)]     # two words, far apart


def test_gestures_of_the_empty_writing_area():
    assert kind(line((500, 150), (250, 160), wobble=2)) == gestures.DELETE_WORD
    assert kind(line((250, 150), (480, 140), wobble=2)) == gestures.SPACE
    assert kind(line((500, 100), (505, 150)) + line((505, 150), (330, 155))[1:]) == gestures.ENTER
    assert kind(line((250, 150), (330, 150))) is None, "a dash is writing"
    assert kind(line((250, 150), (480, 230))) is None, "a slanted line is writing"
    assert kind(line((500, 80), (500, 200)) + line((500, 200), (470, 205))[1:]) is None, "a J is writing"
    assert kind(line((300, 100), (300, 200)) + line((300, 200), (480, 200))[1:]) is None, "an L is writing"
    assert kind([(300, 150)]) is None, "a dot is writing"
    # with ink waiting, the same lines are part of the writing
    assert kind(line((500, 150), (250, 160)), INK) is None and kind(line((250, 150), (480, 140)), INK) is None


def test_gestures_over_ink():
    scratch = gestures.classify(zigzag(100, 230, 150), CANVAS, BAND, INK)
    assert scratch.kind == gestures.SCRATCH and scratch.strokes == (0, 1), "only what the zigzag lies over"
    assert kind(zigzag(100, 230, 150), []) is None, "a zigzag over nothing is writing"
    assert kind(zigzag(500, 630, 150), INK) is None, "... and so is one beside the ink"
    assert kind(zigzag(100, 230, 150, passes=2), INK) is None, "there and back is not a scratch"
    split = gestures.classify(line((265, 50), (270, 268)), CANVAS, BAND, INK)
    assert split.kind == gestures.SPLIT and 265 <= split.x <= 270
    assert kind(line((265, 120), (268, 200)), INK) is None, "a line as tall as a letter is an l"
    assert kind(line((50, 50), (52, 268)), INK) is None, "a split needs ink on both sides"


def test_a_split_opens_a_gap_where_there_is_room():
    shifts = gestures.split_shifts(INK, 267, BAND, CANVAS)
    assert shifts[0] == shifts[1] < 0 < shifts[2] and math.isclose(shifts[2] - shifts[0], gestures.SPLIT_GAP * BAND)
    at_the_edge = [(8, 120, 60, 200), (70, 120, 130, 200)]
    shifts = gestures.split_shifts(at_the_edge, 65, BAND, CANVAS)
    assert shifts[0] == 0 and math.isclose(shifts[1], gestures.SPLIT_GAP * BAND), "no room on the left: all to the right"
    full = [(8, 120, 450, 200), (460, 120, 904, 200)]
    assert gestures.split_shifts(full, 455, BAND, CANVAS) == [0, 0], "nothing leaves the writing area"


def test_handwriting_is_never_a_gesture():
    with open(os.path.join(APP_DIR, "data", "sample-strokes.json"), encoding="utf-8") as f:
        strokes = [[tuple(p) for p in stroke] for stroke in json.load(f)["strokes"]]

    def bounds(points):
        return min(p[0] for p in points), min(p[1] for p in points), max(p[0] for p in points), max(p[1] for p in points)

    for i, stroke in enumerate(strokes):
        assert kind(stroke) is None, f"stroke {i} alone"
        assert kind(stroke, [bounds(s) for s in strokes[:i]]) is None, f"stroke {i} after the ones before it"


# -- history ------------------------------------------------------------------

def insertion(span, own_length=None, trailing_space=True, ate_space=False, candidates=("a", "b")):
    return Insertion(span, len(span) if own_length is None else own_length, Context(), trailing_space, candidates, 0, ate_space)


def test_history_walks_back():
    history = History(length=3)
    assert history.last is None and history.undo() is None
    for word in ("one ", "two ", "three ", "four "):
        history.add(insertion(word, candidates=(word,)))
    assert len(history) == 3 and history.last.span == "four ", "only the newest are kept"
    previous = history.undo()
    assert previous is history.last and previous.span == "three " and previous.restored and previous.candidates == ["three "]
    assert history.undo().span == "two " and history.undo() is None and history.last is None


def test_undoing_a_comma_gives_the_space_back_to_nobody():
    history = History()
    history.add(insertion("hello "))
    history.add(insertion(", ", ate_space=True))        # "hello " became "hello, "
    previous = history.undo()                           # the text field holds "hello" now
    assert (previous.span, previous.own_length, previous.trailing_space) == ("hello", 5, False)
    history.clear(); history.add(insertion("hello", trailing_space=False)); history.add(insertion(", ", ate_space=True))
    assert history.undo() is None and len(history) == 0, "a history that contradicts itself is dropped"


# -- learnt words -------------------------------------------------------------

def learned():
    return LearnedWords(os.path.join(tempfile.mkdtemp(), "state", "learned.json"))


def test_a_word_picked_twice_counts():
    words = learned()
    assert LEARNED_AFTER == 2
    assert not words.note("Kritzel rocks", "Kritzal rocks") and words.words() == []
    assert words.note("Kritzel", "Kritze1") and words.words() == ["Kritzel"], "True when a word starts to count"
    assert not words.note("Kritzel", "Kritzal") and words.counts == {"Kritzel": 3}
    assert "rocks" not in words.counts, "words both readings share are not corrections"
    assert LearnedWords(words.path).words() == ["Kritzel"], "remembered across restarts"
    assert not words.retract("Kritzel", "Kritzal") and words.counts == {"Kritzel": 2}
    assert words.retract("Kritzel", "Kritzal") and words.words() == [], "True when a word no longer counts"
    assert not words.retract("Kritzel", "Kritzal") and words.counts == {} and not words.retract("Kritzel", "Kritzal")
    words.note("Kritzel", "Kritzal"); words.note("Kritzel", "Kritzal")
    words.forget(["Kritzel", "never there"]); assert LearnedWords(words.path).counts == {}


def test_what_is_learnt_and_what_is_not():
    words = learned()
    words.note("The cat", "Tne cot", keep_capitals=False)
    assert set(words.counts) == {"the", "cat"}, "a sentence's capital is not the word's"
    words.note("The KWin", "Tne kwin", keep_capitals=False); words.note("Berlin", "berlin")
    assert {"KWin", "Berlin"} <= set(words.counts), "capitals inside a word, or in mid-sentence, are"
    words.note("a 42 x1 pass_word " + "x" * 50, "")
    assert set(words.counts) == {"the", "cat", "KWin", "Berlin", "pass", "word"}, "letters only, not too long"
    for i in range(MAX_WORDS + 20):
        words.note("w" + "".join(chr(97 + int(d)) for d in str(i)), "")
    assert len(words.counts) == MAX_WORDS and "the" in words.counts and "cat" not in words.counts, \
        "the least picked, oldest words make room"


# -- the fitted pause ---------------------------------------------------------

def test_pause_follows_the_writer():
    pace = Pace(0.8)
    for _ in range(MIN_SAMPLES - 1): pace.note_break(0.7)
    assert pace.pause == 0.8, "too few breaks to judge"
    for _ in range(60): pace.note_break(0.7)
    assert math.isclose(pace.pause, FACTOR * 0.7, rel_tol=0.01), "slow writing: a longer pause"
    for _ in range(120): pace.note_break(0.2)
    assert math.isclose(pace.pause, FACTOR * 0.2, rel_tol=0.01), "fast writing: a shorter one"
    for _ in range(120): pace.note_break(0.01)
    assert math.isclose(pace.pause, LOWEST * 0.8, rel_tol=0.01)
    for _ in range(120): pace.note_break(5.0)
    assert math.isclose(pace.pause, HIGHEST * 0.8, rel_tol=0.01), "within limits around the setting"
    steady = Pace(0.8)
    for i in range(200): steady.note_break(3.0 if i % 20 == 0 else 0.3)
    assert steady.pause < 1.0, "an odd long break changes little"


def test_remembered_pause_is_only_a_number_within_limits():
    assert Pace(0.8, 1.2).pause == 1.2 and Pace(0.8, 100).pause == 0.8 and Pace(0.8, 30).pause == HIGHEST * 0.8
    for junk in (None, "1.2", True, float("nan"), float("inf"), -1, [1]):
        assert Pace(0.8, junk).pause == 0.8, repr(junk)
    pace = Pace(0.8, 1.9); pace.configure(0.4); assert pace.pause == HIGHEST * 0.4, "the setting changed"
    pace.note_break(float("nan")); pace.note_break(-3); assert pace._breaks == []


# -- the ink reader -----------------------------------------------------------

class FakeLoop:
    """Timers that go off when the test says so."""
    def __init__(self): self.timers = {}
    def set_timer(self, name, delay, fn): self.timers[name] = (delay, fn)
    def cancel_timer(self, name): self.timers.pop(name, None)
    def has_timer(self, name): return name in self.timers
    def fire(self, name): self.timers.pop(name)[1]()


class FakeWorker:
    ready = True
    def __init__(self): self.jobs = []
    def submit(self, generation, image, hint): self.jobs.append((generation, hint))


def reader(text=""):
    cfg = Config.from_values(dict(line.split("=") for line in text.split()))
    loop, worker, typed, changes = FakeLoop(), FakeWorker(), [], []
    ink = InkReader(loop, worker, cfg, hint=lambda: "the hint", on_reading=typed.append,
                    on_change=lambda: changes.append(ink.pending), remember_pause=False)
    ink.fit(44.0, 230.0)
    return ink, loop, worker, typed, changes


def write(ink, x=100.0):
    stroke = Stroke()
    for i in range(10): stroke.add(x + 4 * i, 150.0 + (i % 3) * 8)
    ink.begin(); ink.add(stroke); ink.end()


def test_ink_is_read_ahead_and_typed_when_the_pause_is_over():
    ink, loop, worker, typed, changes = reader()
    write(ink)
    assert set(loop.timers) == {"recognize", "preview"} and loop.timers["recognize"][0] == 0.8 and not worker.jobs
    loop.fire("preview")
    assert worker.jobs == [(ink.generation, "the hint")] and ink.pending == "" and not ink.waiting
    ink.on_result(ink.generation, [("hello", 0.9), ("hallo", 0.1)])
    assert ink.pending == "hello" and changes == ["hello"] and typed == [], "shown, not typed"
    loop.fire("recognize")
    assert typed == [["hello", "hallo"]] and len(worker.jobs) == 1, "typed without asking the recogniser again"
    assert ink.pending == "" and not ink.waiting and ink.strokes, "the ink stays until the panel says done()"
    ink.done(); assert not ink.strokes and not loop.timers


def more_ink(ink):
    stroke = Stroke(); stroke.add(200, 150); stroke.add(240, 160); ink.add(stroke); ink.end()


def test_an_answer_for_ink_that_has_changed_is_dropped():
    ink, loop, worker, typed, changes = reader()
    write(ink); loop.fire("preview"); first = ink.generation
    ink.begin()                                         # the user writes on
    assert not loop.timers and ink.generation != first
    ink.on_result(first, [("he", 0.9)])
    assert ink.pending == "" and typed == [] and len(worker.jobs) == 1, "nothing happens while the pen is down"
    more_ink(ink); loop.fire("preview")
    assert worker.jobs[-1][0] == ink.generation and len(worker.jobs) == 2

    ink, loop, worker, typed, changes = reader()
    write(ink); loop.fire("preview"); first = ink.generation
    ink.begin(); more_ink(ink); loop.fire("preview")
    assert len(worker.jobs) == 1, "one question at a time: the recogniser only answers the newest"
    ink.on_result(first, [("he", 0.9)])                 # the answer comes when the pen is up again
    assert ink.pending == "" and worker.jobs[-1][0] == ink.generation and len(worker.jobs) == 2, "asked again"
    loop.fire("recognize")
    assert ink.waiting and typed == [] and len(worker.jobs) == 2, "waits for the answer that is under way"
    ink.on_result(ink.generation, [("hello", 0.9)])
    assert typed == [["hello"]] and not ink.waiting


def test_a_key_does_not_wait_for_the_pause():
    ink, loop, worker, typed, changes = reader()
    ink.read_now(); assert not worker.jobs, "nothing to read"
    write(ink); ink.read_now()
    assert ink.waiting and not loop.timers and len(worker.jobs) == 1 and changes == [""], "the panel shows that it waits"
    ink.on_result(ink.generation, [])
    assert typed == [[]], "what cannot be read is reported as that"
    ink.clear(); write(ink); loop.fire("preview"); stale = ink.generation; write(ink, 200.0)
    ink.read_now(); ink.on_result(stale, [("x", 1.0)])
    assert len(typed) == 1 and worker.jobs[-1][0] == ink.generation, "an outdated answer: the ink is read again at once"


def test_reader_settings():
    ink, loop, worker, typed, changes = reader("preview=false")
    write(ink); assert set(loop.timers) == {"recognize"}, "no reading ahead"
    loop.fire("recognize"); ink.on_result(ink.generation, [("a", 1.0)]); assert typed == [["a"]] and "a" not in changes
    ink, loop, worker, typed, changes = reader("delay_ms=200")
    write(ink); assert set(loop.timers) == {"recognize"}, "a pause shorter than the look ahead needs none"
    worker.ready = False; loop.fire("recognize")
    assert not worker.jobs and "recognize" in loop.timers, "the model is still loading: asks again"
    worker.ready = True; loop.fire("recognize"); assert len(worker.jobs) == 1
    ink, loop, worker, typed, changes = reader("delay_auto=true")
    for i in range(80): ink.pace.note_break(0.6)
    assert ink.pause > 1.4, "the fitted pause is used"
    ink.configure(Config(os.devnull)); assert ink.pause == 0.8


def test_erasing_and_clearing():
    ink, loop, worker, typed, changes = reader()
    write(ink); write(ink, 300.0)
    ink.begin(erasing=True); assert ink.erase(110.0, 152.0, 9.0) and not ink.erase(600.0, 60.0, 9.0); ink.end()
    assert len(ink.strokes) == 1 and "recognize" in loop.timers
    ink.begin(erasing=True); ink.erase(310.0, 152.0, 9.0); ink.end()
    assert not ink.strokes and not loop.timers and changes[-1] == "", "nothing left to read"
    write(ink); loop.fire("preview"); ink.on_result(ink.generation, [("a", 1.0)]); ink.clear()
    assert not ink.strokes and ink.pending == "" and not loop.timers and typed == []
    top = ink.linescale.top; write(ink); ink.fit(80.0, 150.0); assert ink.linescale.top == top, "not while ink is waiting"
    ink.clear(); ink.fit(80.0, 150.0); assert ink.linescale.top != top


# -- what the text field tells the recogniser -----------------------------------

class FakeContext:
    def __init__(self): self.dispatcher = {}
    def destroy(self): pass


def test_kinds_of_text_field():
    f = TextField(Loop()); ctx = FakeContext(); f.attach(ctx)
    def kind_of(hint, purpose):
        ctx.dispatcher["content_type"](ctx, hint, purpose); return f.plain, f.prose, f.secret, f.numeric
    assert kind_of(0, 0) == (False, True, False, None)
    assert kind_of(0, 2) == (True, False, False, "0123456789"), "digits"
    assert kind_of(0, 4)[3] == "0123456789+-()/#* ", "phone"
    assert kind_of(0, 10)[3] == "0123456789:. ", "time"
    assert kind_of(0, 6) == (True, False, False, None), "e-mail: no prose, but letters"
    assert kind_of(0, 8) == (True, False, True, None), "password"
    assert kind_of(0x40, 0) == (True, False, True, None) and kind_of(0x40, 3)[2:] == (True, None), "hidden text"
    assert kind_of(0, 12) == (False, False, False, None), "a terminal: spaces as usual, but nothing is learnt"
    f.attach(FakeContext()); assert (f.plain, f.secret, f.numeric) == (False, False, None), "the next field starts afresh"


def test_number_fields_only_yield_their_characters():
    r = Recognizer.__new__(Recognizer)
    r.chars = ["", "1", "l", "7", "+", " "]; r.mask = np.ones(6, dtype=np.float32); r._character_masks = {}; r.lexicon = None
    probs = np.array([[0.1, 0.3, 0.6, 0, 0, 0], [0.9, 0.05, 0.05, 0, 0, 0], [0.1, 0, 0, 0.5, 0.4, 0]], dtype=np.float32)
    r.session = type("Session", (), {"run": lambda self, _outputs, feeds: [probs[None]]})()
    r.input_name = "x"
    image = np.zeros((48, 60), dtype=np.uint8)
    assert r.recognize(image)[0][0] == "l7"
    assert r.recognize(image, hint=Hint("0123456789"))[0][0] == "17", "an l cannot be meant among digits"
    assert all(set(text) <= set("17+") for text, _ in r.recognize(image, hint=Hint("0123456789+")))
    assert list(r._mask_for("1+")) == [1, 1, 0, 0, 1, 0] and r._mask_for(None) is r.mask


def test_words_before_the_cursor():
    assert unfinished_word("say hel") == "hel" and unfinished_word("hello ") == "" and unfinished_word("") == ""
    assert unfinished_word("well-kn") == "kn" and unfinished_word("x1y") == "y" and unfinished_word("Grüß") == "Grüß"
    assert len(unfinished_word("a" * 500)) == 40
    assert mid_sentence(Context("hello ")) and mid_sentence(Context("one, ")) and mid_sentence(Context("hel"))
    assert not mid_sentence(Context("")) and not mid_sentence(Context("Done. ")) and not mid_sentence(Context('He said "'))


def english():
    lex = Lexicon([], 1.5); lex.words = frozenset({"hello", "help", "some", "world", "Sam", "Will", "will"})
    lex.letters = "abcdefghijklmnopqrstuvwxyz"
    return lex


def test_writing_that_continues_a_word_is_judged_with_it():
    lex = english()
    assert lex.bonus("lo") < 0 == lex.bonus("lo", "hel"), '"lo" is no word, "hel" + "lo" is'
    assert lex.bonus("lo world", "hel") == 0 > lex.bonus("lx world", "hel")
    assert lex.bonus("o", "hell") == 0 > lex.bonus("a", "hell"), "a single letter can finish a word"
    assert lex.bonus(" lo", "hel") < 0 and lex.bonus(", lo", "hel") < 0, "a space or a comma ends the word before"
    fixes = dict(lex.alternatives("la world", "hel"))
    assert set(fixes) == {"lo world"}, "corrected as part of the word, and only the letters that were written"
    assert lex.alternatives("lo", "hel") == [] and lex.alternatives("lp", "he") == []
    assert dict(lex.alternatives("helle")) and lex.alternatives("helle") == lex.alternatives("helle", ""), "no prefix: as before"


def test_capitals_that_look_like_small_letters():
    lex = english()
    assert lex.mid_sentence_form("Some day") == "some day"
    assert lex.mid_sentence_form("Sam") is None and lex.mid_sentence_form("Will") is None, "names keep their capital"
    assert lex.mid_sentence_form("Hello") is None, "an H does not look like an h"
    assert lex.mid_sentence_form("SOME") is None and lex.mid_sentence_form("Sxme") is None
    assert lex.mid_sentence_form("some") is None and lex.mid_sentence_form("5 Some") is None
    personal = Lexicon([], 1.5, ["Some"]); personal.words = lex.words
    assert personal.mid_sentence_form("Some") is None, "the user's own spelling stands"
    german = english(); german.capitalises_nouns = True
    assert german.mid_sentence_form("Some") is None, "where nouns have capitals nothing can be said"


def test_the_context_decides_between_look_alikes():
    r = Recognizer.__new__(Recognizer)
    r.chars = ["", "S", "s", "o", " "]; r.mask = None; r._character_masks = {}
    r.lexicon = Lexicon([], 1.5); r.lexicon.words = frozenset({"so"}); r.lexicon.letters = "os"
    probs = np.array([[0.02, 0.5, 0.48, 0, 0], [0.98, 0.01, 0.01, 0, 0], [0.02, 0, 0, 0.98, 0]], dtype=np.float32)
    r.session = type("Session", (), {"run": lambda self, _outputs, feeds: [probs[None]]})()
    r.input_name = "x"
    image = np.zeros((48, 60), dtype=np.uint8)
    assert [text for text, _ in r.recognize(image)][:2] == ["So", "so"], "the picture alone"
    assert [text for text, _ in r.recognize(image, hint=Hint(mid_sentence=True))][:2] == ["so", "So"]


# -- export and import --------------------------------------------------------

def test_settings_travel_in_one_file():
    values = {o.key: o.format(o.default) for o in OPTIONS}
    values.update(height="300", languages="de_DE, en_US", model="/home/me/model.onnx", threads="8")
    path = os.path.join(tempfile.mkdtemp(), "mine" + transfer.SUFFIX)
    transfer.save(path, values, ["Kritzel", "Über"])
    imported, words = transfer.load(path)
    assert words == ["Kritzel", "Über"] and imported["height"] == "300" and imported["languages"] == "de_DE, en_US"
    assert "model" not in imported and "threads" not in imported, "what belongs to a computer stays there"
    assert set(imported) == set(values) - {"model", "threads"}
    cfg = Config.from_values(imported)
    assert cfg.height == 300 and cfg.model == "small" and cfg.gestures is True


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1; print("FAIL", name, f"{type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)

"""Tests for the things that keep Kritzel's files, signals and child
processes to itself.  No compositor needed.  Run: python3 tests/test_hardening.py"""
import array, logging, os, random, socket, stat, struct, subprocess, sys, tempfile, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
from kritzel import config, control, lexicon, switch, wayland
from kritzel.config import Config, load_words, save_config, save_words, write_atomically
from kritzel.eventloop import Loop
from kritzel.textfield import PreviewField, TextField


class patched:
    """with patched(obj, name=value, ...): attributes are put back afterwards."""
    def __init__(self, target, **values): self.target, self.values = target, values
    def __enter__(self):
        self.old = {k: getattr(self.target, k) for k in self.values}
        for k, v in self.values.items(): setattr(self.target, k, v)
    def __exit__(self, *_):
        for k, v in self.old.items(): setattr(self.target, k, v)


class environment:
    """with environment(NAME=value or None): os.environ is put back afterwards."""
    def __init__(self, **values): self.values = values
    def __enter__(self):
        self.old = {k: os.environ.get(k) for k in self.values}
        for k, v in self.values.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)
    def __exit__(self, *_):
        for k, v in self.old.items():
            os.environ.pop(k, None) if v is None else os.environ.__setitem__(k, v)


def mode(path): return stat.S_IMODE(os.stat(path).st_mode)


# -- files ------------------------------------------------------------------

def test_a_value_cannot_add_settings_of_its_own():
    d = tempfile.mkdtemp(); path = os.path.join(d, "config.ini")
    save_config({"symbols": "a b\n[recognition]\nmodel = /tmp/evil.onnx\rthreads = 16", "height": "300\nshow = always"}, path)
    cfg = Config(path)
    assert cfg.model == "small" and cfg.threads == 2 and cfg.show == "touch", (cfg.model, cfg.threads, cfg.show)
    assert cfg.symbols.split()[:2] == ["a", "b"]
    words = os.path.join(d, "words.txt"); save_words(["Kritzel\nSecond", "", "Über"], words)
    assert load_words(words) == ["Kritzel Second", "Über"]


def test_files_are_private_and_written_in_one_step():
    d = tempfile.mkdtemp(); path = os.path.join(d, "new", "config.ini")
    old_umask = os.umask(0o022)
    try:
        save_config({"height": "300"}, path)
    finally:
        os.umask(old_umask)
    assert mode(path) == 0o600 and mode(os.path.dirname(path)) == 0o700
    assert os.listdir(os.path.dirname(path)) == ["config.ini"], "no temporary file is left behind"
    # a stale or planted "config.ini.tmp" is neither followed nor needed
    victim = os.path.join(d, "victim"); open(victim, "w").write("untouched")
    os.symlink(victim, path + ".tmp")
    save_config({"height": "310"}, path)
    assert open(victim).read() == "untouched" and Config(path).height == 310


def test_a_symlinked_settings_file_stays_a_symlink():
    d = tempfile.mkdtemp(); real = os.path.join(d, "dotfiles", "config.ini"); link = os.path.join(d, "config.ini")
    os.makedirs(os.path.dirname(real)); open(real, "w").write("[panel]\nheight = 200\n"); os.symlink(real, link)
    save_config({"height": "320"}, link)
    assert os.path.islink(link) and Config(real).height == 320


def test_write_failure_leaves_the_old_file_and_no_rubbish():
    d = tempfile.mkdtemp(); path = os.path.join(d, "state.json"); write_atomically(path, "old")
    try:
        write_atomically(path, "caf\udce9")          # cannot be encoded
    except UnicodeEncodeError:
        pass
    else:
        raise AssertionError("expected the write to fail")
    assert open(path).read() == "old" and os.listdir(d) == ["state.json"]


def test_log_file_is_private():
    from kritzel.__main__ import PrivateLogFile
    d = tempfile.mkdtemp(); path = os.path.join(d, "kritzel.log")
    open(path, "w").close(); os.chmod(path, 0o644)      # as an older version left it
    handler = PrivateLogFile(path, maxBytes=200, backupCount=1, encoding="utf-8")
    try:
        for i in range(20):
            handler.emit(logging.LogRecord("kritzel", logging.INFO, __file__, 1, "line %d of the log", (i,), None))
    finally:
        handler.close()
    assert mode(path) == 0o600 and mode(path + ".1") == 0o600, "also after the file was rotated"


def test_relative_xdg_directories_are_ignored():
    with environment(XDG_CONFIG_HOME="relative/dir", HOME="/home/someone"):
        assert config.xdg_dir("XDG_CONFIG_HOME", "~/.config") == "/home/someone/.config"
    with environment(XDG_CONFIG_HOME="/abs/dir"):
        assert config.xdg_dir("XDG_CONFIG_HOME", "~/.config") == "/abs/dir"
    with environment(XDG_RUNTIME_DIR="rel"):
        assert control.pid_file() == os.path.join(config.STATE_DIR, "kritzel.pid")


# -- settings that become file names ------------------------------------------

def test_language_codes_cannot_leave_the_dictionary_directories():
    d = tempfile.mkdtemp(); dicts = os.path.join(d, "hunspell"); os.makedirs(dicts)
    for name in ("de_DE", "en_GB-large", "en_US"):
        open(os.path.join(dicts, name + ".dic"), "w").write("1\nwort\n")
    open(os.path.join(d, "secret.dic"), "w").write("1\nsecret\n")
    with patched(lexicon, SEARCH_DIRS=(dicts,)):
        assert lexicon.find_dictionary("de_DE") == os.path.join(dicts, "de_DE.dic")
        assert lexicon.find_dictionary("de-DE") == os.path.join(dicts, "de_DE.dic")
        assert lexicon.find_dictionary("de_AT") == os.path.join(dicts, "de_DE.dic"), "another dictionary of the language"
        assert lexicon.find_dictionary("en_GB-large") == os.path.join(dicts, "en_GB-large.dic"), "a name with a dash"
        assert lexicon.find_dictionary("fr") is None
        for code in ("../secret", os.path.join(d, "secret"), ".", "..", "", "de\0"):
            assert lexicon.find_dictionary(code) is None, code
    path = os.path.join(d, "config.ini")
    open(path, "w").write(f"[recognition]\nlanguages = de_DE, ../secret; {d}/secret, en-GB, .hidden, sr_Latn_RS\n")
    assert Config(path).dictionary_languages() == ["de_DE", "en-GB", "sr_Latn_RS"]
    with environment(LC_ALL=None, LC_MESSAGES="x/../../etc.UTF-8", LANG="de_DE.UTF-8@euro"):
        assert Config(os.devnull).dictionary_languages() == ["en_US"]
    with environment(LC_ALL=None, LC_MESSAGES=None, LANG="de_DE.UTF-8@euro"):
        assert Config(os.devnull).dictionary_languages() == ["de_DE", "en_US"]


def test_broken_dictionary_header_is_survived():
    d = tempfile.mkdtemp(); dic = os.path.join(d, "xx_XX.dic")
    open(dic, "w", encoding="utf-8").write("2\nhallo/A\nwelt\n")
    for aff in (b"SET\n", b"SET \n", b"SETTINGS x\n", b"SET no-such-encoding\n", b"SET\tUTF-8\n", b""):
        open(dic[:-4] + ".aff", "wb").write(aff)
        lexicon._files.pop(dic, None)
        words = lexicon._read_dictionary(dic)          # must not raise
        assert words in (frozenset(), frozenset({"hallo", "welt"})), (aff, words)
    assert lexicon._dictionary_encoding(dic) == "utf-8"


def test_colour_scheme_name_is_a_file_name():
    assert config._is_file_name("BreezeDark") and config._is_file_name("My Scheme")
    for name in ("../../etc/passwd", "/etc/passwd", "a/b", "", ".", ".."):
        assert not config._is_file_name(name), name


def test_numbers_that_are_none():
    option = config.OPTION["pen_width"]
    assert option.parse("nan") == option.default and option.parse("inf") == option.hi and option.parse("-inf") == option.lo
    assert config.OPTION["height"].parse("inf") == config.OPTION["height"].default


def test_state_file_may_hold_anything():
    from kritzel import languages
    d = tempfile.mkdtemp(); state = os.path.join(d, "state.json")
    with patched(config, STATE_FILE=state):
        for text in ("[" * 100000, "{", "[1, 2]", '"text"', "\xff"):
            open(state, "w", encoding="latin-1").write(text)
            assert config.load_state() == {}, text[:20]
    for remembered in (["de_DE"], {"a": 1}, 7, None, "de_DE", "nonsense"):
        with patched(languages, load_state=lambda: {"language": remembered}):
            choice = languages.LanguageChoice(Config(os.devnull))       # must not raise
            assert choice.label and choice.title(choice.current)


# -- other processes ----------------------------------------------------------

def _sleeper(*arguments):
    return subprocess.Popen([sys.executable, "-c", "import time; time.sleep(60)", *arguments])


def test_only_a_kritzel_gets_our_signals():
    d = tempfile.mkdtemp()
    bystanders = [_sleeper("kritzel.log"), _sleeper(os.path.join(d, "kritzel", "config.ini")), _sleeper("--name=kritzel-notes")]
    panel, settings = _sleeper("-m", "kritzel", "-v"), _sleeper("-m", "kritzel", "settings", "languages")
    try:
        time.sleep(0.2)
        with environment(XDG_RUNTIME_DIR=d):
            def pid_in_file(pid, name=control.PANEL):
                open(control.pid_file(name), "w").write(str(pid))
                return control.running_pid(name)
            for p in bystanders:
                assert pid_in_file(p.pid) is None, p.args
                assert not control.reload_panel() and p.poll() is None, "a program that only mentions Kritzel is left alone"
            assert pid_in_file(panel.pid) == panel.pid
            assert pid_in_file(settings.pid) is None, "the settings window is not the panel"
            assert pid_in_file(settings.pid, control.SETTINGS) == settings.pid
            assert pid_in_file(panel.pid, control.SETTINGS) is None
            assert pid_in_file(os.getpid()) is None and pid_in_file(1) is None and pid_in_file(-1) is None
            assert pid_in_file("0") is None and pid_in_file("x" * 100) is None and pid_in_file(2 ** 40) is None
    finally:
        for p in bystanders + [panel, settings]:
            p.kill(); p.wait()


def test_pid_file_does_not_follow_links():
    d = tempfile.mkdtemp(); victim = os.path.join(d, "victim"); open(victim, "w").write("untouched")
    with environment(XDG_RUNTIME_DIR=d):
        os.symlink(victim, control.pid_file())
        control.write_pid()
        assert open(victim).read() == "untouched"
        assert not os.path.islink(control.pid_file()) and open(control.pid_file()).read() == str(os.getpid())
        assert mode(control.pid_file()) == 0o600 and sorted(os.listdir(d)) == ["kritzel.pid", "victim"]
        control.remove_pid(); control.remove_pid()
        assert os.listdir(d) == ["victim"]


def test_only_desktop_files_are_made_the_virtual_keyboard():
    d = tempfile.mkdtemp(); good = os.path.join(d, "org.kde.plasma.keyboard.desktop"); open(good, "w").close()
    script = os.path.join(d, "evil.sh"); open(script, "w").close()
    ran = []
    def fake_run(cmd, **_kw):
        ran.append(cmd)
        return subprocess.CompletedProcess(cmd, 0, "", "")
    with patched(switch, tool=lambda *names: "/usr/bin/" + names[0], PREVIOUS_FILE=os.path.join(d, "previous")), \
            patched(switch.subprocess, run=fake_run), patched(sys, stderr=open(os.devnull, "w")):
        for bad in ("--delete", "-x", "relative.desktop", script, d, "", os.path.join(d, "missing.desktop")):
            ran.clear()
            assert not switch.kwrite(bad) and not ran, bad
        assert switch.kwrite(good) and ran[0][-1] == good
        ran.clear()
        assert switch.kwrite(None) and ran[0][-1] == "--delete"
        for remembered, expected in ((good, good), (good + "\n", good), ("evil.desktop", ""), (script, ""), ("--delete", ""), ("\xff\xfe", "")):
            open(switch.PREVIOUS_FILE, "w", encoding="latin-1").write(remembered)
            assert switch.previous_keyboard() == expected, remembered


def test_try_out_text_reaches_the_clipboard_through_a_pipe():
    d = tempfile.mkdtemp(); fake = os.path.join(d, "wl-copy")
    open(fake, "w").write(f'#!/bin/sh\nprintf "%s\\n" "$@" > "{d}/argv"\ncat > "{d}/stdin.part" && mv "{d}/stdin.part" "{d}/stdin"\n')
    os.chmod(fake, 0o755)
    with environment(PATH=d + os.pathsep + os.environ.get("PATH", "")):
        field = PreviewField()
    secret = "my password ' \" $(x) -n --type"
    field.commit(secret)
    for _ in range(100):
        if os.path.exists(os.path.join(d, "stdin")): break
        time.sleep(0.05)
    assert open(os.path.join(d, "stdin")).read() == secret
    assert "password" not in open(os.path.join(d, "argv")).read(), "the command line can be read by every user"
    with environment(PATH=os.path.join(d, "nothing-here")):
        PreviewField().commit("no wl-copy, no harm")


def test_text_field_takes_no_string_for_an_empty_one():
    f = TextField(Loop())
    class Ctx:
        dispatcher = {}
        def destroy(self): pass
    f.attach(Ctx())
    f._on_surrounding_text(None, "abc", 3, 3)
    f._on_surrounding_text(None, None, 0, 0)          # must not raise
    assert f.reported_before_cursor() == ""


# -- the Wayland connection ----------------------------------------------------

def _connection():
    ours, theirs = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    with environment(WAYLAND_SOCKET=str(os.dup(ours.fileno()))):
        conn = wayland.Connection()
    ours.close()
    return conn, theirs


def _event(oid, opcode, body=b""):
    return struct.pack("=II", oid, ((8 + len(body)) << 16) | opcode) + body


def test_file_descriptors_nobody_wants_are_closed():
    conn, peer = _connection()
    keyboard = conn._new_object("wl_keyboard", 1)
    keymap = [m.name for m in keyboard.iface.events].index("keymap")
    r, w = os.pipe()
    peer.sendmsg([_event(keyboard.id, keymap, struct.pack("=II", 1, 4096))],
                 [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [r]))])
    assert conn.read(block=True) and len(conn.in_fds) == 1
    received = conn.in_fds[0]
    assert not os.get_inheritable(received), "not handed on to programs the panel starts"
    conn.dispatch()
    assert conn.in_fds == []
    try:
        os.fstat(received)
    except OSError:
        pass
    else:
        raise AssertionError("the unused file descriptor is still open")
    # with a handler the descriptor is the handler's
    got = []
    keyboard.dispatcher["keymap"] = lambda _k, fmt, fd, size: got.append(fd)
    peer.sendmsg([_event(keyboard.id, keymap, struct.pack("=II", 1, 4096))],
                 [(socket.SOL_SOCKET, socket.SCM_RIGHTS, array.array("i", [r]))])
    conn.read(block=True); conn.dispatch()
    os.fstat(got[0]); os.close(got[0]); os.close(r); os.close(w); peer.close()


def test_malformed_events_are_dropped_not_misread():
    conn, peer = _connection()
    registry = conn._new_object("wl_registry", 1)
    seen = []
    registry.dispatcher["global"] = lambda _r, name, interface, version: seen.append((name, interface, version))
    string = b"wl_shm\0\0"
    good = _event(registry.id, 0, struct.pack("=II", 7, 7) + string + struct.pack("=I", 1))
    lying = _event(registry.id, 0, struct.pack("=II", 7, 4000) + string + struct.pack("=I", 1))     # string longer than the message
    short = _event(registry.id, 0, struct.pack("=I", 7))
    logging.disable(logging.CRITICAL)
    try:
        peer.sendall(lying + short + good + _event(registry.id, 99) + _event(424242, 0))
        conn.read(block=True); conn.dispatch()
    finally:
        logging.disable(logging.NOTSET)
    assert seen == [(7, "wl_shm", 1)], seen
    peer.sendall(struct.pack("=II", registry.id, (4 << 16)))        # a message shorter than its own header
    conn.read(block=True)
    try:
        conn.dispatch()
    except wayland.ProtocolError:
        pass
    else:
        raise AssertionError("expected a protocol error")
    peer.close()


def test_handler_may_dispatch_again():
    """A handler that calls roundtrip() must not see an event twice."""
    conn, peer = _connection()
    registry = conn._new_object("wl_registry", 1)
    seen = []
    def on_global(_r, name, _interface, _version):
        seen.append(name)
        conn.dispatch()
    registry.dispatcher["global"] = on_global
    string = b"wl_shm\0\0"
    peer.sendall(b"".join(_event(registry.id, 0, struct.pack("=II", n, 7) + string + struct.pack("=I", 1)) for n in (1, 2, 3)))
    conn.read(block=True); conn.dispatch()
    assert seen == [1, 2, 3], seen
    peer.close()


def test_compositor_is_only_looked_for_in_the_runtime_directory():
    for runtime_dir in (None, "", "relative"):
        with environment(WAYLAND_SOCKET=None, WAYLAND_DISPLAY="wayland-0", XDG_RUNTIME_DIR=runtime_dir):
            try:
                wayland.Connection()
            except RuntimeError as e:
                assert "XDG_RUNTIME_DIR" in str(e)
            else:
                raise AssertionError("connected to a socket in the current directory")
    with environment(WAYLAND_SOCKET="not-a-number"):
        try:
            wayland.Connection()
        except RuntimeError:
            pass
        else:
            raise AssertionError("expected an error")
        assert "WAYLAND_SOCKET" not in os.environ


def test_oversized_request_is_refused():
    conn, peer = _connection()
    try:
        conn._queue(1, 0, b"x" * 70000)
    except ValueError:
        pass
    else:
        raise AssertionError("a request longer than the size field allows was sent")
    peer.close()


# -- same results as before, with less work -------------------------------------

def test_edit_distance_within_agrees_with_at_most():
    rnd = random.Random(4)
    for _ in range(3000):
        a = "".join(rnd.choice("abc") for _ in range(rnd.randint(0, 7)))
        b = "".join(rnd.choice("abc") for _ in range(rnd.randint(0, 7)))
        for limit in (0, 1, 2, 3):
            d = lexicon.edit_distance_within(a, b, limit)
            assert (d is not None) == lexicon.edit_distance_at_most(a, b, limit)
            if d is not None:
                assert d <= limit and not lexicon.edit_distance_at_most(a, b, d - 1), (a, b, limit, d)


def test_nearest_personal_word_is_chosen_as_before():
    def reference(lex, word):                   # the search as it was written up to 1.4
        lower = word.lower()
        limit = 2 if len(word) >= 8 else 1
        best = None
        for key, spelling in lex.personal.items():
            if len(key) < 4:
                continue
            for distance in range(1, limit + 1):
                if lexicon.edit_distance_at_most(lower, key, distance):
                    if best is None or distance < best[1]:
                        best = (spelling, distance)
                    break
        return None if best is None else (best[0], lexicon.EDIT_COST * best[1])
    rnd = random.Random(7)
    def word(n): return "".join(rnd.choice("abcde") for _ in range(n))
    for _ in range(300):
        lex = lexicon.Lexicon(personal=[word(rnd.randint(3, 10)) for _ in range(12)])
        for _ in range(20):
            w = word(rnd.randint(4, 10))
            if w.lower() not in lex.personal:
                assert lex._nearest_personal(w) == reference(lex, w), (w, list(lex.personal))


# -- learnt words, imported settings, password fields (version 1.5) -----------

def test_learnt_words_file_is_private_and_may_hold_anything():
    from kritzel.learning import MAX_COUNT, MAX_WORDS, LearnedWords
    d = tempfile.mkdtemp(); path = os.path.join(d, "state", "learned.json")
    words = LearnedWords(path); words.note("Kritzel", ""); words.note("Kritzel", "")
    assert mode(path) == 0o600 and mode(os.path.dirname(path)) == 0o700 and os.listdir(os.path.dirname(path)) == ["learned.json"]
    for junk in ("", "[]", "null", '{"words": 5}', '{"words": [1, 2]}', "[" * 100000, "\xff\xfe", '{"words": {"a": NaN}}'):
        with open(path, "w", encoding="latin-1") as f: f.write(junk)
        assert LearnedWords(path).counts == {}, junk[:20]
    entries = {"good": 2, "many": 10 ** 9, "x": 3, "two words": 3, "pass_word": 3, "abc1": 3, "line\nbreak": 3,
               "w" * 41: 3, "zero": 0, "minus": -1, "text": "3", "real": 2.5, "yes": True, "nested": {"a": 1}}
    with open(path, "w") as f: f.write(__import__("json").dumps({"words": entries}))
    assert LearnedWords(path).counts == {"good": 2, "many": MAX_COUNT}, "only words made of letters, with a sane count"
    with open(path, "w") as f:
        f.write(__import__("json").dumps({"words": {"w" + "".join(chr(97 + int(c)) for c in str(i)): 2 for i in range(MAX_WORDS * 3)}}))
    assert len(LearnedWords(path).counts) == MAX_WORDS
    with open(path, "w") as f: f.write('{"words": {"good": 2}, "padding": "' + "x" * 300000 + '"}')
    assert LearnedWords(path).counts == {}, "a file far too large is not ours"
    target = os.path.join(d, "elsewhere"); os.remove(path); os.symlink(target, path + ".tmp")
    LearnedWords(path).note("word", ""); assert not os.path.exists(target), "no predictable temporary name to plant a link at"


def test_imported_settings_are_checked_value_by_value():
    from kritzel import transfer
    def parse(settings, words=()): return transfer.parse(__import__("json").dumps(
        {"format": transfer.FORMAT, "version": transfer.VERSION, "settings": settings, "words": list(words)}))
    values, words = parse({"height": "99999", "show": "sometimes", "delay_ms": "abc", "pen_width": "nan", "ink_color": "red",
                           "model": "/tmp/evil.onnx", "threads": "64", "no_such_setting": "1", "keys": 5,
                           "symbols": "a b\n[recognition]\nmodel = /tmp/evil.onnx", "languages": "de_DE, ../../etc/passwd, en_US",
                           "symbol_keys": "x" * 5000},
                          ["Kritzel", "two\nlines", 7, None, "#comment", "w" * 65, "Kritzel", ["nested"]])
    assert "model" not in values and "threads" not in values and "no_such_setting" not in values and "keys" not in values
    assert values["height"] == "900" and values["show"] == "touch" and values["delay_ms"] == "800"
    assert values["pen_width"] == "2.6" and values["ink_color"] == "", "limits and known choices as in the settings file"
    assert "\n" not in values["symbols"] and values["languages"] == "de_DE, en_US" and len(values["symbol_keys"]) == transfer.MAX_VALUE_LENGTH
    assert words == ["Kritzel", "two lines"], "single lines, strings only, no duplicates"
    # what was imported can be saved without a value adding settings of its own
    d = tempfile.mkdtemp(); path = os.path.join(d, "config.ini"); save_config(values, path)
    assert Config(path).model == "small" and Config(path).symbols.startswith("a b [recognition]")
    assert len(parse({}, ["w%d" % i for i in range(transfer.MAX_WORDS + 50)])[1]) == transfer.MAX_WORDS
    for junk in ("", "{}", "[]", "null", '{"format": "other"}', "[" * 100000, '{"format": "kritzel-settings"}',
                 '{"format": "kritzel-settings", "version": 2}', '{"format": "kritzel-settings", "version": "1"}',
                 '{"format": "kritzel-settings", "version": 1, "settings": [], "words": []}',
                 '{"format": "kritzel-settings", "version": 1, "settings": {}, "words": "abc"}'):
        try: transfer.parse(junk); raise AssertionError(junk[:40])
        except transfer.NotASettingsFile: pass


def test_settings_file_for_another_computer():
    from kritzel import transfer
    d = tempfile.mkdtemp(); path = os.path.join(d, "mine.kritzel.json")
    transfer.save(path, {"height": "300", "model": "/home/me/private/model.onnx"}, ["Kritzel"])
    assert mode(path) == 0o600, "it holds the user's words"
    assert "private" not in open(path).read(), "paths of this computer do not travel"
    big = os.path.join(d, "big.json")
    with open(big, "w") as f: f.write(" " * (transfer.MAX_FILE_BYTES + 1) + open(path).read())
    for bad in (big, os.path.join(d, "missing"), d):
        try: transfer.load(bad); raise AssertionError(bad)
        except transfer.NotASettingsFile: pass
    with open(big, "wb") as f: f.write(b"\xff\xfe\x00")
    try: transfer.load(big); raise AssertionError("not text")
    except transfer.NotASettingsFile: pass


def test_password_fields_show_dots():
    from PyQt6.QtGui import QGuiApplication, QImage, QPainter
    from kritzel import ui
    from kritzel.config import Theme
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    cfg = Config(os.devnull); view = ui.View(Theme(), cfg)
    assert view.shown("hunter2", secret=True) == ui.HIDDEN * 7 and view.shown("hunter2", secret=False) == "hunter2"

    class State:
        strokes, candidates, selected, status, pending, pressed = [], ["hunter2", "hunterz"], 0, "", "secret", None
        hint, preview, language_label, secret = "", None, "EN", True

    drawn = []
    class Spy(QPainter):
        def drawText(self, *args): drawn.append(args[-1]); super().drawText(*args)
    def paint():
        drawn.clear(); image = QImage(800, 280, QImage.Format.Format_RGB32); p = Spy(image); view.paint(p, State); p.end()
        return " ".join(drawn)
    text = paint()
    assert "hunter" not in text and "secret" not in text and ui.HIDDEN * 7 in text, "neither the suggestions nor the preview"
    assert [chip.text for chip in view.chips] == State.candidates, "the chips still stand for the readings"
    State.secret = False; assert "hunter2" in paint() and "secret" in paint()
    State.secret = True; cfg.hide_passwords = False; view.configure(cfg); assert "hunter2" in paint(), "the user's choice"
    del app


def test_labels_show_text_as_it_is():
    from PyQt6.QtCore import Qt
    from PyQt6.QtWidgets import QApplication
    app = QApplication.instance() or QApplication(sys.argv[:1])
    from kritzel import settings
    for label in (settings.plain_label("<img src='/etc/hostname'><b>x</b>"), settings.note("<h1>big</h1>")):
        assert label.textFormat() == Qt.TextFormat.PlainText
    del app


if __name__ == "__main__":
    failed = 0
    for name, fn in sorted(globals().items()):
        if name.startswith("test_") and callable(fn):
            try:
                fn(); print("PASS", name)
            except Exception as e:  # noqa: BLE001
                failed += 1; print("FAIL", name, f"{type(e).__name__}: {e}")
    sys.exit(1 if failed else 0)

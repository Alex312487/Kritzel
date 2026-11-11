# How Kritzel is put together

Kritzel is one Python process that KWin starts as its "virtual keyboard".
It talks to KWin over Wayland, paints its panel with QPainter into shared
memory, and runs the handwriting model on a background thread. The settings
window is a second, ordinary Qt program (`kritzel settings`) that writes the
settings files and sends the panel a signal to read them again.

## The path of a word

```
 pen / finger / mouse
        │  Wayland events
        ▼
 devices.InputDevices ── press / motion / release ──▶ panel.Panel
                                                        │ strokes
                                                        ▼
                                   gestures.classify   a command? then it is carried out, not read
                                                        │ ink
                                                        ▼
                                              reader.InkReader   waits for the pause, reads ahead
                                                        │
                                                        ▼
                                              ink.render_line  (strokes → 48 px line image)
                                                        │
                                                        ▼
                                        engine.RecognitionWorker  (thread)
                                              recognizer.Recognizer (ONNX model, CTC beam search)
                                                        │ candidates
                                                        ▼
                                                   panel.Panel ──▶ compose (spaces, capitals)
                                                        │              ──▶ textfield.TextField ──▶ KWin ──▶ application
                                                        │
                                                        ▼
                                          ui.View  ──▶ surface.PanelSurface ──▶ KWin (pixels)
```

## Modules

| File | Responsibility | Knows about Wayland? |
|---|---|---|
| `__main__.py` | command line (`run`, `try`, `enable`, `status`, …), logging | no |
| `panel.py` | **the conductor**: gestures, what each key does, suggestions, when the panel is visible | barely (activate/deactivate) |
| `keys.py` | the list of keys: order, what each does, which can be switched off | no |
| `layout.py` | where things go: rows of keys, symbol grid; plain arithmetic | no |
| `ui.py` | the three pages (writing, symbols, languages) and all painting | no |
| `compose.py` | from a reading to the typed text: automatic spaces, capitals; pure functions | no |
| `languages.py` | the language in use, the known alphabets | no |
| `gestures.py` | strokes that are commands: scratch-out, split, delete word, space, enter; pure geometry | no |
| `reader.py` | the ink that is waiting: the pause, reading ahead, dropping answers for ink that has changed | no |
| `pace.py` | the pause fitted to how fast the user writes | no |
| `history.py` | the last insertions, to swap them or walk back through them | no |
| `learning.py` | words learnt from the suggestions the user picks | no |
| `ink.py` | strokes, smoothing, turning strokes into the model's input image | no |
| `recognizer.py` | ONNX model, CTC beam search, re-ranking of the candidates | no |
| `lexicon.py` | spell-check dictionaries and the personal word list | no |
| `engine.py` | background thread around the recogniser; applies new settings to it | no |
| `textfield.py` | the focused text field: commit text, single keys, replace a word, what kind of field it is | yes (input-method context) |
| `fakekeys.py` | key combinations (copy, cut, paste) through KWin's fake-input interface | yes |
| `devices.py` | mouse, touch, pen (incl. eraser, palm rejection) → one press/motion/release vocabulary | yes |
| `surface.py` | the window in its three forms (docked, floating, try-out), scaling, buffers, dragging | yes |
| `compositor.py` | connection, global interfaces, screens | yes |
| `wayland.py` | the Wayland wire protocol in pure Python, driven by the XML files in `protocols/` | is it |
| `eventloop.py` | timers, file descriptors, calls from other threads | no |
| `kwin.py` | D-Bus calls to KWin's virtual-keyboard interface (via `busctl`) | no |
| `switch.py` | selecting which virtual keyboard KWin runs (`kwinrc`) | no |
| `config.py` | the table of all settings (`OPTIONS`), settings file, personal words, remembered state, KDE colours and font | no |
| `transfer.py` | settings and personal words as one file for another computer; checks what it reads | no |
| `settings.py` | the settings window (Qt Widgets, own process), one class per page | no |
| `control.py` | finding and signalling the running panel from another process (pid file) | no |

Dependencies point downwards only: `panel` uses everything; nothing uses
`panel` except `__main__`. `keys`, `layout`, `compose`, `languages`, `ui`,
`ink`, `recognizer`, `lexicon`, `textfield` (with a fake context) and
`eventloop` can be exercised without a compositor, which is what
`tests/test_units.py` does. It lays out all 32,768 combinations of keys.
`tests/test_features.py` does the same for `gestures`, `reader` (with a fake
recogniser and timers that go off on command), `pace`, `history`, `learning`
and `transfer`.

## Things that are not obvious

**Why a hand-written Wayland client?** Qt cannot create an input-panel
surface from Python, and the pywayland version packaged by Fedora cannot
receive events that create objects (such as "a text field became active").
The protocol is small; `wayland.py` reads the same XML files a code generator
would.

**Keys are sent as key symbols** (`keysym`), not key codes. KWin ignores
key codes and modifiers from an input method that has not grabbed the
keyboard, and a key symbol cannot carry Ctrl or Shift. That is why copy, cut
and paste take another road (`fakekeys.py`): KWin's fake-input interface,
which newer KWin versions open to their input method. The combinations are
Ctrl+Insert, Shift+Delete and Shift+Insert because Ctrl+C would need the
keyboard layout (where is "c"?) and would interrupt a program in a terminal.

**Spaces go after words** (`compose.py`). The panel remembers that the space
before the cursor is its own (`Insertion.trailing_space`); a comma written
next replaces that space instead of following it. A swap of suggestions
replaces the whole span including such a space, because the other reading
may be the one that starts with a comma.

**The keys lay themselves out** (`layout.arrange`). The enabled keys, in the
order of `keys.KEYS`, are cut into at most four rows; of all ways to cut,
the one with the narrowest widest row wins (more room for writing), then the
one that keeps groups together. Every row fills the width of the block, so
no combination leaves holes.

**Pages.** The area below the suggestions shows the writing area, the symbol
keyboard or the list of languages (`View.page`). The panel and its keys are
identical on all three; only that area changes, and on the other two pages
the writing area is an empty rectangle, so nothing can draw.

**Replacing a word** (`TextField.replace`). Applications handle key events
*later* than input-method text (Qt queues keys). "Five Backspaces, then the
new word" therefore arrives as "new word, then five Backspaces". If the
application reports its text, delete and insert both go through the
text-input channel, which preserves order. Otherwise Backspace keys are sent
and the new text is held back for 150 ms.

**Docked vs. floating.** A docked panel is a `zwp_input_panel_v1` surface;
KWin positions it and decides whether it is displayed (only after pen/touch
input). A floating panel is a layer-shell surface positioned by its margins.
KWin does not manage it, so `Panel` asks KWin (`willShowOnActive`) whether a
keyboard would be shown, and `kritzel show` signals the process directly.

**The docked surface lives only while the panel is shown**
(`PanelSurface._create_dock`). KWin decides "was the text field tapped with
finger or pen?" when it first sees a panel surface, and from then on keeps
displaying that surface for every text field, including ones reached with
mouse or keyboard. Keeping one surface for the whole session (as versions up
to 1.2 did) therefore made the panel pop up for mouse clicks after the first
tap. A new surface per showing gives KWin a fresh decision each time.
`kritzel show` and the "always" setting want the panel regardless of that
decision; where KWin does not go along (`Panel._insist_on_being_displayed`
checks), the panel floats instead.

**Gestures are far from writing, on purpose** (`gestures.py`). A stroke is
judged once, when the pen is lifted, and a stroke taken for a command is
gone from the writing, so a false alarm costs the user a letter. Each shape
is therefore one that writing does not produce: a straight horizontal line
at least one and a half times as long as the writing is tall (a dash is a
third of that), a straight line over three quarters of the writing area's
height (an "l" is half of that), a zigzag whose passes each cross a third
of its width, four times, over ink that is already there. The lines only
count in an empty writing area, where no stroke can be the bar of a "T".
The thresholds are multiples of `LineScale.height`, the height the user's
writing has turned out to have. `tests/test_features.py` holds every
gesture against the strokes of the bundled writing sample; none may match.

**Reading ahead** (`reader.py`). The ink is read 0.25 s after every stroke,
not only when the pause is over. The reading is shown in grey
(`InkReader.pending`), and if nothing more is written it is typed the moment
the pause ends, without waiting for the recogniser. Answers arrive on the
loop thread some time after the question; every change of the ink bumps
`generation`, and an answer for another generation is dropped. Only one
question is under way at a time, because the worker thread skips all but
the newest.

**What the text field tells the recogniser** (`recognizer.Hint`, built by
`Panel._hint`). A number field narrows the output to digits and a few marks
(`textfield.NUMERIC_PURPOSES`), by a mask like the one for alphabets.
Letters right before the cursor are a word that the writing may continue:
`Lexicon.bonus` and `alternatives` judge "hel" + "lo", not "lo". In the
middle of a sentence, `Lexicon.mid_sentence_form` adds the reading with a
small first letter – only for capitals that are their small letters drawn
bigger, only if the small word is in the dictionary and the capital one is
not, and not in German, where a capital says nothing about the place in the
sentence.

**Walking back** (`history.py`). The panel keeps its last ten insertions.
The word key (or the gesture) deletes the newest and makes the one before
it current again, suggestions included. An insertion describes the end of
the text as it was when it was typed; if a later one took its automatic
space (a comma), `History.undo` corrects the description. Anything the
panel cannot follow – a key, a paste, the user's own editing – empties the
history, as it emptied the single remembered insertion before.

**Settings are applied live.** The settings window writes `config.ini` and
`words.txt`, then sends SIGHUP; `Panel.reload_settings` re-reads both. The
recogniser thread gets the new setup as a job in its queue and only reloads
the model if the model settings changed.

**Languages.** The model reads all languages at once. The language in use
narrows it in two ways: its dictionary re-ranks and corrects the candidates
(`Lexicon`), and letters beyond a-z that it does not use are masked out of
the model's output (`Recognizer.set_letters`). Switching is a job in the
recogniser thread's queue (`RecognitionWorker.configure`) and instant once
a dictionary file has been read.

**Dragging** (`PanelSurface.drag`). KWin reports touch positions relative to
where the surface was at touch-down, but pen and pointer positions relative
to where it is now. The two cases need different arithmetic. Touch positions
are coalesced to one move per 8 ms.

**The model is loaded on the first stroke**, not at login, to keep an idle
session light.

**One frame, one buffer.** `PanelSurface` sends a buffer only after the
previous frame callback, so fast pen input cannot flood the compositor;
single stroke segments are painted straight into the image
(`draw_directly`) instead of repainting the panel.

## Keeping things to itself

The panel sees everything the user writes, so a few rules hold throughout.
`tests/test_hardening.py` checks them.

**Files.** Whatever Kritzel writes goes through `config.write_atomically`:
a temporary file with an unpredictable name and mode 0600 in the same
directory, moved into place. Directories it creates and the log file are
for the user alone (`config.ensure_private_dir`, `PrivateLogFile` in
`__main__.py`). `save_config` and `save_words` turn line breaks in values
into spaces: a value must not be able to add settings of its own.

**Nothing written by the user is logged**, not even with `-v`: the recogniser
logs how many readings it found, not what they say. The try-out window
hands its text to `wl-copy` through a pipe, not on the command line.

**What is kept of the user's writing is little, and theirs alone.** The
only text Kritzel stores by itself are learnt words (`learning.py`): single
words made of letters, at most 400, in a file with mode 0600. They are only
ever taken from a suggestion the user picked, and never in a field that
holds no prose – password, address, number – nor in a terminal
(`TextField.prose`). The fitted pause is stored as one number; the breaks it
was computed from are not.

**Password fields.** Where the application marks a field as a password or
as hidden text (`TextField.secret`), the suggestions and the grey preview
are painted as dots (`View.shown`), unless the user switches that off. The
ink itself stays visible while it is written, like the keys of an on-screen
keyboard that light up.

**Files from elsewhere.** An imported settings file (`transfer.py`) is
somebody else's data. It has a size limit; only known settings are taken,
each through `Option.parse` like a line of the settings file; values and
words are single lines of limited length; and `config.MACHINE_SETTINGS` –
the model, which is a file the program loads – are never taken, nor
exported. The state files (`state.json`, `learned.json`) are read with the
same distrust: `LearnedWords.load` keeps only words with a sane count.

**Settings that become file names** are checked where they are read and
again where they are used: language codes (`config.is_language_code`,
`lexicon.find_dictionary`), the name of the colour scheme. Relative paths in
`XDG_*` variables are ignored, as the specification demands
(`config.xdg_dir`; `install.sh` and `uninstall.sh` do the same), and the
compositor's socket is only looked for in `XDG_RUNTIME_DIR`.

**The virtual keyboard is a program KWin runs.** `switch.kwrite` therefore
only ever writes the absolute path of an existing `*.desktop` file into
kwinrc (`switch.is_desktop_file`), also when the path comes from the file
that remembers the keyboard used before.

**Signals.** `kritzel show` and `kritzel reload` signal the process named
in the pid file. Such a file can be stale and its number given to another
program, which SIGHUP would end; `control._is_kritzel` checks that the
process belongs to the user and was started as `kritzel`. The panel
installs its handlers before the pid file is written.

**The compositor is trusted, but not blindly.** `wayland.Connection` drops
events whose length fields do not fit the message, closes file descriptors
that arrive with events nobody handles, and marks received descriptors
close-on-exec. The installer writes paths into the launcher and the
desktop files as quoted words.

## Tests

`tests/run_all.sh` runs everything; its header explains the three layers
(unit tests, scripted compositor, real headless KWin) and their limits. The
scripted compositor (`tests/mockcomp.py`) exists because a headless KWin
cannot inject pen input, and because it lets a test check the exact requests
Kritzel sends.

## Adding things

* A key: a line in `keys.KEYS` (and in `keys.OPTIONS` if it can be switched
  off), an icon in `ui.ICONS` unless it has a text label. A key
  that presses a key symbol or types text needs nothing else; a new command
  gets an entry in `Panel._commands`. Layout and settings window pick it up.
* A symbol key: nothing to code; `symbol_keys` and `symbols` are settings.
* A language: a line in `languages.KNOWN`.
* A gesture: a function in `gestures.py` that returns a `Gesture`, called
  from `classify`; what it does goes into `Panel._do_gesture` (a key it
  stands for: `GESTURE_KEYS`). Add the shapes of writing it must not match
  to `tests/test_features.py`.
* A kind of text field: `textfield.NUMERIC_PURPOSES` for one that only
  takes certain characters.
* A setting: a line in `config.OPTIONS`, a commented line in
  `data/config.ini` (a unit test compares the two), one line on the fitting
  page in `settings.py` (`self.check(...)`, `self.number(...)`,
  `self.choice(...)`), and the code that uses `cfg.<name>`.
* Another recogniser: anything with `recognize(image) -> [(text, confidence)]`
  can replace `recognizer.Recognizer` in `engine.py`.

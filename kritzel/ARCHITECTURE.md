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
| `settings.py` | the settings window (Qt Widgets, own process), one class per page | no |
| `control.py` | finding and signalling the running panel from another process | no |

Dependencies point downwards only: `panel` uses everything; nothing uses
`panel` except `__main__`. `keys`, `layout`, `compose`, `languages`, `ui`,
`ink`, `recognizer`, `lexicon`, `textfield` (with a fake context) and
`eventloop` can be exercised without a compositor, which is what
`tests/test_units.py` does. It lays out all 32,768 combinations of keys.

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

## Tests

`tests/run_all.sh` runs everything; its header explains the three layers
(unit tests, scripted compositor, real headless KWin) and their limits. The
scripted compositor (`tests/mockcomp.py`) exists because a headless KWin
cannot inject pen input, and because it lets a test check the exact requests
Kritzel sends.

## Adding things

* A key: a line in `keys.KEYS` (and in `keys.OPTIONS` if it can be switched
  off), an icon in `ui.View._paint_button` unless it has a text label. A key
  that presses a key symbol or types text needs nothing else; a new command
  gets an entry in `Panel._commands`. Layout and settings window pick it up.
* A symbol key: nothing to code; `symbol_keys` and `symbols` are settings.
* A language: a line in `languages.KNOWN`.
* A setting: a line in `config.OPTIONS`, a commented line in
  `data/config.ini` (a unit test compares the two), one line on the fitting
  page in `settings.py` (`self.check(...)`, `self.number(...)`,
  `self.choice(...)`), and the code that uses `cfg.<name>`.
* Another recogniser: anything with `recognize(image) -> [(text, confidence)]`
  can replace `recognizer.Recognizer` in `engine.py`.

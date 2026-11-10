# How Kritzel is put together

Kritzel is one Python process that KWin starts as its "virtual keyboard".
It talks to KWin over Wayland, paints its panel with QPainter into shared
memory, and runs the handwriting model on a background thread.

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
                                                   panel.Panel ──▶ textfield.TextField ──▶ KWin ──▶ application
                                                        │
                                                        ▼
                                          ui.View  ──▶ surface.PanelSurface ──▶ KWin (pixels)
```

## Modules

| File | Responsibility | Knows about Wayland? |
|---|---|---|
| `__main__.py` | command line (`run`, `try`, `enable`, `status`, …), logging | no |
| `panel.py` | **the conductor**: gestures, buttons, suggestions, when the panel is visible | barely (activate/deactivate) |
| `ui.py` | layout of bar, canvas and buttons; all painting | no |
| `ink.py` | strokes, smoothing, turning strokes into the model's input image | no |
| `recognizer.py` | ONNX model, CTC beam search, dictionary re-ranking | no |
| `engine.py` | background thread around the recogniser | no |
| `textfield.py` | the focused text field: commit text, keys, replace a word, auto-space rules | yes (input-method context) |
| `devices.py` | mouse, touch, pen (incl. eraser, palm rejection) → one press/motion/release vocabulary | yes |
| `surface.py` | the window in its three forms (docked, floating, try-out), scaling, buffers, dragging | yes |
| `compositor.py` | connection, global interfaces, screens | yes |
| `wayland.py` | the Wayland wire protocol in pure Python, driven by the XML files in `protocols/` | is it |
| `eventloop.py` | timers, file descriptors, calls from other threads | no |
| `kwin.py` | D-Bus calls to KWin's virtual-keyboard interface (via `busctl`) | no |
| `switch.py` | selecting which virtual keyboard KWin runs (`kwinrc`) | no |
| `config.py` | settings file, KDE colours and font | no |

Dependencies point downwards only: `panel` uses everything; nothing uses
`panel` except `__main__`. `ui`, `ink`, `recognizer`, `textfield` (with a
fake context) and `eventloop` can be exercised without a compositor, which is
what `tests/test_units.py` does.

## Things that are not obvious

**Why a hand-written Wayland client?** Qt cannot create an input-panel
surface from Python, and the pywayland version packaged by Fedora cannot
receive events that create objects (such as "a text field became active").
The protocol is small; `wayland.py` reads the same XML files a code generator
would.

**Keys are sent as key symbols** (`keysym`), not key codes. KWin 6.8 ignores
key codes from an input method that has not grabbed the keyboard.

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

* A button: add it to `BUTTONS` (and an icon) in `ui.py`, handle its name in
  `Panel._do_button`.
* A setting: default and parsing in `config.py`, a commented line in
  `data/config.ini`.
* Another recogniser: anything with `recognize(image) -> [(text, confidence)]`
  can replace `recognizer.Recognizer` in `engine.py`.

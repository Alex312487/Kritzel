# Kritzel – handwriting input panel for KDE Plasma (Wayland)

Write with a pen (or finger, or mouse) in a panel at the bottom of the screen;
the handwriting is recognised **offline** and typed into whatever text field
has focus – like the handwriting panel of the Windows touch keyboard.

```
┌──────────────────────────────────────────────────────────┬───────────┐
│ [Guten Morgen] [Guten Morgan]              suggestions   │  ⌫     ↵  │
│ ┌──────────────────────────────────────────────────────┐ │   space   │
│ │   write here                                         │ │ word keys │
│ └──────────────────────────────────────────────────────┘ │ float hide│
└──────────────────────────────────────────────────────────┴───────────┘
```

## How it fits into Plasma

Kritzel is a regular Plasma **virtual keyboard**, the same kind of program as
Plasma Keyboard: KWin starts it, docks its panel at the bottom of the screen,
shows it when you tap a text field with pen or finger, and delivers the text
through the Wayland input-method protocol. So it

* appears in *System Settings → Keyboard → Virtual Keyboard*,
* works in Qt, GTK, Electron/Chromium, Firefox and XWayland applications,
* never steals keyboard focus, follows your colour scheme, font and
  display scaling,
* uses only Fedora packages: `python3-pyqt6`, `python3-numpy`,
  `python3-onnxruntime`. No daemon, no root service, no pip.

Plasma has one virtual-keyboard slot. While Kritzel is selected, Plasma
Keyboard (or IBus/Fcitx, if you use them for e.g. Chinese or Japanese) is
not active; `kritzel disable` switches back. Requires a Plasma 6 **Wayland**
session.

## Install / update

```sh
tar xf kritzel-1.2.tar.gz && cd kritzel
./install.sh
```

The script asks before installing missing packages with `sudo dnf`, copies
everything to `~/.local/share/kritzel`, runs a self-test and asks whether to
make Kritzel the virtual keyboard. Running it again over an older version
updates it and restarts the panel; your settings are kept. If the panel does
not appear right after enabling, tap into another window once.

## Remove

```sh
~/.local/share/kritzel/uninstall.sh          # or ./uninstall.sh --purge
```

This restores the virtual keyboard that was selected before, deletes all
files, and tells you which dnf packages the installer had added.

## Use

| | |
|---|---|
| Tap a text field with the pen | panel appears |
| Write, then pause (0.8 s) | text is recognised and typed |
| Tap another suggestion | replaces what was just typed. The suggestions disappear once you edit the text yourself |
| ⌫ ↵ space | Backspace, Enter, Space (ink that is still waiting is typed first) |
| **word** | removes what was just inserted; pressed again, the word before the cursor. Ink that is not typed yet is thrown away instead |
| **keys** | switch to the normal on-screen keyboard |
| **float** / **dock** | detach the panel and drag it by the dotted grip at its top left; **dock** puts it back at the bottom |
| **hide** | hide the panel until you tap a text field again |
| Pen eraser end, or pen button held | rub out strokes |

While the pen hovers over the panel, your palm cannot draw.

Getting back from the normal keyboard to handwriting: run `kritzel switch`, or
start **Handwriting / Keyboard** from the application launcher (it can be
pinned to the task bar or given a shortcut). It toggles between the two.

Floating: the panel starts docked after each login. While it floats, windows
keep their full height. It appears for text fields you tap with pen or
finger, like the docked panel.

Commands:

```
kritzel try        try the recognition in a normal window (text goes to the clipboard)
kritzel switch     toggle handwriting <-> normal on-screen keyboard
kritzel enable     make Kritzel the virtual keyboard
kritzel keyboard   switch to the normal on-screen keyboard
kritzel disable    restore exactly the setting from before Kritzel
kritzel restart    restart the panel (after changing settings)
kritzel status     configuration + self-test
kritzel show       show the panel now – useful as a keyboard shortcut or pen-button action
```

With a mouse and keyboard only, Plasma does not pop up virtual keyboards by
itself; use `kritzel show`.

## Settings

`~/.config/kritzel/config.ini` (commented). The ones that matter:

* `delay_ms` – pause before recognition. Raise it if words get split.
* `auto_insert = false` – nothing is typed until you tap a suggestion.
* `languages = de_DE, en_US` – hunspell dictionaries used to decide between
  look-alike readings. Install more with e.g. `sudo dnf install hunspell-de`.
* `scripts` – `western` (default), `latin` (Polish, Czech, Turkish …), `all`.

* `replace = keys` – if tapping a suggestion misbehaves in some application
  (see the comment in the file).
* `max_width` – width of the docked panel; 0 (default) fills the screen.
  `float_width` – width while floating.

Apply changes with `kritzel restart`.

## Recognition quality

The recogniser is PP-OCRv6 "small" (PaddleOCR, Apache-2.0, 20 MB, bundled),
one model for English, German and about 45 other Latin-script languages. It
runs on the CPU in roughly 50–100 ms per line and is loaded on the first
stroke.

It is an image-based text-line model, not a stroke-based one like the
Windows recogniser. Clear print and tidy cursive work well; very personal
or hasty cursive will make more mistakes than Windows does. Things that help:
write on one line, not too small, and leave visible gaps between words.

A larger PP-OCRv6 "medium" recognition model exists. If you download its ONNX
file you can point `model = /path/to/file.onnx` at it; this has not been
tested.

## Troubleshooting

* `kritzel status` – checks packages, model and the active virtual keyboard.
* Log: `~/.local/state/kritzel/kritzel.log` (start-up errors also land in
  `journalctl --user -b | grep -i kritzel`).
* Panel never shows: *System Settings → Keyboard → Virtual Keyboard* must list
  Kritzel as selected, and the session must be Wayland.

## Development

`ARCHITECTURE.md` explains how the code is organised and why some things are
done the way they are. `tests/run_all.sh` runs the unit tests, the tests
against a scripted compositor and, if `kwin_wayland` is available, the tests
inside a headless KWin (needs `pip install Hershey-Fonts`, and pywayland ≥
0.4.19 for the KWin test).

## Licences

Kritzel: MIT (see LICENSE). Bundled: PP-OCRv6 recognition model (Apache-2.0,
PaddlePaddle; ONNX conversion from the RapidOCR project) and Wayland protocol
descriptions (MIT-style, see the headers of the files in `protocols/`).

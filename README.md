# Kritzel – handwriting input panel for KDE Plasma (Wayland)

Write with a pen (or finger, or mouse) in a panel at the bottom of the screen;
the handwriting is recognised **offline** and typed into whatever text field
has focus – like the handwriting panel of the Windows touch keyboard.

```
┌──────────────────────────────────────────────┬────────────────────────────────────┐
│ [Guten Morgen] [Guten Morgan]    suggestions │     ⌫          word          ↵     │
│ ┌──────────────────────────────────────────┐ │    ←       ↑       ↓       →       │
│ │                                          │ │  copy   paste   [ space ]    DE    │
│ │   write here                             │ │ symbols  keys  float settings hide │
│ └──────────────────────────────────────────┘ │                                    │
└──────────────────────────────────────────────┴────────────────────────────────────┘
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
tar xf kritzel-1.5.tar.gz && cd kritzel
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
| Tap a text field with the pen or a finger | panel appears. A text field you click with the mouse or reach with the keyboard does not bring it up |
| Write, then pause (0.8 s) | text is recognised and typed, followed by a space. What was read appears in grey at the right of the suggestions as soon as you lift the pen |
| Write `,` `.` `?` … next | closes up to the word: "hello " becomes "hello, " |
| Tap another suggestion | replaces what was just typed. The suggestions disappear once you edit the text yourself. A word you pick twice is preferred from then on |
| ⌫ ↵ space | Backspace, Enter, Space (ink that is still waiting is typed first) |
| ← ↑ ↓ → | cursor keys; like ⌫ they repeat while held |
| **copy** / **paste** | copy what is selected in the application, paste the clipboard |
| Language key (**DE**) | shows your languages; tap the one you are writing now |
| **symbols** | swaps the writing area for a keyboard of digits and symbols; the same key (now **write**) brings the writing area back |
| **word** | removes what was just inserted; pressed again, the word before the cursor. Ink that is not typed yet is thrown away instead. While it walks back through what you wrote, the suggestions of the word that is now the last come back |
| **keys** | switch to the normal on-screen keyboard |
| **float** / **dock** | detach the panel and drag it by the dotted grip at its top left; **dock** puts it back at the bottom |
| **settings** | opens the settings window |
| **hide** | hide the panel until you tap a text field again |
| Pen eraser end, or pen button held | rub out strokes |
| Zigzag across writing that is not typed yet | rubs it out |
| Line from the top of the writing area to its bottom, between two words that ran together | opens a gap there, so that they are read as two words |
| Long line to the left, in the empty writing area | deletes the last word, like **word** |
| Long line to the right, in the empty writing area | Space |
| Down, then a long line to the left (the shape on the Enter key), in the empty writing area | Enter |

While the pen hovers over the panel, your palm cannot draw.

**Gestures.** The last five rows are strokes that are commands. They are
shaped so that writing cannot be mistaken for them: the lines are longer than
any dash (about one and a half times the height of your writing), the
splitting line is taller than any letter, and the zigzag has to cross ink
that is already there at least four times. They can be switched off in
Settings → Pen and ink.

**Spaces.** A space is typed after every word, never before one. To repair
the end of a word, delete the space and the wrong letters and write the right
ones: they join the word. Address, number and password fields get no
automatic spaces. Settings → Recognition has the alternatives (space before
words as up to version 1.3, or none).

**Languages.** The recogniser reads all languages at once. Picking a
language narrows it down: only that language's spell-check dictionary judges
and corrects the readings (write "helle" in English and "hello" is among the
suggestions; in German it is "Halle" and "Hölle"), and only its letters are
recognised. Which languages the key offers is set in Settings → Languages.
A dictionary is a hunspell package, e.g. `sudo dnf install hunspell-de`;
without one a language still limits the letters.

**Number and password fields.** Where an application says that a field
takes a number, a phone number, a date or a time, only digits and the marks
that go with them are recognised: an "l" cannot be read where a "1" must be
meant. The writing area then says "Write digits". In password fields the
suggestions show dots instead of what was read, and nothing written there
is ever learnt. (Many desktop applications do not describe their fields;
web pages in Firefox and Chromium usually do.)

**The text before the cursor** helps to read: letters written right after
a word are read as the rest of that word (delete "lo" from "hello", write
it again, and "lo" is judged as the end of "hello"), and in the middle of a
sentence "some" is preferred to "Some" – for the letters whose capital only
differs in size (c, k, o, p, s, u, v, w, x, z), outside German.

**Learning.** When Kritzel types one reading and you pick another, it notes
the words you chose. A word picked twice counts like a personal word from
then on. A pick you take back teaches nothing. Settings → Personal words
lists what was learnt; words can be forgotten there or made personal words,
and learning can be switched off.

**Keys.** Every key except Backspace and Settings can be switched off in
Settings → Keys, and there are more to switch on: Cut, Home/End, Delete,
Tab. The keys arrange themselves in up to four rows so that the writing area
stays as wide as possible; they can sit on the left for left-handers.

Copy, cut and paste are sent as Ctrl+Insert, Shift+Delete and Shift+Insert,
which mean the same on every keyboard layout and also work in terminals.
They need a KWin that lets its on-screen keyboard press key combinations
(the panel says so if yours does not).

Getting back from the normal keyboard to handwriting: run `kritzel switch`, or
start **Handwriting / Keyboard** from the application launcher (it can be
pinned to the task bar or given a shortcut). It toggles between the two.

Floating: while the panel floats, windows keep their full height. It appears
for text fields you tap with pen or finger, like the docked panel. It starts
docked after each login unless you tick "float again" in Settings → Panel.

Commands:

```
kritzel try        try the recognition in a normal window (text goes to the clipboard)
kritzel switch     toggle handwriting <-> normal on-screen keyboard
kritzel enable     make Kritzel the virtual keyboard
kritzel keyboard   switch to the normal on-screen keyboard
kritzel disable    restore exactly the setting from before Kritzel
kritzel settings   open the settings window
kritzel reload     apply a settings file you edited by hand
kritzel restart    restart the panel
kritzel status     configuration + self-test
kritzel show       show the panel now – useful as a keyboard shortcut or pen-button action
```

With a mouse and keyboard only, Plasma does not pop up virtual keyboards by
itself; use `kritzel show`. Some KWin versions refuse to display a docked
keyboard after mouse or keyboard input even then; the panel then comes up
floating (the **dock** key, tapped with pen or finger, puts it back).

## Settings

The **settings** key on the panel (or `kritzel settings`, or *Kritzel
Settings* in the application launcher) opens a window with seven pages.
Apply takes effect at once, without restarting the panel.

* **Panel** – when the panel appears, its height and widths, whether it
  floats again after a restart, how many suggestions it offers, dots instead
  of readings in password fields.
* **Keys** – which keys are on the panel (with a picture of the result),
  their side and size, key repeat, the symbol keyboard and the optional thin
  symbol row with their keys.
* **Pen and ink** – ink thickness and colour, the guide line, whether
  fingers may write, palm rejection, gestures.
* **Recognition** – pause before recognition (raise it if words get split,
  or let Kritzel fit it to your writing), the grey preview, which characters
  may be recognised, digits only in number fields, help from the text before
  the cursor, typing right away or on tap, automatic spaces, capital letters
  at the start of a sentence.
* **Languages** – the languages you write in, what the language key does,
  how strongly the dictionary counts and whether it suggests or applies
  corrections.
* **Personal words** – names and terms that are in no dictionary. They are
  preferred when the writing could be read that way, and offered when the
  reading is one letter off. Below them: the words learnt from your picks.
* **Advanced** – switch between handwriting and the normal on-screen
  keyboard, how a suggestion replaces a word (try "Backspace keys" if
  replacing misbehaves in some application), another recognition model,
  export and import.

**Fitting the pause** (Recognition → "Fit the pause to my writing", off
unless you tick it). Kritzel measures the breaks you make between strokes
and waits two and a half times as long as nearly all of them, within 0.6 to
2.5 times the pause you set. Slow writers get fewer split words, fast
writers get their text sooner.

**Another computer.** Advanced → Export writes the settings and your
personal words into one file (`*.kritzel.json`); Import on the other
computer shows them in the settings window, and Apply uses them. An imported
file is checked value by value. The recognition model and the number of
processor threads are not carried over: they belong to a computer.

Settings are stored in `~/.config/kritzel/`: `config.ini` (can also be
edited by hand, then `kritzel reload`; `data/config.ini` in the installation
explains every line) and `words.txt` (one word per line). What Kritzel
remembers by itself is in `~/.local/state/kritzel/`: the language in use,
where the panel floated, the fitted pause, and `learned.json` with the learnt
words. All of these files are readable by you alone.

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
file you can select it under *Settings → Advanced*; this has not been
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

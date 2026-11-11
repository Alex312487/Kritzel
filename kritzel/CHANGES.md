# Changes

## 1.5 – gestures, learning, a faster and better-informed reader

### New

* **Gestures.** A zigzag across writing that is not typed yet rubs it out.
  A line from the top of the writing area to its bottom opens a gap between
  two words that ran together. In the empty writing area a long line to the
  left deletes the last word, a long line to the right is a space, and
  down-then-left is Enter. *Settings → Pen and ink → Gestures.*
* **Walking back.** The word key (and the gesture) used to know only the
  last thing typed. Now the last ten insertions are remembered: when one is
  deleted, the suggestions of the one before it return, so an earlier word
  can still be swapped for another reading.
* **The reading is shown before it is typed.** A quarter of a second after
  the pen is lifted the ink is read, and the result appears in grey at the
  right of the suggestions. When the pause is over it is typed at once
  instead of only then being sent to the recogniser.
  *Settings → Recognition → Show what was read while the pause still runs.*
* **A pause that fits the writer** (off unless switched on). The panel
  measures the breaks between strokes and waits 2.5 times as long as nearly
  all of them, within 0.6 to 2.5 times the configured pause.
  *Settings → Recognition → Fit the pause to my writing.*
* **Number fields.** In fields that an application marks as number, phone
  number, date or time, only digits and the marks that go with them are
  recognised. *Settings → Recognition.*
* **The text before the cursor helps.** Letters written right after a word
  are judged as the rest of that word by the dictionary. In the middle of a
  sentence a small first letter is preferred where the capital looks the
  same (c, k, o, p, s, u, v, w, x, z; not in German).
  *Settings → Recognition.*
* **Learning from corrections.** A word picked twice from the suggestions,
  in place of what Kritzel had typed, counts like a personal word. A pick
  that is taken back teaches nothing. The learnt words are listed under
  *Settings → Personal words*, where they can be forgotten or turned into
  personal words.
* **Export and import** of the settings and the personal words as one file.
  *Settings → Advanced → Another computer.*

### Security and privacy

| | |
|---|---|
| **Password fields** | The suggestions and the preview show dots instead of what was read (*Settings → Panel*; on unless switched off). Up to 1.4.1 the readings of a password stood on the panel in clear. |
| **Learnt words** | The only text Kritzel keeps by itself. Single words of letters, at most 400, mode 0600, removed by `uninstall.sh`. Never taken from password, address, number, date fields or terminals, and only from a suggestion the user picked. |
| **Imported settings** | Checked value by value through the parser of the settings file; size limits on the file, the values and the word list; unknown settings are dropped. The recognition model – a file the program loads – and the thread count are neither exported nor imported. |
| **Exported settings** | Written with mode 0600 like every file Kritzel writes: it holds the user's words. |
| **State files** | `learned.json` and the remembered pause are read with the same distrust as `state.json`: whatever is not a word with a count, or a time within limits, is ignored. |
| **Fields the panel knows** | Date and time fields now count as "no prose" (no automatic spaces). |

### Maintainability

* `panel.py` no longer keeps the ink's state itself. The pause, reading
  ahead and the dropping of outdated answers are `reader.InkReader`, which is
  tested with a fake recogniser and timers that go off on command. The panel
  lost its `generation`, `recognizing`, line-scale and timer bookkeeping.
* New modules with one job each, all free of Wayland and Qt widgets:
  `gestures.py`, `history.py`, `learning.py`, `pace.py`, `transfer.py`.
* `Config.from_values()` builds settings from `{key: text}`; the preview in
  the settings window and the import use it instead of setting attributes
  by hand.
* `TextField._describe()` is the one place that turns an application's
  description of a field into `plain`, `prose`, `secret` and `numeric`.
* `Lexicon.known()` is built on `_listed()` (exact spelling), which the new
  capital-letter rule needs as well.
* The panel's messages (`Panel.message`) and the "…" of a pending reading
  are separate things now; `status` is what the view shows of the two.
* `tests/test_features.py` (22 tests), four more in
  `tests/test_hardening.py`, 19 more checks against the scripted compositor.
  `ARCHITECTURE.md` describes the new parts and their rules.

### Considered and left out

* **Typing the left part of the ink while one still writes on the right.**
  It needs a reliable way to tell a gap between words from a gap between
  letters before the line is finished, and it moves ink under the pen. Done
  badly it is worse than waiting; it was not possible to tune it here
  without a pen. The preview and the fitted pause shorten the wait instead.
* **Word pairs** ("in *der* Stadt"). hunspell dictionaries carry no
  frequencies, and collecting them from what the user writes would mean
  storing their text. The two uses of context above need neither.
* **A German interface.** Worth doing only for the panel, the settings
  window and the command line together, which is a change of its own.
* **The medium model** is still untested: its file is not part of the
  package.

## 1.4.1 – security and maintenance

Nothing about using the panel changes. Recognition, layout and painting
give the same results as 1.4 (the pictures of the panel were compared pixel
by pixel in 1,728 configurations).

### Security

Most of these need another user on the same computer, an unusual home
directory or a file placed where Kritzel reads it. None can be reached over
the network.

| | 1.4 | 1.4.1 |
|---|---|---|
| **Installer** | The paths of the installation went into the launcher script unquoted: a home directory whose name contains `$(...)` or a backtick had that command run by `install.sh`, a quote broke the launcher, and `\|` or `&` broke the desktop files. | Paths are written as quoted words (`sh_quote`, `desktop_exec_arg`). A home directory with spaces now works. For ordinary paths the desktop files are byte for byte what they were. |
| **KWin test** | `tests/it_kwin.py` imported generated modules from the fixed directory `/tmp/itgen`. Whoever created that directory first decided what the test ran. | A private directory. |
| **Settings, words, state** | Written through `<name>.tmp`, following a symbolic link of that name, readable by everybody (0644). | An unpredictable temporary file, 0600, flushed, moved into place; cleaned up if writing fails. A settings file that is a symbolic link (dotfiles) stays one. |
| **Log** | 0644, and with `-v` it contained the text of every recognition – passwords included. | 0600 also after rotation, directory 0700, and the text is never logged (only how many readings and how long). |
| **Try-out window** | Passed the text to `wl-copy` on its command line, where `ps` shows it to every user. | Through a pipe. |
| **pid file** | Any process whose command line contained "kritzel" (an editor with `kritzel.log` open) counted as the panel; `kritzel reload` then sent it SIGHUP, which ends most programs. The file was written through an existing symbolic link. | The process must belong to the user and be started as `kritzel`; settings window and panel are told apart. The file is created with `O_EXCL \| O_NOFOLLOW` and moved into place. |
| **Signals at start-up** | A `kritzel show` or `reload` between writing the pid file and installing the handlers ended the panel. | Handlers first. |
| **Virtual keyboard** | The remembered previous keyboard went from a state file into kwinrc – which names a program KWin runs – after a mere "is a file" check; a value starting with `-` was read by `kwriteconfig` as an option. | Only the absolute path of an existing `*.desktop` file is ever written. KDE tools are run with a time limit. |
| **Language codes** | Used as file names unchecked: `languages = ../../x` read a "dictionary" from anywhere. Same for the colour scheme name in kdeglobals. | Checked where read and where used. |
| **Settings file** | `save_config()` wrote values as they came; one with a line break added settings of its own (for instance another model file). The settings window could not produce such a value, other callers could. | Line breaks and control characters become spaces. |
| **XDG directories** | A relative `XDG_CONFIG_HOME` etc. was used as given, so settings, model path and state came from wherever the program was started. Without `XDG_RUNTIME_DIR` the compositor socket was looked for in the current directory. | Relative values are ignored (as the specification says), in the program and in both scripts. No runtime directory, no connection. `uninstall.sh` refuses to run without a home directory instead of aiming `rm -rf` at `/.local/...`. |
| **Wayland events** | A string longer than its message was silently cut; file descriptors of unhandled events were never closed; received descriptors were inheritable. | Such events are dropped and logged, the descriptors closed and close-on-exec. |
| **Settings window** | Labels showing paths and error messages let Qt guess whether the text is HTML. | Plain text. |

### Robustness

* `state.json` holding `Infinity`, a list where a string belongs or 100,000
  nested brackets no longer stops the panel from starting.
* A `.aff` file with a bare `SET` line no longer makes loading the
  dictionary fail; `SET<tab>UTF-8` is understood.
* `nan` in a number setting gives the default instead of the maximum.
* An application that sends "no string" as surrounding text is handled.
* A dictionary whose name contains a dash (`en_GB-large`) is found under
  its own name instead of being replaced by another one of the language.
* `kritzel disable` says why when this is not a Plasma session.
* `install.sh` says so when Python is older than 3.11 (the launcher has
  always needed it) and only counts modules that are really installed.

### Efficiency

* Painting: the fonts and the icons are built once instead of on every
  repaint.
* Nearest personal word: one distance computation per word instead of up to
  two, and the search stops at the first word that cannot be beaten.
* The table of commands is built once, not on every key press.
* `busctl` helpers are collected when they exit instead of lingering as
  zombies until the next one starts.

### Maintainability

* `__main__.py`: one table of commands instead of a list of names and a
  dictionary that had to agree; `kritzel disable` lives in
  `switch.restore_previous()` next to its relatives instead of repeating
  their file handling.
* `kwin.py`: one helper builds every `busctl` call (there were four copies).
* `ui.py`: the long `if`/`elif` chain of icon drawings is a table of
  small functions (`ICONS`); adding a key no longer means editing
  `_paint_button`.
* `wayland.py`: decoding has one place per argument type and checks
  lengths; delivering an event is its own method.
* `lexicon.py` / `languages.py`: "de_DE" → "de" is written once.
* `control.py`: names for the two pid files, `remove_pid()`.
* New `tests/test_hardening.py` (24 tests), run by `tests/run_all.sh`;
  `ARCHITECTURE.md` has a section on the rules above.
* The shell scripts pass `shellcheck`.

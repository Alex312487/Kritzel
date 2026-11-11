# Changes

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

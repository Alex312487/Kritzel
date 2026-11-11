#!/usr/bin/env bash
# Kritzel installer - everything goes into your home directory:
#
#   ~/.local/share/kritzel/                  program, model, protocol files
#   ~/.local/bin/kritzel                     launcher
#   ~/.local/share/applications/kritzel.desktop   makes it selectable as
#                                            Plasma's virtual keyboard
#   ~/.local/share/applications/kritzel-switch.desktop   launcher that toggles
#                                            handwriting / normal keyboard
#   ~/.local/share/applications/kritzel-settings.desktop   the settings window
#   ~/.config/kritzel/                       settings and personal words (kept on reinstall)
#
# The only system change is installing up to three Fedora packages if they
# are missing (asked first).  ./uninstall.sh reverts everything.
set -euo pipefail

SRC=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
case ${HOME:-} in /*) ;; *) echo "HOME is not set to an absolute path" >&2; exit 1 ;; esac

xdg_dir() {  # xdg_dir VALUE DEFAULT -> VALUE if it is an absolute path (the XDG specification demands one), else DEFAULT
    case $1 in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$2" ;; esac
}
DATA=$(xdg_dir "${XDG_DATA_HOME:-}" "$HOME/.local/share")
CONF=$(xdg_dir "${XDG_CONFIG_HOME:-}" "$HOME/.config")
STATE=$(xdg_dir "${XDG_STATE_HOME:-}" "$HOME/.local/state")
DEST=$DATA/kritzel
BIN=$HOME/.local/bin
PYTHON=${PYTHON:-/usr/bin/python3}
ASSUME_YES=0
ENABLE=ask
for arg in "$@"; do
    case $arg in
        -y|--yes) ASSUME_YES=1 ;;
        --no-enable) ENABLE=no ;;
        --enable) ENABLE=yes ;;
        -h|--help) sed -n '2,15p' "$0" | sed 's/^# \{0,1\}//'; echo; echo "Options: --yes (no questions), --enable / --no-enable"; exit 0 ;;
        *) echo "unknown option: $arg" >&2; exit 2 ;;
    esac
done

say()  { printf '\033[1m==>\033[0m %s\n' "$*"; }
ask()  {  # ask "question" -> 0 for yes
    [ "$ASSUME_YES" = 1 ] && return 0
    [ -t 0 ] || return 1
    read -r -p "$1 [Y/n] " reply
    [[ -z $reply || $reply =~ ^[YyJj] ]]
}

sh_quote() {  # sh_quote TEXT -> TEXT as one word of a shell script, whatever characters it has
    local text=$1 quote="'" out="" c i
    for ((i = 0; i < ${#text}; i++)); do
        c=${text:i:1}
        if [ "$c" = "$quote" ]; then out+="'\\''"; else out+=$c; fi
    done
    printf "'%s'" "$out"
}
desktop_exec_arg() {  # desktop_exec_arg PATH -> PATH as the program of an Exec= line (Desktop Entry specification)
    local arg=$1 out="" c i
    if [[ $arg =~ ^[A-Za-z0-9_@.,:/+=-]+$ ]]; then
        printf '%s' "$arg"
        return
    fi
    # anything else (a space in the name of the home directory, say) has to
    # be quoted; backslashes are doubled once more, as in every value
    for ((i = 0; i < ${#arg}; i++)); do
        c=${arg:i:1}
        # shellcheck disable=SC1003  # a backslash is meant, not an escaped quote
        case $c in
            \\) out+='\\\\' ;;
            '"'|'`'|'$') out+="\\\\$c" ;;
            '%') out+='%%' ;;
            *) out+=$c ;;
        esac
    done
    printf '"%s"' "$out"
}
install_desktop_file() {  # install_desktop_file TEMPLATE DESTINATION: puts the launcher in the place of @EXEC@
    local line
    while IFS= read -r line || [ -n "$line" ]; do
        case $line in
            'Exec=@EXEC@'*) printf 'Exec=%s%s\n' "$EXEC" "${line#Exec=@EXEC@}" ;;
            *) printf '%s\n' "$line" ;;
        esac
    done < "$1" > "$2"
}

[ -x "$PYTHON" ] || { echo "Python not found at $PYTHON" >&2; exit 1; }
"$PYTHON" -c 'import sys; sys.exit(sys.version_info < (3, 11))' \
    || { echo "Kritzel needs Python 3.11 or newer; $PYTHON is older." >&2; exit 1; }
case "$PYTHON$DEST$BIN" in *$'\n'*) echo "paths with line breaks in them are not supported" >&2; exit 1 ;; esac

# --- 1. dependencies -------------------------------------------------------
declare -A PKG=( [PyQt6.QtGui]=python3-pyqt6 [numpy]=python3-numpy [onnxruntime]=python3-onnxruntime )
missing=()
for mod in PyQt6.QtGui numpy onnxruntime; do
    # -P: only installed modules count, not a file of that name in the current directory
    "$PYTHON" -P -c "import $mod" 2>/dev/null || missing+=("${PKG[$mod]}")
done
mkdir -p "$STATE/kritzel"
chmod 700 "$STATE/kritzel"      # the log and what the panel remembers are private
if [ ${#missing[@]} -gt 0 ]; then
    say "Missing system packages: ${missing[*]}"
    if command -v dnf >/dev/null; then
        if ask "Install them now with 'sudo dnf install ${missing[*]}'?"; then
            sudo dnf install -y "${missing[@]}"
            # remembered so that uninstall.sh can tell you what was added
            printf '%s\n' "${missing[@]}" >> "$STATE/kritzel/installed-packages"
            sort -u -o "$STATE/kritzel/installed-packages" "$STATE/kritzel/installed-packages"
        else
            echo "Install them and run this script again."; exit 1
        fi
    else
        echo "dnf not found. Install the Python modules PyQt6, numpy and onnxruntime" >&2
        echo "for $PYTHON with your package manager, then run this script again." >&2
        exit 1
    fi
fi

# --- 2. files --------------------------------------------------------------
say "Installing to $DEST"
rm -rf "$DEST.new"
mkdir -p "$DEST.new" "$BIN" "$DATA/applications" "$CONF/kritzel"
cp -r "$SRC/kritzel" "$SRC/protocols" "$SRC/models" "$SRC/data" "$DEST.new/"
cp "$SRC/uninstall.sh" "$SRC/README.md" "$SRC/ARCHITECTURE.md" "$SRC/CHANGES.md" "$SRC/LICENSE" "$DEST.new/" 2>/dev/null || true
find "$DEST.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$DEST" && mv "$DEST.new" "$DEST"

# The paths go into the launcher as quoted words: whatever characters they
# contain, the shell never takes them for code.
cat > "$BIN/kritzel" <<LAUNCHER
#!/bin/sh
# Kritzel launcher (created by install.sh)
PYTHONPATH=$(sh_quote "$DEST")"\${PYTHONPATH:+:\$PYTHONPATH}" exec $(sh_quote "$PYTHON") -P -m kritzel "\$@"
LAUNCHER
chmod +x "$BIN/kritzel"

EXEC=$(desktop_exec_arg "$BIN/kritzel")
# kritzel.desktop            makes Kritzel selectable as the virtual keyboard
# kritzel-switch.desktop     launcher "Handwriting / Keyboard": toggles between Kritzel and the normal keyboard
# kritzel-settings.desktop   the settings window
for name in kritzel kritzel-switch kritzel-settings; do
    install_desktop_file "$SRC/data/$name.desktop.in" "$DATA/applications/$name.desktop"
done
[ -f "$CONF/kritzel/config.ini" ] || cp "$SRC/data/config.ini" "$CONF/kritzel/config.ini"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

# --- 3. self-test ----------------------------------------------------------
say "Self-test"
if ! "$BIN/kritzel" status; then
    echo; echo "The self-test failed - Kritzel was installed but NOT enabled." >&2
    exit 1
fi

# --- 4. enable -------------------------------------------------------------
echo
current=$(kreadconfig6 --file kwinrc --group Wayland --key InputMethod 2>/dev/null || true)
if [ "${current##*/}" = kritzel.desktop ]; then
    # an update: let KWin start the new version
    "$BIN/kritzel" restart || true
elif [ "$ENABLE" = yes ] || { [ "$ENABLE" = ask ] && ask "Make Kritzel the Plasma virtual keyboard now? (undo: kritzel disable)"; }; then
    "$BIN/kritzel" enable
else
    echo "Not enabled. Later: 'kritzel enable', or System Settings > Keyboard > Virtual Keyboard."
fi
case ":$PATH:" in *":$BIN:"*) ;; *) echo "Note: $BIN is not in your PATH; use the full path $BIN/kritzel." ;; esac
echo
say "Done.  Try the recognition any time with:  kritzel try"

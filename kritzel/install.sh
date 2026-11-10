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
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONF=${XDG_CONFIG_HOME:-$HOME/.config}
STATE=${XDG_STATE_HOME:-$HOME/.local/state}
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

[ -x "$PYTHON" ] || { echo "Python not found at $PYTHON" >&2; exit 1; }

# --- 1. dependencies -------------------------------------------------------
declare -A PKG=( [PyQt6.QtGui]=python3-pyqt6 [numpy]=python3-numpy [onnxruntime]=python3-onnxruntime )
missing=()
for mod in PyQt6.QtGui numpy onnxruntime; do
    "$PYTHON" -c "import $mod" 2>/dev/null || missing+=("${PKG[$mod]}")
done
mkdir -p "$STATE/kritzel"
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
cp "$SRC/uninstall.sh" "$SRC/README.md" "$SRC/ARCHITECTURE.md" "$SRC/LICENSE" "$DEST.new/" 2>/dev/null || true
find "$DEST.new" -name __pycache__ -prune -exec rm -rf {} +
rm -rf "$DEST" && mv "$DEST.new" "$DEST"

cat > "$BIN/kritzel" <<EOF
#!/bin/sh
# Kritzel launcher (created by install.sh)
PYTHONPATH="$DEST\${PYTHONPATH:+:\$PYTHONPATH}" exec "$PYTHON" -P -m kritzel "\$@"
EOF
chmod +x "$BIN/kritzel"

sed "s|@EXEC@|$BIN/kritzel|" "$SRC/data/kritzel.desktop.in" > "$DATA/applications/kritzel.desktop"
# launcher "Handwriting / Keyboard": toggles between Kritzel and the normal keyboard
sed "s|@EXEC@|$BIN/kritzel|" "$SRC/data/kritzel-switch.desktop.in" > "$DATA/applications/kritzel-switch.desktop"
sed "s|@EXEC@|$BIN/kritzel|" "$SRC/data/kritzel-settings.desktop.in" > "$DATA/applications/kritzel-settings.desktop"
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

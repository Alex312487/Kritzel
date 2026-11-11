#!/usr/bin/env bash
# Removes Kritzel again and restores the virtual keyboard that was set before.
#   ./uninstall.sh            keep ~/.config/kritzel (settings, personal words)
#   ./uninstall.sh --purge    remove the settings too
set -uo pipefail

# Everything below is removed relative to the home directory; without one,
# "rm -rf" would be aimed at paths that start at the root directory.
case ${HOME:-} in /*) ;; *) echo "HOME is not set to an absolute path" >&2; exit 1 ;; esac

xdg_dir() {  # xdg_dir VALUE DEFAULT -> VALUE if it is an absolute path, else DEFAULT (as in install.sh)
    case $1 in /*) printf '%s\n' "$1" ;; *) printf '%s\n' "$2" ;; esac
}
DATA=$(xdg_dir "${XDG_DATA_HOME:-}" "$HOME/.local/share")
CONF=$(xdg_dir "${XDG_CONFIG_HOME:-}" "$HOME/.config")
STATE=$(xdg_dir "${XDG_STATE_HOME:-}" "$HOME/.local/state")
CACHE=$(xdg_dir "${XDG_CACHE_HOME:-}" "$HOME/.cache")
BIN=$HOME/.local/bin
PURGE=0
case ${1:-} in
    "") ;;
    --purge) PURGE=1 ;;
    -h|--help) sed -n '2,4p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $1" >&2; exit 2 ;;
esac

# 1. give Plasma its previous virtual keyboard back (also stops the running panel)
if [ -x "$BIN/kritzel" ]; then
    "$BIN/kritzel" disable || true
else
    cur=$(kreadconfig6 --file kwinrc --group Wayland --key InputMethod 2>/dev/null || true)
    case $cur in */kritzel.desktop) kwriteconfig6 --file kwinrc --group Wayland --key InputMethod --delete ;; esac
fi

pkgs=""
[ -f "$STATE/kritzel/installed-packages" ] && pkgs=$(tr '\n' ' ' < "$STATE/kritzel/installed-packages")

# 2. files
rm -f --  "$BIN/kritzel" "$DATA/applications/kritzel.desktop" "$DATA/applications/kritzel-switch.desktop"
rm -f --  "$DATA/applications/kritzel-settings.desktop"
rm -f --  "${XDG_RUNTIME_DIR:-/nonexistent}/kritzel.pid" "${XDG_RUNTIME_DIR:-/nonexistent}/kritzel-settings.pid"
rm -rf -- "$DATA/kritzel" "$CACHE/kritzel" "$STATE/kritzel"
[ "$PURGE" = 1 ] && rm -rf -- "$CONF/kritzel"
command -v update-desktop-database >/dev/null && update-desktop-database -q "$DATA/applications" 2>/dev/null || true
command -v kbuildsycoca6 >/dev/null && kbuildsycoca6 >/dev/null 2>&1 || true

echo "Kritzel removed."
[ "$PURGE" = 0 ] && [ -d "$CONF/kritzel" ] && echo "Settings kept in $CONF/kritzel (remove with: ./uninstall.sh --purge)."
if [ -n "$pkgs" ]; then
    echo
    echo "install.sh added these Fedora packages. Other programs may use them by now;"
    echo "if you want them gone as well:"
    echo "    sudo dnf remove $pkgs"
fi

#!/usr/bin/env bash
# Removes Kritzel again and restores the virtual keyboard that was set before.
#   ./uninstall.sh            keep ~/.config/kritzel (settings, personal words)
#   ./uninstall.sh --purge    remove the settings too
set -uo pipefail

DATA=${XDG_DATA_HOME:-$HOME/.local/share}
CONF=${XDG_CONFIG_HOME:-$HOME/.config}
STATE=${XDG_STATE_HOME:-$HOME/.local/state}
CACHE=${XDG_CACHE_HOME:-$HOME/.cache}
BIN=$HOME/.local/bin
PURGE=0
[ "${1:-}" = "--purge" ] && PURGE=1

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
rm -f  "$BIN/kritzel" "$DATA/applications/kritzel.desktop" "$DATA/applications/kritzel-switch.desktop"
rm -f  "$DATA/applications/kritzel-settings.desktop"
rm -f  "${XDG_RUNTIME_DIR:-/nonexistent}/kritzel.pid" "${XDG_RUNTIME_DIR:-/nonexistent}/kritzel-settings.pid"
rm -rf "$DATA/kritzel" "$CACHE/kritzel" "$STATE/kritzel"
[ "$PURGE" = 1 ] && rm -rf "$CONF/kritzel"
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

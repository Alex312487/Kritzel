#!/bin/bash
# Headless end-to-end test: KWin + Kritzel + a fake text application.
set -u
ROOT=$(cd "$(dirname "$0")/.." && pwd)
PY=${PY:-python3}
KWIN=${KWIN:-kwin_wayland}
T=$(mktemp -d /tmp/kritzel-it.XXXXXX)
export HOME=$T/home XDG_RUNTIME_DIR=$T/rt XDG_CONFIG_HOME=$T/home/.config XDG_DATA_HOME=$T/home/.local/share XDG_STATE_HOME=$T/home/.local/state XDG_CACHE_HOME=$T/home/.cache
mkdir -p "$XDG_RUNTIME_DIR" "$XDG_CONFIG_HOME/kritzel" "$XDG_DATA_HOME/applications"; chmod 700 "$XDG_RUNTIME_DIR"
cat > "$T/kritzel" <<EOF
#!/bin/sh
PYTHONPATH="$ROOT" exec "${KRITZEL_PY:-$PY}" -m kritzel -v "\$@"
EOF
chmod +x "$T/kritzel"
printf '[Desktop Entry]\nType=Application\nName=Kritzel\nExec=%s\nX-KDE-Wayland-VirtualKeyboard=true\n' "$T/kritzel" > "$XDG_DATA_HOME/applications/kritzel.desktop"
printf '[Desktop Entry]\nType=Application\nName=Fake Plasma Keyboard\nExec=/bin/sleep 600\nX-KDE-Wayland-VirtualKeyboard=true\n' > "$XDG_DATA_HOME/applications/org.kde.plasma.keyboard.desktop"
printf '[recognition]\ndelay_ms=500\n' > "$XDG_CONFIG_HOME/kritzel/config.ini"
printf '[Wayland]\nVirtualKeyboardEnabled=true\n' > "$XDG_CONFIG_HOME/kwinrc"
export IT_ENABLE="$T/kritzel"
[ "${KRITZEL_LAYER_SCOPE:-}" = dock ] && export KRITZEL_TOUCH_DRAG=relative   # KWin 5 quirks
export KWIN_COMPOSE=Q KWIN_WAYLAND_NO_PERMISSION_CHECKS=1 KWIN PY ROOT T
dbus-run-session -- bash -c '
  "$KWIN" --virtual --width 1280 --height 800 --no-lockscreen --socket wl-it > "$T/kwin.log" 2>&1 &
  KP=$!
  for i in $(seq 50); do [ -S "$XDG_RUNTIME_DIR/wl-it" ] && break; sleep 0.1; done
  sleep 2
  WAYLAND_DISPLAY=wl-it WAYLAND_DEBUG=${ITDEBUG:-0} timeout 150 "$PY" "$ROOT/tests/it_kwin.py" 2> "$T/client.err"; RC=$?
  kill $KP 2>/dev/null; exit $RC'
RC=$?
echo "--- kritzel log"; tail -${LOGLINES:-15} "$XDG_STATE_HOME/kritzel/kritzel.log" 2>/dev/null
echo "--- kwin log"; grep -v "fd limit" "$T/kwin.log" | tail -8
exit $RC

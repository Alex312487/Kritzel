#!/bin/bash
# Runs every test.  PY = python with PyQt6, numpy, onnxruntime and Hershey-Fonts
# (plus pywayland >= 0.4.19 for the KWin test); KWIN = kwin_wayland binary.
#
#   test_units.py   pure logic, no compositor
#   it_pen.py       the real program against a scripted compositor (mockcomp.py):
#                   pen, eraser, palm, scaling, floating, suggestions
#   run_it.sh       the real program inside a headless KWin, with a fake text
#                   application (it_kwin.py) and injected touch / mouse input
#
# The KWin test was written against KWin 5.27, the only version available
# where it was developed.  Two KWin 5 quirks are worked around with
# KRITZEL_LAYER_SCOPE=dock (see surface.py) for the floating tests; without it
# the test checks that Kritzel notices the quirk and docks again.
cd "$(dirname "$0")/.." || exit 1
PY=${PY:-python3}; export PY
fail=0
run() { echo "=== $*"; out=$("$@" 2>&1); rc=$?; echo "$out" | grep -E "^(FAIL|SKIP)|Traceback"; echo "    $(echo "$out" | grep -c '^PASS') passed, exit $rc"; [ $rc -eq 0 ] || fail=1; }
run "$PY" tests/test_units.py
run "$PY" tests/it_pen.py
IT_SCALE=2 run "$PY" tests/it_pen.py
IT_NOFRAC=1 run "$PY" tests/it_pen.py
if command -v "${KWIN:-kwin_wayland}" >/dev/null; then
    run tests/run_it.sh
    KRITZEL_LAYER_SCOPE=dock run tests/run_it.sh
else
    echo "=== KWin test skipped (kwin_wayland not found)"
fi
exit $fail

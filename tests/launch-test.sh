#!/usr/bin/env bash
# Launch a disposable test instance (OmoTest) on a private Xvfb display, pointed at
# the isolated test stack (tests/testenv.py: bridge 127.0.0.1:8794, tmux socket
# omo-test) and with the in-game test driver on 127.0.0.1:8793. Never touches
# the live bridge, other instances or the real display.
#
#   tests/launch-test.sh &      then   tests/acceptance.py
#
# Env: OMO_TEST_DISPLAY (:98), OMO_TEST_PRISM (Prism binary), OMO_TEST_INSTANCE
# (OmoTest; optionally a copy of a larger modpack), OMO_PRISM_DATA
# (default ~/.local/share/omo-test-prism: a Prism data dir of its own, with
# libraries/assets/meta linked from the real one, so the test Prism is a
# separate single-instance and a player's running Prism is never involved).
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
DISP="${OMO_TEST_DISPLAY:-:98}"
PRISM="${OMO_TEST_PRISM:-$HOME/Applications/minecraft/PrismLauncher.AppImage}"
DATA="${OMO_PRISM_DATA:-$HOME/.local/share/omo-test-prism}"
NAME="${OMO_TEST_INSTANCE:-OmoTest}"
INSTANCE="$DATA/instances/$NAME"
[ -d "$INSTANCE" ] || { echo "launch-test: no $NAME instance in $DATA" >&2; exit 1; }

# Prism is single-instance per data dir: a second "prism -l X" is handed to a
# Prism already running on the same dir, on whatever display THAT one uses.
# Only a test Prism on $DISP may receive it; other data dirs are unaffected.
for pid in $(pgrep -u "$(id -u)" -x 'prismlauncher|PrismLauncher\.A.*' || true); do
  [ -r "/proc/$pid/environ" ] || continue            # a zombie (an exited Prism not yet reaped)
  d="$(tr '\0' '\n' <"/proc/$pid/environ" 2>/dev/null | sed -n 's/^DISPLAY=//p')"
  if tr '\0' ' ' <"/proc/$pid/cmdline" | grep -q -- "$DATA" && [ "$d" != "$DISP" ]; then
    echo "launch-test: a Prism on $DATA is running on display ${d:-unknown}; quit it first." >&2
    exit 1
  fi
done

python3 "$HERE/testenv.py" prepare-instance "$INSTANCE"     # refuses while it runs
python3 "$HERE/testenv.py" up
eval "$(python3 "$HERE/testenv.py" env)"

if ! xdpyinfo -display "$DISP" >/dev/null 2>&1; then
  (setsid nohup Xvfb "$DISP" -screen 0 1600x900x24 +extension GLX -nolisten tcp \
     >"$OMO_TEST_DATA/xvfb.log" 2>&1 &)
  for _ in $(seq 50); do
    xdpyinfo -display "$DISP" >/dev/null 2>&1 && break
    sleep 0.2
  done
  xdpyinfo -display "$DISP" >/dev/null 2>&1 || { echo "launch-test: Xvfb did not start on $DISP" >&2; exit 1; }
fi

echo "launch-test: $NAME on $DISP, log $OMO_TEST_DATA/game-launch.log"
exec env -u WAYLAND_DISPLAY DISPLAY="$DISP" LIBGL_ALWAYS_SOFTWARE=1 \
  "$PRISM" --dir "$DATA" -l "$NAME" "$@" >"$OMO_TEST_DATA/game-launch.log" 2>&1

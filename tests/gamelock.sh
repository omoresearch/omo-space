#!/usr/bin/env bash
# One test game at a time. Several people (or agents) can build and run the
# bridge tests in parallel, but there is one Xvfb display, one driver port and
# one isolated stack, so anything that launches or drives the OmoTest game
# holds this lock for the whole session.
#
#   tests/gamelock.sh acquire <who> [wait-seconds]   # blocks until free (default 3600 s)
#   tests/gamelock.sh release <who>
#   tests/gamelock.sh status
#
# A lock older than 90 minutes is treated as abandoned.
set -euo pipefail
LOCK="${XDG_RUNTIME_DIR:-/tmp}/omo-test-game.lock"
cmd="${1:-status}"; who="${2:-$USER}"; wait_s="${3:-3600}"
case "$cmd" in
  acquire)
    end=$((SECONDS + wait_s))
    until mkdir "$LOCK" 2>/dev/null; do
      if [ -n "$(find "$LOCK" -maxdepth 0 -mmin +90 2>/dev/null)" ]; then
        echo "gamelock: breaking abandoned lock held by $(cat "$LOCK/owner" 2>/dev/null)" >&2
        rm -rf "$LOCK"; continue
      fi
      [ $SECONDS -lt $end ] || { echo "gamelock: still held by $(cat "$LOCK/owner" 2>/dev/null)" >&2; exit 1; }
      sleep 10
    done
    echo "$who $(date +%T)" > "$LOCK/owner"; echo "gamelock: acquired by $who" ;;
  release)
    if [ "$(cut -d' ' -f1 "$LOCK/owner" 2>/dev/null)" = "$who" ]; then rm -rf "$LOCK"; echo "gamelock: released"; 
    else echo "gamelock: not held by $who ($(cat "$LOCK/owner" 2>/dev/null || echo free))" >&2; fi ;;
  status) cat "$LOCK/owner" 2>/dev/null || echo free ;;
esac

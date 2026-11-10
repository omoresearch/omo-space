#!/bin/sh
# One-command World Sense demo — no game, no key, no model needed.
#
#   sh scripts/demo.sh
#
# Starts a scratch bridge on a free loopback port, pushes the canned
# snapshot (examples/demo_snapshot.json, the same one the tests use),
# runs the reference agent with the echo brain, prints the cost ledger,
# and cleans up. Takes ~10 seconds. Needs only Python 3.11+ and tmux
# (the bridge starts its own tmux server when one is available; with
# tmux missing it still serves /world/*, it just reports tmux: false).
set -e
ROOT=$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)
DATA=$(mktemp -d /tmp/omo-world-sense-demo.XXXXXX)
PORT=$(python3 -c "import socket; s=socket.socket(); s.bind(('127.0.0.1',0)); print(s.getsockname()[1]); s.close()")
TOKEN_FILE="$DATA/token"
cleanup() {
  if [ -n "$BRIDGE_PID" ]; then kill "$BRIDGE_PID" 2>/dev/null || true; fi
  rm -rf "$DATA"
}
trap cleanup EXIT INT TERM
OMO_DATA="$DATA" OMO_PORT="$PORT" nohup python3 "$ROOT/bridge/omobridge.py" > "$DATA/bridge.log" 2>&1 &
BRIDGE_PID=$!
for _ in 1 2 3 4 5 6 7 8 9 10; do
  curl -fsS "http://127.0.0.1:$PORT/health" >/dev/null 2>&1 && break
  sleep 1
done
echo "== bridge up on 127.0.0.1:$PORT =="
curl -fsS "http://127.0.0.1:$PORT/health"; echo
echo "== snapshot in (what /omo sense does in game) =="
python3 "$ROOT/tools/push_snapshot.py" --bridge "ws://127.0.0.1:$PORT/ws" \
  --token-file "$TOKEN_FILE" --snapshot "$ROOT/examples/demo_snapshot.json"
echo "== reference agent, echo brain (no key, no network) =="
python3 "$ROOT/tools/world_agent.py" --bridge "http://127.0.0.1:$PORT" \
  --token-file "$TOKEN_FILE" --backend echo \
  --task "describe the area and call world.describe" --world "Omo HQ" --dry-run
echo "== cost ledger (one call, tokens counted, no prompt text stored) =="
TOK=$(cat "$TOKEN_FILE")
curl -fsS "http://127.0.0.1:$PORT/world/cost?token=$TOK&workspace=world-agent"; echo
echo "== demo done: bridge stopped, scratch data removed =="

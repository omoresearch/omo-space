#!/usr/bin/env python3
"""Push a snapshot to the bridge over its websocket, like the Fabric mod does.

    python3 tools/push_snapshot.py --bridge ws://127.0.0.1:8791/ws \
        --token-file /tmp/omo-test/token --snapshot examples/demo_snapshot.json

Reads the token, hellos as a ui client, sends {"t": "world_snapshot"},
then asks for world_describe and prints the deterministic summary text.
Exit 1 when the bridge refuses anything, so scripts/demo.sh can fail fast.
"""

import argparse
import asyncio
import json
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge", "vendor"))
import websockets


async def main():
    ap = argparse.ArgumentParser(description="Push a world snapshot to the bridge")
    ap.add_argument("--bridge", default="ws://127.0.0.1:8780/ws")
    ap.add_argument("--token-file", required=True)
    ap.add_argument("--snapshot", default=os.path.join(
        os.path.dirname(__file__), "..", "examples", "demo_snapshot.json"))
    ap.add_argument("--world", default=None)
    args = ap.parse_args()

    token = open(args.token_file).read().strip()
    with open(args.snapshot) as fh:
        snap = json.load(fh)
    async with websockets.connect(args.bridge) as ws:
        await ws.send(json.dumps({"t": "hello", "token": token,
                                  "role": "ui", "station": "push-snapshot"}))
        welcome = json.loads(await ws.recv())
        assert welcome.get("t") == "welcome", f"no welcome: {welcome}"
        await ws.send(json.dumps({"t": "world_snapshot", "snapshot": snap}))
        reply = json.loads(await ws.recv())
        if not reply.get("ok"):
            print(f"snapshot refused: {reply}", file=sys.stderr)
            return 1
        print(f"snapshot accepted: world={reply['world']}")
        await ws.send(json.dumps({"t": "world_describe",
                                  "world": args.world or snap.get("world")}))
        desc = json.loads(await ws.recv())
        if not desc.get("ok"):
            print(f"describe failed: {desc}", file=sys.stderr)
            return 1
        print(desc["snapshot"]["text"])
        print(f"[budget {desc['snapshot']['budget']['used_chars']}"
              f"/{desc['snapshot']['budget']['max_chars']} chars]")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))

#!/usr/bin/env python3
"""Drive the Omo bridge from outside the game.

  testclient.py list
  testclient.py create <name> [terminal|hermes]
  testclient.py run <ws> <tab|-> '<shell command>'     type a line, print the screen
  testclient.py screen <ws> <tab|-> [cols rows]        print the rendered screen
  testclient.py tabs <ws> <new|close|revive> [args]
  testclient.py delete <ws> [force]                     close a whole workspace
"""
import asyncio, json, os, sys
import websockets

DATA = os.path.expanduser("~/.local/share/omo")
TOKEN = open(os.path.join(DATA, "token")).read().strip()
URL = os.environ.get("OMO_URL", "ws://127.0.0.1:8780/ws")


def render(frame, screen):
    for y, runs in frame.get("rows_changed", []):
        screen[y] = "".join(r[1] for r in runs)


async def session():
    ws = await websockets.connect(URL, max_size=2 ** 20)
    await ws.send(json.dumps({"t": "hello", "token": TOKEN, "station": "testclient", "world": "cli"}))
    hello = json.loads(await ws.recv())
    assert hello["t"] == "welcome", hello
    return ws


async def expect(ws, kind, timeout=10):
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout))
        if msg["t"] == kind:
            return msg
        if msg["t"] == "error":
            raise SystemExit("bridge error: " + msg["m"])


async def collect_screen(ws, seconds, cols, rows):
    screen = {y: "" for y in range(1, rows + 1)}
    last = None
    deadline = asyncio.get_event_loop().time() + seconds
    while True:
        left = deadline - asyncio.get_event_loop().time()
        if left <= 0:
            break
        try:
            msg = json.loads(await asyncio.wait_for(ws.recv(), left))
        except asyncio.TimeoutError:
            break
        if msg["t"] == "frame":
            render(msg, screen)
            last = msg
        elif msg["t"] in ("toast", "error", "detached"):
            print(f"[{msg['t']}] {msg.get('m') or msg.get('reason')}")
    return screen, last


async def main(argv):
    cmd = argv[1] if len(argv) > 1 else "list"
    ws = await session()
    if cmd == "list":
        await ws.send(json.dumps({"t": "ws_list"}))
        items = (await expect(ws, "workspaces"))["items"]
        for w in items:
            tabs = ", ".join(f"{t['name']}[{t['state']}{'*' if t['busy'] else ''}]" for t in w["tabs"])
            print(f"{w['id']}  {w['name']:<20} {w['kind']:<9} alive={w['alive']}  {tabs}")
    elif cmd == "create":
        await ws.send(json.dumps({"t": "ws_create", "name": argv[2], "kind": argv[3] if len(argv) > 3 else "terminal"}))
        w = (await expect(ws, "workspace"))["ws"]
        print(w["id"], w["name"], [t["id"] for t in w["tabs"]])
    elif cmd in ("run", "screen"):
        wsid, tab = argv[2], (argv[3] if len(argv) > 3 and argv[3] != "-" else "")
        cols, rows = 80, 24
        if cmd == "screen" and len(argv) > 5:
            cols, rows = int(argv[4]), int(argv[5])
        await ws.send(json.dumps({"t": "attach", "ws": wsid, "tab": tab, "cols": cols, "rows": rows}))
        att = await expect(ws, "attached")
        print(f"attached tab={att['tab']} state={att['state']}")
        if att["state"] != "live":
            return
        if cmd == "run":
            await asyncio.sleep(0.6)
            await ws.send(json.dumps({"t": "in", "d": argv[4] + "\r"}))
        screen, last = await collect_screen(ws, 1.5 if cmd == "run" else 0.8, cols, rows)
        print("-" * cols)
        for y in range(1, rows + 1):
            if screen[y].strip():
                print(screen[y].rstrip())
        print("-" * cols)
        if last:
            print(f"cursor={last['cursor']} scroll={last['scroll']}/{last['max_scroll']} mouse={last['mouse']} cmd={last.get('cmd')}")
    elif cmd == "delete":
        await ws.send(json.dumps({"t": "ws_delete", "ws": argv[2], "force": len(argv) > 3}))
        print(json.dumps(json.loads(await asyncio.wait_for(ws.recv(), 10)), indent=1)[:600])
    elif cmd == "tabs":
        wsid, action = argv[2], argv[3]
        if action == "new":
            await ws.send(json.dumps({"t": "tab_new", "ws": wsid, "kind": argv[4] if len(argv) > 4 else "shell",
                                      "name": argv[5] if len(argv) > 5 else None}))
        elif action == "close":
            await ws.send(json.dumps({"t": "tab_close", "ws": wsid, "tab": argv[4], "force": len(argv) > 5}))
        elif action == "revive":
            await ws.send(json.dumps({"t": "tab_revive", "ws": wsid, "tab": argv[4], "mode": argv[5] if len(argv) > 5 else "shell"}))
        elif action == "rename":
            await ws.send(json.dumps({"t": "tab_rename", "ws": wsid, "tab": argv[4], "name": argv[5]}))
        elif action == "hermes":
            await ws.send(json.dumps({"t": "hermes", "ws": wsid, "tab": argv[4]}))
        msg = json.loads(await asyncio.wait_for(ws.recv(), 10))
        print(json.dumps(msg, indent=1)[:1500])
    await ws.close()


asyncio.run(main(sys.argv))

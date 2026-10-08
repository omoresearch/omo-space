#!/usr/bin/env python3
"""Talk to the in-game test driver (mod started with -Domo.test.port=N;
the port is OMO_TEST_PORT, default 8793).

  gamectl.py status | pages | panel | widgets | term
  gamectl.py cmd '/gamemode creative'
  gamectl.py chat 'hello'
  gamectl.py use X Y Z [face]
  gamectl.py key <glfw-key> [mods]
  gamectl.py type 'text\n'
  gamectl.py click X Y
  gamectl.py be X Y Z
  gamectl.py look YAW PITCH
  gamectl.py slot N
  gamectl.py queue X Y Z event [args...]   queue a CC event on the computer there
  gamectl.py screenshot /path/out.png
  gamectl.py perf [seconds]
  gamectl.py openworld NAME
  gamectl.py quit
  gamectl.py wait [seconds]     wait until the driver answers
"""
import json, os, sys, time, urllib.request

PORT = int(os.environ.get("OMO_TEST_PORT", "8793"))


def call(op, body=None, timeout=40):
    data = json.dumps(body or {}).encode()
    req = urllib.request.Request(f"http://127.0.0.1:{PORT}/{op}", data=data, method="POST",
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return json.loads(r.read().decode())


def main(a):
    op = a[1] if len(a) > 1 else "status"
    if op == "wait":
        deadline = time.time() + float(a[2] if len(a) > 2 else 300)
        while time.time() < deadline:
            try:
                print(json.dumps(call("status"), indent=1)); return
            except Exception:
                time.sleep(2)
        sys.exit("driver did not answer")
    body = {}
    if op == "cmd": body = {"command": a[2].lstrip("/")}
    elif op == "chat": body = {"text": a[2]}
    elif op in ("use", "be"):
        body = {"x": int(a[2]), "y": int(a[3]), "z": int(a[4])}
        if op == "use" and len(a) > 5: body["face"] = a[5]
    elif op == "key": body = {"key": int(a[2]), "mods": int(a[3]) if len(a) > 3 else 0}
    elif op == "type": body = {"text": a[2].encode().decode("unicode_escape")}
    elif op == "click": body = {"x": float(a[2]), "y": float(a[3]), "button": int(a[4]) if len(a) > 4 else 0}
    elif op == "look": body = {"yaw": float(a[2]), "pitch": float(a[3])}
    elif op == "slot": body = {"slot": int(a[2])}
    elif op == "screenshot": body = {"path": a[2]}
    elif op == "perf": body = {"seconds": int(a[2]) if len(a) > 2 else 5}
    elif op == "openworld": body = {"name": a[2]}
    elif op == "queue":
        body = {"x": int(a[2]), "y": int(a[3]), "z": int(a[4]), "event": a[5],
                "args": [int(v) if v.lstrip("-").isdigit() else v for v in a[6:]]}
    try:
        out = call(op, body)
    except Exception as e:
        print(json.dumps({"error": f"driver unreachable: {e}"})); sys.exit(2)
    if op == "term" and "lines" in out:
        print(f"--- {out['cols']}x{out['rows']}")
        for i, l in enumerate(out["lines"]):
            if l.strip(): print(f"{i:2d}| {l}")
        print("---")
    else:
        print(json.dumps(out, indent=1))


if __name__ == "__main__":
    main(sys.argv)

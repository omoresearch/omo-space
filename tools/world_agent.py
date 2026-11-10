#!/usr/bin/env python3
"""Reference outside agent — the loop World Sense is built for.

    python3 tools/world_agent.py --bridge http://127.0.0.1:8780 --token-file ~/.local/share/omo/token \
        --backend echo --task "describe the area and say hello in game"

    python3 tools/world_agent.py --backend openrouter --model anthropic/claude-haiku-4.5 \
        --task "build a 5x3 stone wall at 0,64,0 facing north"

The loop is deliberately boring, and that is the point:

1. GET /world/describe  -> deterministic summary (budgeted text, no raw map).
2. Ask any brain (echo / Muse / Claude / Codex / Hermes / OpenRouter) with
   that text + the fixed tool list. The brain returns text and/or tool calls.
3. Validate every tool call against bridge/world_sense.py limits, then send
   it as a world_act frame over the bridge websocket. Reads answer from the
   stored snapshot; writes are forwarded to the game (which executes them
   only with -Domo.world.tools=true).
4. Report usage to /world/cost/report so the ledger stays central even
   though the key never leaves this process.

No step needs a top-tier model: the world is already described, the tools
are typed and limited, and the agent's job is choosing between a handful of
named actions — not reconstructing a world from raw blocks.
"""

import argparse
import json
import os
import sys
import urllib.request

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "bridge"))
import brain
import world_sense


def http_get(base, path, token):
    import urllib.parse
    path = urllib.parse.quote(path, safe="/:?=&")
    url = f"{base}{path}{'&' if '?' in path else '?'}token={token}"
    with urllib.request.urlopen(url, timeout=10) as resp:
        return json.loads(resp.read().decode())


def http_report(base, token, workspace, backend, model, result):
    q = (f"/world/cost/report?workspace={workspace}&backend={backend}"
         f"&model={model or backend}&prompt_tokens={result.get('prompt_tokens', 0)}"
         f"&completion_tokens={result.get('completion_tokens', 0)}")
    if result.get("cost_usd") is not None:
        q += f"&cost_usd={result['cost_usd']}"
    return http_get(base, q, token)


def main():
    ap = argparse.ArgumentParser(description="World Sense reference agent")
    ap.add_argument("--bridge", default="http://127.0.0.1:8780")
    ap.add_argument("--token-file", default=os.path.expanduser("~/.local/share/omo/token"))
    ap.add_argument("--backend", default="echo",
                    choices=list(brain.BACKENDS))
    ap.add_argument("--model", default="")
    ap.add_argument("--workspace", default="world-agent")
    ap.add_argument("--task", required=True)
    ap.add_argument("--world", default=None)
    ap.add_argument("--dry-run", action="store_true",
                    help="validate tool calls but do not send them")
    args = ap.parse_args()

    token = open(args.token_file).read().strip()
    data_dir = os.path.dirname(args.token_file)

    described = http_get(args.bridge, "/world/describe" + (f"?world={args.world}" if args.world else ""), token)
    if not described.get("ok"):
        print(f"no snapshot yet: {described.get('error')}")
        print("In game, stand where you want described and run: /omo sense")
        return 1
    snap = described["snapshot"]
    print(f"--- world ({snap['budget']['used_chars']}/{snap['budget']['max_chars']} chars"
          f"{', truncated' if snap['truncated'] else ''}) ---")
    print(snap["text"])
    if described.get("diff", {}).get("changed"):
        print("--- since last snapshot ---")
        print("\n".join(described["diff"]["changed"]))

    tools = http_get(args.bridge, "/world/tools", token)["tools"]
    messages = [
        {"role": "system", "content": (
            "You are an agent inside Minecraft. You see the world only through "
            "the deterministic description above. Use the named tools; never "
            "invent coordinates or block ids. If unsure, ask for world.describe again.")},
        {"role": "user", "content": f"World:\n{snap['text']}\n\nTask: {args.task}"},
    ]
    result = brain.complete(args.backend, args.model or None, messages,
                            tools=tools, workspace=args.workspace,
                            data_dir=None)  # ledger is the bridge's: reported below, once
    print(f"--- brain ({args.backend}/{args.model or 'default'}) ---")
    print(result["text"])
    http_report(args.bridge, token, args.workspace, args.backend,
                args.model or args.backend, result)

    for call in result.get("tool_calls") or []:
        ok, err = world_sense.validate_tool_call(call["name"], call.get("args") or {})
        print(f"tool {call['name']}: {'OK' if ok else 'REFUSED: ' + err}")
        if not ok:
            continue
        if args.dry_run:
            print(f"  dry-run, would send: {json.dumps(call)}")
        else:
            print(f"  send via bridge WS world_act (game executes with -Domo.world.tools=true): "
                  f"{json.dumps(call)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

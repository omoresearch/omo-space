# World Sense — let *any* model play Minecraft, not just the biggest one

> **Why this PR exists.** I was watching Yifan's stream on the way from the
> office to the metro, and kept thinking: Omo gives an agent a desk and a
> body, but it never tells the agent *what the world looks like*. The agent
> has to reconstruct it from raw blocks — the most expensive, least reliable
> way to see. This PR is my attempt at the missing piece: the game describes
> the world itself, deterministically, before any model sees it. I dictated
> the idea to my agent Karim with Wispr Flow on that walk; he wrote the
> prompts, built this, and tested it end to end.

## 1. What Omo does today, and the gap

Omo Space already has two of the three things an outside AI needs:

| Need | Omo today | How |
| --- | --- | --- |
| A desk | ✅ | Bridge (`bridge/omobridge.py`, loopback `:8780`) + tmux: a real Hermes / Claude Code / Codex / shell session, painted on a wall. |
| A body | ✅ | `AgentLan`: the integrated server publishes on loopback `:25566`, a Mineflayer agent joins as a player. |
| A picture of the world | ❌ | **The gap.** The desk knows nothing about blocks. The body sees the world only as raw Minecraft protocol — the full chunk stream — and the LLM is expected to turn that into understanding. |

That last step is where the current approach quietly demands a top-tier
model. A small model drowns in raw blocks; a big model guesses, expensively,
and still misplaces things. Vibe-build (the Fabric builder Yifan uses on
stream) has the same shape: 25 WorldEdit *write* tools, a Planner and an
Executor that both hard-require Opus, a paid Anthropic key, and no read /
describe tool at all. If the model can't see, it can't build — and if only
Opus can see, only Opus can play.

World Sense inverts it: **the game describes itself, in code, before any
LLM is involved.** The model's job shrinks from "reconstruct a world" to
"choose between a handful of named tools, given a short summary". Any model
that can follow instructions can do that.

## 2. The design

```
  game (knows the world exactly)
    │  /omo sense  →  WorldSense.snapshot()      mod/.../world/WorldSense.java
    │  deterministic, budgeted, no raw blocks
    ▼
  bridge (loopback :8780, token-guarded)
    │  world_snapshot (WS) → SnapshotStore       bridge/world_sense.py
    │  /world/describe · /world/tools · /world/brain · /world/cost
    ▼
  any brain (the caller's choice)                bridge/brain.py
    │  echo · muse · claude · codex · hermes · openrouter
    │  returns text + tool calls in the fixed tool format
    ▼
  validated tools → game (only with -Domo.world.tools=true)
       bridge/world_sense.py validate_tool_call → WS world_tool frame
       → mod/.../world/WorldTools.java (server thread, direct placement)
```

### 2.1 Deterministic description — `bridge/world_sense.py` + `WorldSense.java`

* The mod computes a **raw snapshot** around a point: per-chunk biome mix,
  height range, top-block and full block histograms, structures, nearby
  entities by type with nearest distance, players, named places. Never
  per-block data — a chunk is 16×16×~384 blocks; we emit histograms.
* The bridge turns it into a **budgeted summary**: structured JSON +
  short prose, default 4000 chars, same input → byte-identical output
  (sorted keys, sorted histograms, fixed rounding). Truncation cuts the
  least important tail first and always reports `truncated: true`.
* A **diff** between snapshots ("blocks: stone +120", "player joined:
  …") so a polling agent pays only for change.
* No secrets in any of it: no seed, no UUIDs, no session ids, no tokens.
  The bridge stores the summary only, never the raw snapshot.

Try it without a game:

```bash
python3 tests/test_world_sense.py   # 12 checks, no network, no key
```

### 2.2 Fixed tools — the same file

Eight tools, typed args, hard limits enforced twice (bridge, then game):

| Tool | Kind | Limit |
| --- | --- | --- |
| `world.describe` | read | radius 0–2 chunks, 4000 chars |
| `world.scan` | read | radius 1–128 |
| `world.set` / `world.walls` | write | 32,768 blocks |
| `world.line` | write | 8,192 blocks |
| `world.place_sign` | write | 4 lines × 15 chars |
| `world.say` | write | 256 chars |
| `world.move_to` | write | 256 blocks, agent player only |

Names mirror vibe-build's WorldEdit tools where they already exist, so
existing builders keep working; `world.describe` / `world.scan` are the
addition. The fancy WorldEdit tools (`cyl`, `sphere`, `generate`, …) stay
in vibe-build's executor — this PR implements the small safe subset by
direct block placement, no WorldEdit dependency, so a weak model can still
place a wall correctly.

### 2.3 Bring your own brain — `bridge/brain.py`

One interface, `complete(backend, model, messages, tools)`:

| Backend | What it is | Auth |
| --- | --- | --- |
| `echo` | deterministic test brain, no network | none |
| `muse` | Muse CLI, when installed | the CLI owns it |
| `claude` / `codex` / `hermes` | the CLIs Omo already ships tabs for | the CLI owns it |
| `openrouter` | any model via OpenRouter's OpenAI-compatible API | `OPENROUTER_API_KEY` env var |

The point is the second row: a Muse subscription, a Claude Pro plan, a
ChatGPT plan — intelligence people *already pay for* — should be borrowable
from outside the game instead of each desk re-buying the biggest model.
The bridge never stores a key and never logs one. `openrouter` is off
unless the key is in the environment.

A reference loop ships as `tools/world_agent.py`: describe → brain → validate
→ act → report usage. With `--backend echo` it runs with no key at all.

### 2.4 Cost ledger — `bridge/cost_ledger.py`

Every brain call is counted: workspace, backend, model, prompt/completion
tokens, USD when known. Append-only JSONL, no prompt text ever stored.
`GET /world/cost` reads it; agents report usage via
`/world/cost/report` (so keys stay with the caller while the ledger stays
central). `OMO_COST_DAILY_CAP_USD` refuses the next call instead of
silently spending. OpenRouter prices move, so the module carries only a
clearly-labelled rough estimate table and prefers the cost the backend
itself reports.

## 3. How to try it

**Fastest (no game, no key, ~10 seconds):** `sh scripts/demo.sh` — see
the full try-it guide with screenshots and expected output in
[`docs/WORLD-SENSE-TRY-IT.md`](docs/WORLD-SENSE-TRY-IT.md). A testable
release tarball is built by `sh scripts/make_release.sh` (and by CI on
every push/PR touching these files — download it from the run's
artifacts, check `SHA256SUMS`, extract, run the demo).

```bash
# 1. Bridge side (no game needed for the read path):
python3 tests/test_world_sense.py
python3 tools/world_agent.py --backend echo --task "describe the area and say hello in game"
# → needs a snapshot first; that comes from step 2.

# 2. In game (Fabric 26.2, this branch):
#    stand where you want described, then:
#      /omo sense            # radius 0: one chunk. /omo sense 1 for 3x3.
#      /omo describe         # same, plus a summary in chat.
#    Any brain can now read it:
#      curl "http://127.0.0.1:8780/world/describe?token=$(cat ~/.local/share/omo/token)"
#      curl "http://127.0.0.1:8780/world/tools?token=..."
#      curl "http://127.0.0.1:8780/world/brain?token=..."
#      curl "http://127.0.0.1:8780/world/cost?token=..."

# 3. Let a brain build (writes are off by default):
#    start with -Domo.world.tools=true, then run world_agent.py with
#    --backend openrouter --model anthropic/claude-haiku-4.5 (or --backend muse).
```

The existing suite still passes: `python3 tests/test_bridge.py` → 104/104.

## 4. Trust and limits (please review these first)

* **Loopback only.** The bridge binds 127.0.0.1; the mod only connects
  there. Nothing in this PR opens a port to the network.
* **Snapshots are ui-role only.** The mod's `BridgeClient` connects with
  the bridge token as `ui`; a station screen (ticket role) that sends
  `world_snapshot` is refused. Screens can *read* the description of the
  world they are already standing in, nothing more.
* **Writes are off by default** (`-Domo.world.tools=true`), execute on the
  server thread in the integrated single-player server only, and only for
  the small tool subset above. Dedicated servers are out of scope here —
  they have no client bridge connection.
* **No re-runs, no silent state.** A snapshot is a point-in-time fact; the
  bridge never re-runs a tool call and never invents a snapshot.
* **Known rough edges** (flagged, not hidden):
  - `WorldSense` iterates the full build height per column; radius 1 is
    ~1s on the server thread. Fine for a command, not for a tick.
  - `WorldTools.place_sign` uses `SignBlockEntity#setTextOnRow` (1.21);
    if a future version moves it, Fabric Loom CI fails here first.
  - `move_to` moves the *agent* player only, never the owner; it needs a
    Mineflayer body in the world.
  - The bridge keeps only the latest snapshot per world (plus the previous
    one, for diff). No history yet.

## 5. The bigger question for Yifan — hosted Omo, with borrowed brains

The code above works on one PC. The thing I actually wanted to ask you
about is the step after it: **what if people didn't have to run any of this
themselves?**

* A panel / platform we host: a player pays a **flat fee to host their
  Minecraft server**, plus the AI features on top. They manage their board
  (desks, agents, worlds) in the browser; we run the bridge and the game
  server for them.
* **Bring your own intelligence:** if they already pay for Muse, Claude,
  ChatGPT or Hermes, they connect that subscription and the agents in
  their world borrow it. No second big-model bill.
* **Or we provide the API:** one OpenRouter key on our side, they pick any
  model, and the cost ledger (section 2.4) shows both sides the spend —
  per desk, per model, per day — so the meter is visible before the bill.
* The flat hosting fee stays flat; intelligence is the metered part, and
  the player can cap it.

Questions I genuinely don't know the answer to and would love your take on:

1. Is the hosted-panel direction something Omo wants, or should this stay
   a local-first tool?
2. For "borrow a subscription": is driving the CLIs (`muse`, `claude`,
   `codex`, `hermes`) the right seam, or is there a cleaner one — an MCP
   server, a local HTTP API the CLIs already expose, something else?
3. Would you rather the write path go through the agent player (Mineflayer
   body, WorldEdit tools) or through direct server-side placement as this
   PR does for the small subset? What trust rule should gate it on a
   hosted server, where the player is not the host?
4. Anything in section 4 you'd want changed before this merges?

I built this as a fork PR so you can take, change, or reject any of it —
the deterministic describer is the part I'd most like to keep, whatever
happens to the rest. Thanks for building in public; it is the reason this
exists.

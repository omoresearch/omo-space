# Agents and terminals

**For now, an agent in Omo Space is a persistent terminal.** Each desk in
the world is a workspace on your PC: a set of tabs that keep running when you
close the screen, walk away or quit Minecraft. A Hermes, Claude Code or
Codex tab is an agent. A shell tab is a plain terminal. Nothing in the world thinks or moves on its own
yet.

---

## 1. The parts

| In the world | On your PC | Lives until |
| --- | --- | --- |
| **Hermes Computer** | A workspace whose first tab runs Hermes in the desk's profile | You close it (`/omo forget`), or the PC restarts |
| **Terminal** | A workspace whose first tab is a bash shell | The same |
| **Tab** | One tmux window inside the workspace | Its program exits, or you close the tab |
| **Monitor wall** | A second screen on the same tab, or a pinned web page | The wall is broken |
| **Omo Pocket** | A workspace that travels with the item | The same as a station |

Behind them:

- **`omo-tmux`** (a systemd user service) owns every session. A workspace is
  one tmux session; a tab is one window.
- **`omo-bridge`** on `127.0.0.1:8780` paints those sessions onto the screens
  in the game and sends your keys back. It listens on your PC only.
- **The Hermes Board** at `http://127.0.0.1:8780/board` lists every
  workspace and what it is doing.

## 2. Make an agent desk

1. Place a **Hermes Computer** (creative tab **Omo Space**).
2. **Shift+right-click** it to open **Station Setup**:
   - **Name**: what the desk is for, for example `Website Dev`.
   - **Kind**: Hermes.
   - **Profile**: the Hermes profile this agent uses. One profile per desk
     gives each agent its own memory, skills and model. List them with
     `hermes profile list`.
   - **Tabs**: the tabs a new workspace starts with, for example `Hermes` +
     `Shell`.
   - **Lock**, if other players may use it but must not change it.
3. Right-click it. The workspace is created and Hermes starts.

Or by command, looking at the station:

```text
/omo preset "Website Dev" hermes web-dev
```

A Hermes tab runs:

```bash
hermes -p <profile> chat --tui -c omo:<workspace>:<tab> --create-if-missing
```

So every tab is **one named Hermes conversation**. Close the screen and open
it again: it is the same conversation, not a new one.

## 3. Make a terminal desk

Place a **Terminal** and open it. You get bash, with your own `~/.bashrc`
loaded first. Use it for builds, logs, servers and other long jobs. The job
keeps running after you close the screen.

Inside any Omo shell, a bare `hermes` starts Hermes in the workspace's
profile.

### Claude Code and Codex tabs

Click **+** at the end of a station's tabs. The menu has four rows:

| Row | Runs in the new tab |
| --- | --- |
| **Shell** (Ctrl+Shift+T) | bash |
| **Hermes** (Ctrl+Shift+H) | Hermes, in the workspace's profile |
| **Claude Code** | `claude`, Anthropic's Claude Code CLI |
| **Codex** | `codex`, OpenAI's Codex CLI |

The menu remembers your last pick and preselects it, so **+** then **Enter**
opens another tab of the same kind (the choice is kept in
`config/omolife-newtab.txt`). Tabs show what they run: Hermes has a gold
spark, Claude Code an orange burst, Codex a blue ring; a shell has no glyph.
New tabs are named **Claude Code** and **Codex** (then *Claude Code 2*, ...);
double-click a tab to rename it.

An agent tab is an ordinary Omo shell that starts the CLI once, in the tab's
folder (your home folder for a new tab, the last folder after a restore).
Quit the agent and you are at the shell prompt; type `claude` or `codex` to
start it again. Like every Omo tab it lives in the `omo-tmux` session server,
so it keeps running when you close the screen or quit Minecraft.

- **Claude Code** gets one session per tab (`--session-id`, a UUID made from
  the workspace and tab). If the tab is revived, or restored after a PC
  restart, it runs `claude --resume <that id>` and you are back in the same
  conversation.
- **Codex** starts fresh after a revive or restore (`codex resume` inside the
  tab gets an older session back).
- **Not installed?** The tab says so and prints the install command
  (`curl -fsSL https://claude.ai/install.sh | bash` or
  `npm install -g @anthropic-ai/claude-code`; `npm install -g @openai/codex`),
  then stays a normal shell. The menu marks the row *not installed*. Install,
  then open a new tab; no restart is needed.
- **Which binary**: the launcher pins the `claude` and `codex` it finds into
  the bridge service (`OMO_CLAUDE_BIN`, `OMO_CODEX_BIN`, like
  `OMO_HERMES_BIN`). Set one to another path to use another build. When unset
  the bridge looks on its `PATH`, then `~/.local/bin`, `~/.claude/local`,
  `~/.npm-global/bin`, `~/.bun/bin`, `/opt/homebrew/bin` and `/usr/local/bin`.
- **Older bridge**: a mod this new against a bridge from before agent tabs
  marks Claude Code and Codex *update bridge* and does not open them (that
  bridge would quietly open a plain shell). Update Omo Space and restart
  `omo-bridge`; open terminals keep running.

Signing in is the CLI's own business: run `claude` or `codex` once in any
terminal (in game or not) and follow its login.

Presets and the bridge API take the same kinds: `tabs=[{"kind": "claude"},
{"name": "Review", "kind": "codex"}]`, and `tab_new {ws, kind: "claude"}`.

## 4. Give an agent a task

Type into its Hermes tab, as in any Hermes chat. Then walk away. To see it
from across the room:

- Put a **monitor wall** against the computer, or link one with the
  **Omo Linker**. It shows the same tab, larger.
- Put a **redstone lamp** against it and run `/omo signal on`. The lamp is
  lit while Hermes works.
- A **Status Lamp** glows gold while working, green when idle and red when
  interrupted.

## 5. What survives what

| Event | Shells and running jobs | Hermes conversation |
| --- | --- | --- |
| Close the screen, walk away | Keep running | Keeps running |
| Quit Minecraft | Keep running | Keeps running |
| Restart the bridge or the launcher | Keep running | Keeps running |
| Claude Code tab after a PC restart | — | Its session resumes (`claude --resume`) when the tab is restored. Codex starts a new session |
| Hermes tab idle and unseen for 20 min | — | Sleeps: Hermes exits cleanly, the conversation is saved, and it resumes when a screen shows the tab again |
| PC restart, or `omo-tmux` stops | **Lost**. The tabs come back (same names and order, shells in their last folder) the next time a screen opens the workspace | Saved. It resumes in its reopened tab (a tab that was asleep stays asleep until you look at it) |

When a screen opens a workspace whose tabs were lost, the bridge reopens
those tabs and says *restored N terminals after a restart*. Commands that
were running are never re-run, and shell scrollback is gone. A tab you
exited yourself stays closed. **Revive shell** and **Resume Hermes** still
work for single tabs. The bridge never starts a session server of its own:
while `omo-tmux` is down, nothing is restored.

A tab that is working never sleeps: Hermes must be `ready`, with an empty
input line, no goal or question on screen and no process of its own. To change
the idle time for every tab, set `OMO_SLEEP_AFTER` (seconds, `0` = never
sleep) on the bridge service. The bridge also has a per-workspace
`keep_awake` switch, but it has no in-game button yet.

## 6. See and manage them from your desktop

```bash
curl -s 127.0.0.1:8780/health                     # bridge up? how many workspaces and views
~/.local/share/omo/bin/tmux -L omo ls             # every workspace session
~/.local/share/omo/bin/tmux -L omo attach -t omo-<workspace>   # open one in a normal terminal
```

Detach from tmux with `Ctrl+B` then `D`. Do not type `exit`: that ends the
tab for the game too.

In game: `/omo workspaces` lists them all, `/omo rebind <id>` shows one on
the station you look at, `/omo forget <id>` closes one.

## 7. Agents in a built map

When Claude Code builds a map ([Building maps](MAPS.md)), every desk you list
under **Agents** gets a preset: a name, a profile and its tabs. The desk has
no workspace until someone opens it, so a shared map never carries your
sessions.

## 8. Agent bodies in your world (LAN join)

A Hermes desk agent works in its terminal. A separate agent runtime on your
PC (for example the Omo Teacher's Mineflayer body) can also join your
single-player world as a player:

- When you open a single-player world and have joined it, the mod opens it to
  LAN on port 25566 (the next free one up to 25570) and writes
  `~/.config/omo/lan.json` (`host`, `port`, `world`, `version`) so the runtime
  can find it. The file is removed when the world closes.
- A bot has no Microsoft account, so the world accepts offline logins. Those
  players may join **from this computer only**: anyone else connecting
  without authentication is disconnected. Your LAN still sees the world in
  its server list.
- The agent bodies (`omo` and `teacher` by default) skip Fabric's registry
  check (they are plain Mineflayer clients), wear the bundled Omo skin and show
  as **ChatGPT - <name>** over their heads, in the tab list and in chat.
- JVM flags: `-Domo.agents.lan=false` turns it all off,
  `-Domo.agents.port=<n>` picks the port, `-Domo.agents.names=a,b` sets the
  agent names, `-Domo.agents.allowRemote=true` also lets unauthenticated
  players in from other computers (only on a network you trust).

## 9. What is not built yet

- A Hermes desk agent does not act in the world. It cannot walk, place
  blocks or press buttons. It works in its terminal on your PC (an agent body
  joined over LAN, above, is a separate runtime).
- Agents do not talk to each other through the world. Hand work between them
  the normal Hermes way.
- Workspaces live on the PC that opened them. A friend on another PC gets
  their own fresh workspace from the same preset.
- Linux only, for now. Mac and Windows support is in progress.

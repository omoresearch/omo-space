# Agents and terminals

**For now, an agent in Omo Space is a persistent terminal.** Each desk in
the world is a workspace on your PC: a set of tabs that keep running when you
close the screen, walk away or quit Minecraft. A Hermes tab is an agent. A
shell tab is a plain terminal. Nothing in the world thinks or moves on its own
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
| Hermes tab idle and unseen for 20 min | — | Sleeps: Hermes exits cleanly, the conversation is saved, and it resumes when a screen shows the tab again |
| PC restart, or `omo-tmux` stops | **Lost** | Saved. The tab shows **INTERRUPTED** and offers **Resume Hermes** |

Nothing is ever re-run without you. **Revive shell** and **Resume Hermes**
are buttons you press.

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

## 8. What is not built yet

- An agent does not act in the world. It cannot walk, place blocks or press
  buttons. It works in its terminal on your PC.
- Agents do not talk to each other through the world. Hand work between them
  the normal Hermes way.
- Workspaces live on the PC that opened them. A friend on another PC gets
  their own fresh workspace from the same preset.
- Linux only, for now. Mac and Windows support is in progress.

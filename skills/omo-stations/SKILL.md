---
name: omo-stations
description: "Use when wiring Omo Space desks and screens to agents: presets, binding, profiles, tasks."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [minecraft, omo-space, hermes, workspaces]
    related_skills: [omo-world-building]
---

# Stations, screens and the agents behind them

A **station** (Hermes Computer or Terminal block) drives the monitor wall it touches. Its first workspace
is created from its **preset** (`OmoPreset`), the first time someone opens it. A **workspace** is a tmux
session the bridge keeps on this PC; it survives the game closing. Background:
[`docs/AGENTS-AND-TERMINALS.md`](../../docs/AGENTS-AND-TERMINALS.md) and
[`docs/builders/README.md`](../../docs/builders/README.md).

## Presets, not workspaces
- Built worlds carry presets only:
  `OmoPreset:{Name:"Edit room", Kind:"hermes"|"terminal", Profile:"<hermes profile>", Tabs:[{Name:"Hermes",Kind:"hermes"},{Name:"Shell",Kind:"shell"}]}`.
  `Profile` must be a real profile (`hermes profile list` / `ls ~/.hermes/profiles`); if none fits, leave
  it empty (default profile) and say so. Never write a workspace id into a map you share: it means nothing
  on another PC, and the station makes a fresh workspace from the preset there.
- Capacity: the bridge allows 128 workspaces, 12 tabs each and 192 open screens
  (`curl -s 127.0.0.1:<port>/health`). Each new station opened costs one workspace. If
  `workspaces + new stations > 128`, ask which old ones to close first (`/omo forget <id>`), or the new
  station hangs on "creating workspace".
- Change the preset of an unopened desk with a tiny pack the player runs after `/reload`:
  `execute if data block X Y Z OmoPreset unless data block X Y Z Workspace run data merge block X Y Z {OmoPreset:{Profile:"..."}}`.

## Find the screens first
- "The terminals" is ambiguous (this room or the next one, this floor or upstairs). Map every station near
  the spot with `tools/mapbuild/omo_scan.py stations X Y Z`, group them by room, and confirm the group
  before changing anything; a wrong binding needs an undo pass.
- A station without `Workspace` keys was never opened. Monitor walls sit just above their station.
- The save lags by one autosave. Live truth is in the bridge log (`~/.local/share/omo/bridge.log`):
  `attach station=<uuid> ws=<id>` lines, and in the game log: `station <id> bound to workspace <ws> '<name>'`.
  The newest one is the desk the player just opened.
- Before sending anything into a workspace, read its screen
  (`~/.local/share/omo/bin/tmux -L omo capture-pane -p -t omo-<ws>:0`): an agent may already be working
  there, possibly you.

## Bind and start without the player clicking
- Create or reuse workspaces through the bridge's ui socket on `ws://127.0.0.1:<port>/` (default port
  8780, `OMO_PORT` overrides): first frame `{t:"hello", token:<~/.local/share/omo/token>, role:"ui"}`,
  then `ws_list`, `ws_create {name, kind:"hermes"}`, `ws_rename`, `ws_profile`, `tab_new`, `tab_rename`,
  `hermes {ws, tab}`. Reuse a workspace with the same name so a retry never duplicates. Don't add a
  custom Host header. Never print or copy the token.
- A `kind:"hermes"` workspace starts Hermes in its first tab by itself; check the status line says ready.
- Bind a station to an existing workspace in-game (cheats on), only when the player named the workspace:
  `/data merge block X Y Z {Workspace:"<id>",WorkspaceName:"<name>",On:1b}`. Several stations may share a
  workspace; give each its own tab. Undo: `/data remove block X Y Z Workspace`, the same for
  `WorkspaceName`, then `{On:0b}`.
- A profile change applies to newly started Hermes only: exit the idle tab, then start `hermes` again.
  Renaming a profile behind live desks: exit the tabs first, then rename, then `ws_rename` + `ws_profile`.

## One task per screen
When the player asks every screen in a room to work on something:
- One line per screen, under 2,000 characters; send it once and read the screen back to confirm.
- Every task names the skill to load, an isolated copy to work in, the files it OWNS (no two agents edit
  the same file), its tests, and "no restarts, commits, pushes or publishing". Each finishes with a
  report and a patch.
- You merge the patches, run the full tests, and only then report it done.

## Room profiles
One Hermes profile per room keeps each desk's memory, skills and model separate. Create it with
`hermes profile create <name> --clone --no-alias --description "..."`, then write its `SOUL.md`: what the
room is for, where it sits in the pipeline (the rooms before and after it and what they hand over), and
the real tools it uses. Refuse a name that already exists, and don't edit other rooms' profiles. In bash
heredocs quote the delimiter (`<<'EOF'`) or backticks in SOUL.md get executed.

## Screens that look broken
- **Stretched or split wall:** a template was placed over cells that still held monitor panels, so CC:T
  kept the old sizes. The game log shows one URL `web page opened` from 2+ origins on one wall. Repair:
  forceload, set every omolife cell of the footprint to air top-down, re-place the template, and refuse if
  the station is bound (`execute if data block <computer> Workspace`).
- **"Page limit reached":** more than 12 web walls within 64 blocks. `omo_scan.py web X Y Z` lists them.
- **Re-point a web wall:** `data merge block X Y Z {OmoUrl:"https://..."}` on every panel of the wall; the
  client reloads the page when the URL changes.

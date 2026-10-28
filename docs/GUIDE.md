# Omo Space — full guide

**Give your AI work a place in the world.** Place a Hermes Computer or a
Terminal, hang Omo Monitors on the wall, and work: the shells and the Hermes
conversations are real processes on your PC. Walk away, build, quit the game;
come back and the same workspace is waiting.

Built on [CC: Tweaked](https://tweaked.cc/) (computers, monitors, the terminal),
[MCEF Modern](https://modrinth.com/project/mcef-modern) (Chromium for web
monitors), Fabric, Prism Launcher and Eclipse Temurin. Minecraft is a trademark
of Mojang/Microsoft; you sign in with your own account.

## In game

Creative inventory → **Omo Space**:

| Block | What it is |
| --- | --- |
| **Hermes Computer** (cream and gold) | A workspace whose first tab starts a Hermes conversation. Pick the Hermes profile it runs as: one desk per agent. |
| **Terminal** (espresso and copper) | A persistent shell. Nothing starts an agent unless you do. |
| **Omo Monitor** | Shows a station's terminal, or pins a real web page that keeps running. Walls join like CC:T monitors. |
| **Omo Linker** | A wand: links monitor walls (and other screens) to a station anywhere nearby, and opens a station's setup. See *Building with Omo*. |
| **Omo Pocket** (Hermes or Terminal) | The same station in your hand: right-click for the same screen and header. Its workspace is made on first use and stays on the item; two pockets are two workspaces. CC:T's own pocket computers work the same way (a Terminal workspace); `craftos` on one drops to CraftOS. |
| **Slim Screen** | An Omo Monitor as a 3 px flat screen on a wall, floor or ceiling (it mounts on the face you click). Same walls, terminal or web page, remote control and settings. Bone meal or a brown, yellow, orange, black or white dye refinishes the frame of the whole wall: cream, espresso, gold, copper, black, white. |
| **Name Plate** | A small plaque (on a wall, a desk or a ceiling) with the nearest station's workspace name and a state dot. |
| **Status Lamp** | A paper andon that glows with the nearest station: gold while Hermes or a command works, green when idle, red once interrupted, dark without a workspace. Standing or hanging. |

Right-click a station. The header above the terminal has two rows. On top:
the workspace (click its name for the **workspace switcher**: every workspace
on your PC, search as you type, switch this station to one or start a new one;
switching never stops anything), its **◆ Hermes profile**, the connection pill
(click to reconnect) and **⋯** (rename, Start Hermes here, close tab, ...).
Below: the tabs, standing on the terminal they show (a dot per tab: green
idle, amber running, red interrupted, grey exited; hover for ✕, double-click
to rename), and **+** for a new Shell or Hermes tab.

| Keys | Where | Does |
| --- | --- | --- |
| Ctrl+C / D / Z / L / W / T … | terminal | exactly what they do in any terminal (Ctrl+W deletes a word) |
| Ctrl+Shift+O | panel | workspace switcher (type to search, Enter switches) |
| Ctrl+Shift+T / H | panel | new shell tab / new Hermes tab |
| + (end of the tabs) | panel | new tab menu: Shell, Hermes, Claude Code, Codex; your last pick is preselected (+ then Enter repeats it) |
| Ctrl+Tab, Ctrl+Shift+Tab | panel | next / previous tab |
| Alt+1 … 8, Alt+9 | panel | jump to that tab / the last tab |
| Ctrl+Shift+W | panel | close tab (always asks; a second press within 5 s confirms) |
| Ctrl+Shift+N / E | panel | rename workspace / rename tab |
| Ctrl+Shift+P | panel | choose the Hermes profile (type to filter, Enter picks) |
| Ctrl+Shift+R | panel | reconnect the screen |
| Ctrl+Shift+C / V, Ctrl+V | terminal | copy the selection / paste the host clipboard (multi-line safe) |
| Ctrl+C | terminal | copies when text is highlighted; otherwise interrupts, as usual |
| Ctrl+Shift+A | terminal | select (and copy) the whole screen |
| Ctrl+Shift+F | terminal | find in the screen and the whole scrollback: Enter older, Shift+Enter newer, Ctrl+Shift+F closes |
| Shift+PageUp / PageDown | terminal | scrollback by a page (Ctrl+Shift+Up/Down a line, Ctrl+Shift+Home/End top/live; End returns to live) |
| Ctrl+Shift+= / - | terminal | bigger / smaller text on an attached monitor wall |
| Ctrl+Shift+Q | terminal | leave the screen client (the only way to CraftOS) |

Every other key reaches the app as in xterm: Shift+Tab, Alt+key, Ctrl/Shift/Alt
with arrows, Home/End, PageUp/PageDown, Insert/Delete and F1-F12,
Ctrl+Backspace (delete a word). Esc always closes the GUI, even while vim or
Hermes runs; Ctrl+[ sends them Esc.

| Mouse | Does |
| --- | --- |
| drag / double-click / triple-click | select characters / a word (paths and URLs whole) / lines; the selection is copied to the host clipboard on release |
| Shift+click | extend the selection |
| drag inside Hermes / vim / htop | still selects text; a click without moving goes to the app |
| middle-click | paste |
| right-click | Copy / Paste / Select all / Find / Clear scrollback |
| Ctrl+click | open the link under the pointer in your browser |
| wheel | the app's own scrolling when it uses the mouse (vim, htop), otherwise the scrollback; Shift scrolls faster |

Apps can copy too: OSC 52 from vim, nvim, tmux or Hermes lands on the host
clipboard. Colours use an Omo dark palette (256-colour and truecolour map onto
it; bold is bright). A monitor wall touching a station is a second screen of
the same tab at its own size; whichever you use (the computer, or the wall
while you control it) sets the terminal size.

A monitor touching a station becomes its screen, at any wall size.
**Right-click a wall** (up to 24 blocks away) to take control of it where you
stand: your keys go to its station (or its web page), the crosshair is the
mouse (click, drag, wheel), W/A/S/D type instead of walking, and a pill at the
top says what you are typing into; **Esc** lets go. Just looking at a web wall,
the wheel scrolls its page. **Shift+right-click** (empty hand) opens the wall's
controls: **Terminal** or **Web page**, an address, **Pin page**, zoom, and
**Interact** (the page full-window; Esc returns). Try
`http://127.0.0.1:8780/board`: the **Hermes Board**, a live view of every
workspace and what it is running.

**Web walls** behave like a browser. Links that open a new window
(target=_blank, middle or Ctrl+click, `window.open`) open as **tabs** of that
wall: up to 6, with a tab strip in Interact and Ctrl+T / Ctrl+W /
Ctrl+(Shift+)Tab. Each wall has its own **sound**: the Sound row in its
controls and the Interact bar have [−] [♪ 80%] [+]; click the middle to mute,
scroll on it, or press Ctrl+M. A wall's volume is also scaled by Minecraft's
Master and Jukebox/Note Blocks sliders and fades with distance (full within 6
blocks, silent from 40). Up to 12 pages run at once; past that, the page
nobody has looked at for longest closes and reopens when you look back. A
memory guard pauses new pages when the machine runs low and closes runaway
Chromium processes. Logged-in pages: `-Domo.firefoxCookies=true` (or
`OMO_FIREFOX_COOKIES=1`) copies your Firefox cookies in, one way, at start
and when Firefox changes them. It is off by default, because every page a
wall shows, including one another player pinned, would then browse with
those logins.

Chat commands (world owner or op): `/omo status`, `/omo workspaces` (click one
to bind the station you look at), `/omo rebind <id>`, `/omo rename <name>`,
`/omo profile [<name>]`, `/omo forget <id> [force]`.

### Building with Omo: the Linker and the Station Setup

The **Omo Linker** (crafted: ender pearl, gold ingot, stick, corner to
corner, like a tool) wires screens to stations without wires. Right-click a
station to choose it (its corners glow, the action bar says *Linking to …*),
then right-click monitor walls anywhere within 48 blocks: each becomes a
mirror of that station's screen, at the largest text that fits, as many
walls as you like. Shift+right-click a linked wall unlinks it (or use
**Unlink** in the wall's settings); Shift+right-click the air forgets the
chosen station. While you hold the Linker, gold dust drifts from every
linked screen to its station. A linked wall is used like any other: right-
click it from across the room and type, click and scroll into the station.
A wall *touching* a station still works as before, as that tab's second
view at its own size; a *linked* wall mirrors the computer's own screen.

**Shift+right-click a station** (empty hand, or with the Linker) for its
**Station Setup**: name, kind (Hermes or Terminal), Hermes profile, the tabs
a fresh workspace starts with, and **Lock** (players can use a locked
station, but outside creative mode cannot rename it, switch its workspace
or change its profile). A station with a workspace is renamed and
re-profiled at once. World owner or op only.

Sharing a map: stations keep their setup as a preset (`OmoPreset`) and links
are saved relative to the screen, so rooms survive `/clone`, structure
blocks (rotated too) and world copies. A map opened on another PC, where its
workspace ids mean nothing, gives each station a fresh workspace from its
preset instead of an error. No token or host path is ever in the world.
The preset can also be written by command:
`/setblock x y z omolife:hermes_computer[facing=south]{OmoPreset:{Name:"Website · Dev",Kind:"hermes",Profile:"web-dev",Tabs:[{Name:"Hermes",Kind:"hermes"},{Name:"Shell",Kind:"shell"}]}}`
(Profile and Tabs optional; an Omo Pocket takes the same `OmoPreset` in its
custom data and uses it once).

In survival every block is crafted (the recipe book shows them once you pick
up an advanced computer, an advanced pocket computer or an Omo station):
Hermes Computer = CC:T advanced computer, 2 gold, a feather; Terminal =
advanced computer, 3 copper; 4 Omo Monitors = 7 gold, bone meal, a glass pane;
3 Slim Screens = 4 gold, bone meal, a glass pane; Omo Pockets = an advanced
pocket computer with the feather and gold, or the copper; 2 Name Plates = 3
gold nuggets, 2 dark oak planks, redstone; a Status Lamp = 4 sticks, 3 paper,
glowstone, redstone. Monitor and Slim Screen walls form however they are
placed: by hand, `/fill`, a structure template or another mod. A Name Plate
or Status Lamp follows the nearest station within 8 blocks (right-click it to
see which).

**Building with Omo** ([docs/builders](builders/README.md)): a
comparator reads a station (0 no workspace, 3 idle, 8 working, 15
interrupted) and `/omo signal on` lights a lamp while it works; room kits
(`/omo kit hermes_desk`, `/place template omolife:<kit>`); `/omo preset`;
an Omo Space advancement tab; `/omo export-check` and `/omo export-clean`
before sharing a map.

**Agents and terminals** ([docs/AGENTS-AND-TERMINALS.md](AGENTS-AND-TERMINALS.md)):
for now every agent is a persistent terminal, one Hermes tab per desk and
profile; what survives a closed screen, a quit game and a reboot.
**Building maps** ([docs/MAPS.md](MAPS.md)): describe rooms, agents and screens
in a short brief and Claude Code builds a reversible datapack, tested on a
copy of your save.

The bundled world **Omo Workshop** is a small open-air atelier with a Hermes
Computer driving a 5x3 terminal wall, a Terminal at its own desk, and the
Hermes Board and Hermes docs on the walls.

## How it works

```
 Minecraft (client + integrated server)                     Host (your user)
 ┌───────────────────────────────────────┐  ws + ticket  ┌──────────────────────────────┐
 │ Hermes Computer / Terminal (CC:T)     │─────────────▶ │ omo-bridge  127.0.0.1:8780   │
 │   station.lua paints frames, sends    │◀───────────── │  views = tmux attach clients │
 │   keys; omo.ticket() → one-shot       │    frames     │  /board  live status page    │
 │ Station panel (Java): tabs, profile,  │─ ws + token ─▶│                              │
 │   rename, revive, Hermes, reconnect   │               └──────────────┬───────────────┘
 │ Omo Monitor: CC:T terminal, or a      │                              │ tmux -L omo
 │   Chromium page (shared, capped pool) │               ┌──────────────▼───────────────┐
 └───────────────────────────────────────┘               │ omo-tmux  (the session owner) │
                                                         │  workspace = session          │
                                                         │  tab = window (@omo_tab tag)  │
                                                         │  hermes -p <profile> chat …   │
                                                         └───────────────────────────────┘
```

- **Workspace** = one tmux session with a durable 12-hex id and an optional
  Hermes profile. **Tab** = one tmux window, tagged with its tab id. A **view**
  is a grouped tmux session per open screen; closing a screen only ends the view.
- **Restarting the bridge, the game or the launcher never touches a shell.**
  Stopping the session server or rebooting does, and the UI says so:
  **INTERRUPTED** (host or session server restarted), **ENDED** (every tab
  exited on its own), **TAB ENDED** (this tab's process finished). When a
  screen next opens an interrupted workspace, its lost tabs are reopened as
  they were (names, order, each shell's last folder, Hermes on its own
  conversation). A command that was running is never re-run; *Revive shell*
  and *Resume Hermes* still work per tab.
- **Hermes, the Hermes way.** A Hermes tab runs
  `hermes [-p <profile>] chat --tui -c omo:<workspace>:<tab> --create-if-missing`:
  a titled conversation in that profile's own session store, so resuming never
  forks a new chat and each desk keeps its own memory and skills. Hermes' own
  approval prompts are untouched. In any tab of a profiled workspace, a bare
  `hermes` uses that profile too.
- **Trust.** The bridge listens on loopback only and refuses foreign Host
  headers. The mod's controls authenticate with the token in
  `~/.local/share/omo/token` (0600). The Lua screen inside a computer only ever
  holds a single-use ticket bound to its station, its workspace and the running
  bridge process, and can only look at and type into that one workspace. The
  world stores workspace ids, station ids and monitor URLs, never a credential.
  Browser profile data stays in MCEF's directory on the host.

## Install

```bash
launcher/omo-launcher.py         # the Omo Space app: install, sign in, play, repair, workspaces
# or the engine directly:
launcher/omo_setup.py setup      # pinned, hash-verified runtime in ~/.local/share/omo/runtime; services
launcher/omo_setup.py signin     # Microsoft device-code sign-in (needs Omo's approved client ID)
launcher/omo_setup.py play
launcher/omo_setup.py repair     # re-verifies every file; never touches saves, sign-in, workspaces, token or Hermes config
```

Omo runs its own copy of Minecraft 26.2 + Fabric + mods, and is the only window
the player sees. With Omo's approved Microsoft client ID (`microsoft.client_id`
in `launcher/manifest.json`, or `OMO_MSA_CLIENT_ID`) it signs in and starts Java
itself. Until then it uses a private, headless Prism Launcher (its own data dir;
the Microsoft sign-in window shows once). Your own Prism, its instances and
accounts are never used or changed; worlds from the old "OmoResearch" instance
are copied across once. Hermes is never changed: setup finds your `hermes`,
shows which model/provider it runs, and pins that executable for the bridge.
Without Hermes, Terminals work and Hermes Computers open a shell.

Developer loop: `OMO_TEST=1 mod/build.sh` builds the jar with the in-game test
driver (plain `mod/build.sh` for release), `python3 mod/tools/make_textures.py`
redraws the block textures, `python3 worlds/build_workshop.py` rebuilds the
starter scene in a running test game.

## Tests

```bash
python3 tests/test_bridge.py     # 99 checks: real bridge on a disposable tmux server + private Xvfb clipboard, stub hermes (~90 s)
python3 tests/test_platforms.py  # 28 checks: macOS + Windows paths, archives, launchd, WSL, argfile; any host
python3 tests/test_launcher.py   # 58 checks: downloads, sign-in, launch args, world copy, repair; fake HOME, offline
python3 tests/test_tab_kinds.py  # the "+" menu's kinds, details and remembered pick (needs a JDK)
tests/launch-test.sh &           # OmoTest instance on Xvfb :98 against an isolated bridge (port 8794)
python3 tests/acceptance.py      # the PRD's release checks 1-8 in-game (OMO_TEST_NO_HERMES=1 skips inference)
```

No test touches the live bridge, its token or its tmux server: the acceptance
run brings up its own stack (`tests/testenv.py up|down|status`).
Desktop attach for diagnostics: `~/.local/share/omo/bin/tmux -L omo attach -t omo-<workspace>`.

## Limits (honest)

- Singleplayer and trusted local worlds. Stations and monitors answer only to
  the world owner (or ops).
- Web pages keep running while the game is open. Closing the game ends the
  browser; the page binding is restored on return, in-page state is not.
- MCEF's Chromium has no proprietary codecs: VP8/VP9/AV1/Opus/MP3 play
  (YouTube works), H.264/AAC/HLS do not (most Instagram, X and Reddit video).
  It runs without GPU acceleration (`--disable-gpu`), because GPU-process
  restarts used to take the whole game down.
- CC:T's font is ASCII with 16 colours; TUIs are faithful ASCII renderings.
- macOS uses Homebrew tmux under launchd; Windows runs the session server and
  Hermes in WSL2. Both are tested offline only so far: see docs/PLATFORMS.md.

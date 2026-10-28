# Changelog

## v1.2.0 — 2026-10-28

### Agent bodies join your world

- Single-player worlds open to LAN for local AI agents once you have joined
  (port 25566, or the next free one up to 25570; written to
  `~/.config/omo/lan.json`). Offline logins are accepted **from this computer
  only**; any other unauthenticated player is disconnected. Off with
  `-Domo.agents.lan=false`; see docs/AGENTS-AND-TERMINALS.md.
- The agent bodies (`omo`, `teacher`; `-Domo.agents.names`) skip Fabric's
  registry check, wear the bundled Omo skin and show as **ChatGPT - <name>**.

### Web walls

- **Web monitors take the keyboard properly.** Enter now submits forms and
  breaks lines (it only ever sent key-down/up, never the Enter character).
  On Linux, Shift, Ctrl, Alt, F-keys, Home/End/PageUp/PageDown, the keypad,
  ` and ' reach pages as themselves instead of other keys, and
  `KeyboardEvent.code` is right, so web games that read WASD by code move.
  Keys still held when the page loses the keyboard (Esc, the address bar,
  letting go of a wall) get their key-up, so nothing keeps walking.
- **Tabs**: links that open a new window (target=_blank, middle/Ctrl+click,
  `window.open`) open as tabs of the wall, up to 6, with a tab strip in
  Interact and Ctrl+T / Ctrl+W / Ctrl+(Shift+)Tab. A pinned page only moves
  when the pin changes.
- **Sound per wall**: volume and mute in the wall's controls and the Interact
  bar (Ctrl+M mutes), saved per world and wall. Scaled by Minecraft's Master
  and Jukebox/Note Blocks sliders and by distance (full within 6 blocks,
  silent from 40).
- **12 pages** instead of 6. At the cap, the page unseen longest (at least
  5 s, never a full-window one) closes and reopens when its wall is looked at.
- **Chromium runaway guard**: page creation is rate-limited, a wall that keeps
  reopening is parked for a minute, browsers are never closed younger than 3 s,
  and a watchdog stops new pages when helpers or memory run high (closing all
  but the two nearest walls, or everything if Chromium itself runs away).
  This fixes a crash where three walls evicted each other's page every frame
  (~2,400 browsers in 90 s, out of memory). Closing pages can no longer crash
  the game (`ClosingQueue`).
- Chromium runs with `--disable-gpu` (GPU-process restarts took the game down)
  and a normal Chrome user agent, so sites stop serving fallback fonts and
  layouts. Startup no longer blocks the render thread.
- Optional, off by default: `-Domo.firefoxCookies=true` (or
  `OMO_FIREFOX_COOKIES=1`) copies your Firefox cookies in, one way.
- Walls far away no longer flicker through the block (layers spread in depth
  with distance).

### Terminals

- Lost terminals come back: when a screen opens a workspace whose tabs the
  session server lost (reboot, OOM), they are reopened with the same names
  and order, shells in their last folder and Hermes on its own conversation.
  Running commands are never re-run.
- A stalled session server no longer ends every open screen: a tmux state
  check that takes over 2 s is skipped instead of failing.
- Copy takes the text that was highlighted, even after a TUI redrew under it,
  and the highlight drops when the text changes. Double/triple click selects
  a word/line in mouse apps (Hermes) too.
- Clipboard on GNOME/Wayland: the bridge finds Xwayland's auth cookie and only
  uses X displays owned by the same user.

### Launcher

- `runtime/local-jvm.json` (`{"jvm_args": [...], "memory_mb": {"min": n,
  "max": n}}`) keeps per-machine JVM tuning across launches from any entry
  point; `OMO_JVM_ARGS` still applies after it.
- `mod/build.sh` honours `PRISM=`.

### macOS and Windows

- The product is now called **Omo Space**: game window, creative and
  advancement tabs, launcher, installers and docs. The repo moves to
  github.com/omoresearch/omo-space. The mod id `omolife`, data folders and the
  `omo-research` command are unchanged, so installs and worlds carry over.
- **macOS** (Apple Silicon and Intel): pinned Temurin and the universal Prism
  app. The session server is Homebrew tmux, and both services run as launchd
  agents. Data lives in `~/Library/Application Support/OmoResearch`.
- **Windows 10/11**: the game, Java and Prism run natively. The session server,
  bridge and Hermes run in WSL2 and are reached over localhost. Install with
  `install.ps1`. Data lives in `%LOCALAPPDATA%\OmoResearch`.
- The mod finds the bridge token in each OS's data folder.
- Release bundles: `-linux-x64.tar.gz`, `-macos.tar.gz` and `-windows.zip`.
- Tested offline only, with no real Mac or Windows run yet. See docs/PLATFORMS.md.

## v1.1.0 — release candidate (private; not published)

**Blocks:** Hermes Computer, Terminal, Omo Monitor, Slim Screen, Omo Linker,
Omo Pocket (Hermes and Terminal), Name Plate, Status Lamp. Survival recipes,
an Omo Space advancement tab and room kits.

**Workspaces that survive:** tmux owns every shell. Closing a screen, leaving
the chunk, quitting the game or restarting the bridge never stops a process.
A stopped session server is reported honestly as INTERRUPTED; *Revive shell*
and *Resume Hermes* are explicit.

**Hermes, the Hermes way:** one titled conversation per tab in the chosen
profile's own session store; `hermes -p <profile>` per desk.

**Web monitors:** real Chromium pages (MCEF) pinned to monitor walls, shared
and capped, restored after a restart. The Hermes Board at `/board`.

**Terminal:** tabs, workspace switcher, find in scrollback, selection and host
clipboard (OSC 52 too), xterm keys, mouse, link opening, text size on walls.

**Remote walls:** right-click a wall up to 24 blocks away and type into it.

**Launcher:** pinned, hash-verified runtime in its own directory; repair that
never touches saves, sign-in, workspaces or Hermes config.

**Repo:** one-command installer and release bundle, CI for the bridge and
launcher tests, licence and third-party notices, dead files removed.

Tests at release: bridge 61/61, launcher 56/56. In-game acceptance 1-8 last
run 26-27 Sep 2026 (see `docs/RELEASE-CHECKLIST.md`).

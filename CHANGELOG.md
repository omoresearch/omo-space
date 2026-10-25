# Changelog

## Unreleased: macOS and Windows

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
- **Web monitors take the keyboard properly.** Enter now submits forms and
  breaks lines (it only ever sent key-down/up, never the Enter character).
  On Linux, Shift, Ctrl, Alt, F-keys, Home/End/PageUp/PageDown, the keypad,
  ` and ' reach pages as themselves instead of other keys, and
  `KeyboardEvent.code` is right, so web games that read WASD by code move.
  Keys still held when the page loses the keyboard (Esc, the address bar,
  letting go of a wall) get their key-up, so nothing keeps walking.

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

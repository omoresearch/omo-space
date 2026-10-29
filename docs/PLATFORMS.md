# Platforms

Omo Space runs on Linux, macOS and Windows. The game side is the same
everywhere. Only the session server, which owns your shells, changes from host
to host.

## Platform summary

**Linux x86_64**
- Game: native.
- Session server: pinned static tmux, run as a systemd user service.
- Install with: `install.sh`.
- Status: tested end to end on a real machine.

**macOS (Apple Silicon and Intel)**
- Game: native. Uses Temurin's `.app` bundle and Prism's universal app.
- Session server: your own tmux (`brew install tmux`), run as a launchd agent.
- Install with: `install.sh`.
- Status: untested on a real Mac. See "What was tested" below.

**Windows 10/11 x64**
- Game: native. Uses `javaw.exe` and Prism's MinGW build.
- Session server: tmux inside WSL2, run as a systemd user service in WSL.
- Install with: `install.ps1`.
- Status: untested on a real Windows PC. See "What was tested" below.

## Where files live

**Linux**
- App: `~/.local/share/omo/app`
- Runtime, token and worlds: `~/.local/share/omo`
- Services: `~/.config/systemd/user/omo-{tmux,bridge}.service`

**macOS**
- App: `~/Library/Application Support/OmoResearch/app`
- Runtime, token and worlds: `~/Library/Application Support/OmoResearch`
- Services: `~/Library/LaunchAgents/space.omo.research.{tmux,bridge}.plist`

**Windows**
- App: `%LOCALAPPDATA%\OmoResearch\app`
- Runtime, token and worlds: `%LOCALAPPDATA%\OmoResearch`
- Services: inside WSL, as on Linux

The `OmoResearch` folder names, Prism instance and `space.omo.research.*`
service labels are internal ids from before the product was called Omo Space.
They keep those names so existing installs keep their worlds, token and
services. The command is `omo-space` (`omo-research` still works).

The mod reads the token from the same folder the launcher writes it to
(`BridgeLink.dataDir()` and `omo_setup.default_data_dir()`). A test runs the
mod's real method under each OS name and checks that both give the same path.

## macOS

Install these first:

```bash
brew install tmux python          # the bridge needs Python 3.11+; macOS's own is 3.9
bash install.sh omo-space-<ver>-macos.tar.gz
omo-space                         # or: python3 ".../app/launcher/omo_setup.py" setup
```

- launchd runs two user agents, `space.omo.research.tmux` and
  `space.omo.research.bridge`. They have the same split as on Linux: tmux owns
  the shells, and the bridge only attaches to them. Setup never reloads a
  loaded tmux agent, because a reload would end your running shells. A
  changed bridge agent is reloaded.
- Logs: `~/Library/Application Support/OmoResearch/bridge.log` and `tmux.log`.
- The clipboard uses `pbcopy` and `pbpaste`. Links open with `open`.
- The GTK window is optional. If GTK is not installed, the launcher shows its
  terminal menu instead.

## Windows (WSL2)

Windows has no tmux, so your shells and Hermes run in WSL2. The game runs
natively. WSL forwards `127.0.0.1:8780` to Windows, so the mod reaches the
bridge the same way it does on Linux.

Do this once:

1. Install WSL. In an admin PowerShell, run `wsl --install`, then restart.
2. Inside WSL, run `sudo apt install -y tmux python3`, then install Hermes
   Agent there.
3. Turn on systemd. Add `[boot]` and `systemd=true` to `/etc/wsl.conf`, then
   run `wsl --shutdown`.
4. On Windows, install Python 3.11+ with `winget install Python.Python.3.12`.

Then install, from PowerShell:

```powershell
powershell -ExecutionPolicy Bypass -File .\install.ps1 omo-space-<ver>-windows.zip
```

Setup runs `omo_setup.py services` inside WSL, in its Linux role. It also
turns on `loginctl enable-linger`, so your shells keep running when no WSL
window is open. Then it copies the bridge token to
`%LOCALAPPDATA%\OmoResearch\token`, where the mod reads it.

Setup checks each of these steps. If one is missing, it stops and tells you
the exact command that fixes it: no WSL, no distribution, no python3 in WSL,
no systemd, no token, or the port is not forwarded.

Hermes Computers run Hermes inside WSL. Your Windows files are under
`/mnt/c/...`. The clipboard is the Windows clipboard (`clip.exe` and
PowerShell `Get-Clipboard`). Links open in your Windows browser.

The access token reaches Java in a different way on Windows, which has no
`/dev/fd`. It goes into a file in your own data folder. Java reads it with
`@file`, and the file is deleted 10 seconds after launch, or sooner if the
game exits first. The token is never put on the command line.

## What was tested

These ran on Linux. Each one fakes the platform with `OMO_PLATFORM`, so the
data, Java and Prism paths are worked out the same way the real OS would work
them out:

- **`tests/test_platforms.py`:** 28 tests.
  - Path layouts.
  - Mojang's per-OS library and JVM rules.
  - Real-shaped Java and Prism archives: unpacked, verified, damaged, then
    repaired. This includes the framework symlinks in the `.app` bundle.
  - Hostile archives are refused.
  - The launchd plists and the reload rules.
  - The WSL setup steps and each of their errors.
  - The Windows argfile: written, then deleted, and never on the command line.
  - Game-process detection from `ps` and PowerShell output.
  - The bridge's `ps` fallback for hosts without `/proc`, and the clipboard
    and link-opener choices. The WSL clipboard is checked to receive UTF-16.
  - The mod's real `dataDir()` method under each OS name.
- **Real archives.** With `OMO_REAL_ARCHIVES` set, the test checks the actual
  pinned Temurin and Prism downloads against the manifest hashes and their
  real internal layout.
- **`install.ps1`** was parsed with PowerShell 7.6 (0 errors). It was then run
  under Linux pwsh with a fake `wsl`: the refusal paths, uninstall, and an
  install from the real Windows release zip.
- **`install.sh`** was run with a fake `uname`: a macOS install and uninstall
  from the real macOS bundle, and a refusal on Windows.
- **Mutation run.** Ten faults were seeded in a scratch copy. Each one failed
  the test written for it.

Not tested, because it needs real hardware:

- launchd starting the agents.
- WSL forwarding the port.
- Prism and Java running on macOS or Windows.
- Gatekeeper's quarantine prompt on the Prism app. It is unsigned for
  automation, but Prism is notarised.
- MCEF's Chromium download on each OS.

Before any release on these platforms, do one real play-through on each OS.

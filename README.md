<p align="center">
  <img src="docs/media/hero.jpg" alt="Omo Space — Hermes in Minecraft" width="100%">
</p>

<h1 align="center">Your AI workspace, built in Minecraft.</h1>

<p align="center">
  Place a computer. Start a real Hermes Agent session. Put its terminal on a wall.<br>
  Walk away and come back to the same work, even after closing the game.
</p>

<p align="center">
  <a href="mailto:harry@omo.space?subject=Omo%20Space%20Minecraft%20preview"><b>Request private preview</b></a>
  &nbsp;·&nbsp;
  <a href="#see-it-in-action">See it in action</a>
  &nbsp;·&nbsp;
  <a href="#for-builders">Technical details</a>
</p>

<p align="center"><sub>Private preview · Linux x86_64 · macOS · Windows (WSL2) · Minecraft Java 26.2 · Your own Minecraft account required</sub></p>

---

## A place for work that keeps running

Most agent interfaces disappear when you close the window. Here, the workspace
has a place in the world. Build a desk for an agent, give it a monitor wall, and
walk over when you want to see or control its work. The computer connects to a
real shell on your machine; the Hermes tab connects to a real Hermes session in
the profile you choose.

The game is the interface. Your computer still does the work.

## See it in action

<table>
<tr>
<td width="50%"><img src="docs/media/hermes-station.jpg" alt="A real Hermes conversation inside the Minecraft computer screen"></td>
<td width="50%"><img src="docs/media/world-command-center.jpg" alt="In-world monitor walls showing the Hermes Board and documentation"></td>
</tr>
<tr>
<td align="center"><sub><b>Talk to Hermes.</b> Tabs, a profile, a live terminal and the conversation you return to.</sub></td>
<td align="center"><sub><b>See the room work.</b> A live workspace board and real web pages on monitor walls.</sub></td>
</tr>
</table>

The world is yours to build. These two island images are from our current
Minecraft play world, captured on 30 September 2026. They are separate from
the product close-ups above, not a staged composite.

<table>
<tr>
<td width="50%"><img src="docs/media/island-rooftop.jpg" alt="The rooftop garden and sea at our current Minecraft island workspace"></td>
<td width="50%"><img src="docs/media/island-pier.jpg" alt="The pier and ocean beside our current Minecraft island workspace"></td>
</tr>
<tr>
<td align="center"><sub>The island rooftop. The workspace can be part of any build you make.</sub></td>
<td align="center"><sub>Across the water, away from the screen. Your sessions keep running on the host.</sub></td>
</tr>
</table>

<sub>The island captures use Complementary Unbound shaders for lighting. The
shader pack is not included in Omo Space.</sub>

### The loop

1. **Place** a Hermes Computer or Terminal from the Omo Space inventory tab.
2. **Open** it. Start Hermes, work in a shell, or switch tabs. Connect monitor
   walls to show the same terminal elsewhere in the room.
3. **Leave.** Close the screen, walk to another chunk or quit Minecraft. The
   session continues while your host is awake and the session service runs.
4. **Return** to the same workspace. If the host rebooted, Omo shows that the
   process was interrupted and offers an explicit resume of the saved Hermes
   conversation. It never silently replays a command.

## Make a room worth coming back to

| Build with | What it does |
| --- | --- |
| **Hermes Computer** | One named workspace, with a Hermes conversation in its chosen profile. Give different agents different desks. |
| **Terminal** | A persistent shell when you want tools without starting an agent. |
| **Omo Monitor / Slim Screen** | A larger view of a terminal, or a pinned Chromium webpage. A wall can show docs, a dashboard or your project preview. |
| **Omo Linker** | Connect a wall to a station across the room, without wiring. Multiple walls can mirror one station. |
| **Omo Pocket** | Carry a Hermes or Terminal workspace on an item. |
| **Name Plate / Status Lamp** | Put a name and live working, idle or interrupted state on the build. Status also works with redstone. |

<img src="docs/media/kit-hermes-desk.jpg" alt="Hermes desk kit: computer, terminal wall, lamp and plants" width="100%">

<details>
<summary><b>More real in-game images</b> — web gallery, terminal tabs and room kit</summary>
<br>
<table>
<tr>
<td width="50%"><img src="docs/media/kit-web-gallery.jpg" alt="A wall with a live dashboard and a docs webpage"></td>
<td width="50%"><img src="docs/media/terminal-tabs.jpg" alt="Two terminal tabs preserving shell state"></td>
</tr>
<tr>
<td align="center"><sub>Real webpages on monitor walls.</sub></td>
<td align="center"><sub>Two tabs, with the same PID, directory and environment on return.</sub></td>
</tr>
<tr>
<td><img src="docs/media/kit-meeting-room.jpg" alt="A meeting-room kit with a large terminal wall"></td>
<td><img src="docs/media/kit-redstone.jpg" alt="A comparator reading the station to control a lamp"></td>
</tr>
<tr>
<td align="center"><sub>A big screen for a shared room.</sub></td>
<td align="center"><sub>Build a redstone signal from the agent's state.</sub></td>
</tr>
</table>
</details>

## Who this is for

Developers and researchers who want to *see* their tools in the world they
build. Use a computer for a real Hermes conversation, a plain terminal for
shell work, and a web wall for the docs or dashboard you keep checking. It is
local-first: the bridge runs on your PC, not on a public shell server.

This is an early preview, not a promise of a hosted multiplayer service.
The world, the host shell and the agent are yours to control.

## Get access

**The repo and build are private for now.** There is no public download,
checkout or published GitHub Release yet. [Request a private
preview](mailto:harry@omo.space?subject=Omo%20Space%20Minecraft%20preview)
if you want to test it. We have not announced pricing or a public release.

A preview requires Linux x86_64 with systemd, macOS with Homebrew tmux, or
Windows 10/11 with WSL2 (see [docs/PLATFORMS.md](docs/PLATFORMS.md)); Python
3.11+; a Minecraft Java account and enough space for the game runtime (about 2 GB free). Hermes Agent is
optional for plain terminals. Minecraft and third-party mods are downloaded by
the launcher with pinned hashes. Model-provider access, if you use Hermes, is
separate from Minecraft.

The Linux bundle builds locally and passed install/uninstall in a throwaway
home directory. A clean-machine play-through past Microsoft sign-in remains to
be done before a public release. macOS and Windows support is new and has been
tested offline only, not yet on real Mac or Windows hardware.

## A few important answers

**Is this a real agent or a Minecraft simulation?** A real Hermes process runs
on your machine. The mod sends input to it and paints its terminal in the game.
Hermes uses its own profiles, memory, tools and approval prompts.

**Does the session survive when I quit Minecraft?** Yes, while the host stays
awake and the tmux session server runs. A reboot ends the process. The saved
Hermes conversation can be resumed explicitly; in-flight shell commands cannot.

**Are webpages live?** Yes, while Minecraft is open. The assigned page returns
after a game restart; the browser's in-page state does not.

**Can another player use my host shell?** This preview is for singleplayer or
trusted local worlds. A world or a block is not a security sandbox. Read the
[security model](SECURITY.md) before connecting an untrusted world.

## For builders

The repo includes the Fabric mod, Python bridge, tmux session server, launcher,
world kits, an example workshop, tests and a Linux bundle builder. See the
[full controls and command guide](docs/GUIDE.md), [building guide](docs/builders/README.md),
[agents and terminals](docs/AGENTS-AND-TERMINALS.md), [building maps with Claude Code](docs/MAPS.md),
[release checklist](docs/RELEASE-CHECKLIST.md) and [contribution guide](CONTRIBUTING.md).

```mermaid
flowchart LR
  A["Computer or pocket<br/>in Minecraft"] -->|input and display| B["Local bridge<br/>127.0.0.1 only"]
  C["Monitor walls<br/>terminal or web page"] --> B
  B --> D["tmux session server<br/>workspaces and tabs"]
  D --> E["Your shell"]
  D --> F["Hermes Agent<br/>your profile and conversation"]
```

Local verification: **61/61 bridge** and **56/56 launcher** tests passed. The
mod jar built, and the private Linux bundle passed checksum and isolated
install/uninstall checks. [In-game checks](docs/RELEASE-CHECKLIST.md) were last
recorded on this Linux host; GitHub Actions has not run because the account's
billing/spending limit blocked the runner before it started. The preview is not
yet a public release.

Omo Space's code is [MIT licensed](LICENSE). [Third-party
notices](THIRD_PARTY_NOTICES.md) cover CC: Tweaked, MCEF Modern, Fabric, Prism,
tmux and bundled Python packages. Hermes Agent is by Nous Research.

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH
MOJANG OR MICROSOFT.** Minecraft is a trademark of Mojang/Microsoft.

<p align="center"><sub>Omo Space · Give your AI work a place in the world.</sub></p>

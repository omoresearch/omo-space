<p align="center">
  <img src="docs/media/hero.png" alt="Omo Space. The next interface isn't a chat window. Real AI agents at desks in a world you build." width="100%">
</p>

<p align="center">
  <a href="https://github.com/omoresearch/omo-space/releases/tag/demo-2026-10-27"><img src="docs/media/btn-demo.png" alt="Watch the demo" height="43"></a>&nbsp;
  <a href="#run-it"><img src="docs/media/btn-run.png" alt="Run it" height="43"></a>&nbsp;
  <a href="https://www.twitch.tv/yiifannn"><img src="docs/media/btn-live.png" alt="Live on Twitch" height="43"></a>&nbsp;
  <a href="https://omo.sh"><img src="docs/media/btn-omo.png" alt="omo.sh" height="43"></a>&nbsp;
  <a href="https://github.com/omoresearch/omo-space"><img src="https://img.shields.io/github/stars/omoresearch/omo-space?style=for-the-badge&logo=github&label=Star&color=1a1008&labelColor=4a3b2f" alt="Star Omo Space on GitHub" height="43"></a><br>
  <sub>⭐ Star the repo to follow the build</sub>
</p>

<p align="center"><sub>Hermes Agent · Claude Code · Minecraft Java 26.2 · Linux, macOS, Windows (WSL2) · MIT</sub></p>

<br>

Most AI agents live in a pile of terminal tabs. In **Omo Space**, every agent
gets a desk in a world you build. Place a computer and a real Hermes Agent
session, or a shell for Claude Code, starts on your own machine. Its terminal
goes on the wall, real web pages go on the monitors, and the work keeps running
after you walk away, or quit the game.

## Watch: the whole loop, live

<a href="https://github.com/omoresearch/omo-space/releases/tag/demo-2026-10-27">
  <img src="docs/media/demo-poster.jpg" alt="Play the 15-minute demo: a viewer found Omo Space on Instagram, so Yifan showed him the whole loop, live" width="100%">
</a>

On 27 October a viewer found one of our reels on Instagram and dropped into the
live stream. Yifan, who builds Omo Space, stopped what he was doing and walked
him through everything: the rooms, the agents, the edit review, the publishing
desk, and then one prompt that built an underground robotics lab while they
talked. 15 minutes, one take.

**[▶ Watch the demo](https://github.com/omoresearch/omo-space/releases/tag/demo-2026-10-27)** ·
[download the MP4](https://github.com/omoresearch/omo-space/releases/download/demo-2026-10-27/omo-space-demo-2026-10-27.mp4) (1080p, 346 MB) ·
[read the transcript](docs/demo/TRANSCRIPT.md)

> *"Any product or idea goes from this terminal here, which is just a Hermes,
> into the references room, and it finds us the top references of the most
> viral videos. After we choose, it tells us what we need to record, in this
> room. Once that's done, we go to the editing room, and we have the edits that
> we can just review here."*
>
> *"The robots did all of that. I'm not doing much. I'm just connecting my API
> call, and then doing a few basic prompts over time."*
>
> — Yifan, on stream ([1:03](docs/demo/TRANSCRIPT.md#103--the-loop-end-to-end))

## Inside the world

Every room is an agent at its own desk. You walk up, type into the wall, and it
answers on the wall. These loops are cut straight from that stream.

<table>
<tr>
<td width="33%"><img src="docs/media/loop-references.webp" alt="A web wall in the references room showing reference videos and scripts"></td>
<td width="33%"><img src="docs/media/loop-review.webp" alt="The editing room: the review page for a cut on an in-world monitor wall"></td>
<td width="33%"><img src="docs/media/loop-publish.webp" alt="The publish desk web page pinned to a monitor wall"></td>
</tr>
<tr>
<td valign="top"><b>01 · Find</b><br><sub>"It finds us the top references of the most viral videos."</sub></td>
<td valign="top"><b>02 · Review</b><br><sub>"We have the edits that we can just review here."</sub></td>
<td valign="top"><b>03 · Publish</b><br><sub>"This page will always be here, whenever we want to publish things."</sub></td>
</tr>
<tr>
<td><img src="docs/media/loop-lab.webp" alt="Walking into the underground lab an agent built from one prompt, with glass and water overhead"></td>
<td><img src="docs/media/loop-agent.webp" alt="An agent player walking through the Batcave"></td>
<td><img src="docs/media/loop-batcave.webp" alt="Walking the halls between agent rooms"></td>
</tr>
<tr>
<td valign="top"><b>One prompt, one lab</b><br><sub>"I want to see the glass water above us." It built the lab under the river.</sub></td>
<td valign="top"><b>Agents in the halls</b><br><sub>An agent player you can talk to, walking the same world.</sub></td>
<td valign="top"><b>Build in space</b><br><sub>"Any time I want to work on the robotics project, I just fly down here."</sub></td>
</tr>
</table>

<sub>The rooms, the video pipeline and the agent player are Yifan's own builds
on top of Omo Space. The repo ships the blocks, terminals, web walls and the
local bridge they run on.</sub>

## Made by the loop

Reels the rooms found, cut and published on [Instagram](https://www.instagram.com/yifanexperimenting/),
from clips of the stream.

<table>
<tr>
<td width="25%"><a href="https://www.instagram.com/reel/DfAtqPeApqt/"><img src="docs/media/reel-02.webp" alt="Reel: one Opus 5.5 prompt built an underground lab in Minecraft"></a></td>
<td width="25%"><a href="https://www.instagram.com/reel/DfAtxhbiQPv/"><img src="docs/media/reel-05.webp" alt="Reel: my agent is busy, so I play survival with another agent"></a></td>
<td width="25%"><a href="https://www.instagram.com/reel/DfAuX2fFYoo/"><img src="docs/media/reel-15.webp" alt="Reel: what did you do while I was offline?"></a></td>
<td width="25%"><a href="https://www.instagram.com/reel/DfAuNAKAu_a/"><img src="docs/media/reel-16.webp" alt="Reel: this is my team that runs my business"></a></td>
</tr>
</table>

## What you build with

| Block | What it does |
| --- | --- |
| **Hermes Computer** | One named workspace with a Hermes conversation in the profile you choose. Give different agents different desks. |
| **Terminal** | A persistent shell for tools, scripts or Claude Code, without starting a Hermes agent. |
| **Omo Monitor / Slim Screen** | A bigger view of a terminal, or a real Chromium web page pinned to the wall: docs, a dashboard, your project. |
| **Omo Linker** | Mirror a station onto walls across the room, no wiring. |
| **Omo Pocket** | Carry a Hermes or Terminal workspace on an item. |
| **Name Plate / Status Lamp** | A name and a live working / idle / interrupted light. Status also drives redstone. |

**It keeps running.** tmux owns every session. Close the screen, walk to
another chunk or quit Minecraft: the work continues while your PC is awake.
Come back and it's on the wall where you left it. After a reboot, Omo marks the
desk as interrupted and offers to resume the saved Hermes conversation. It
never replays a command on its own.

## Run it

The code is public and MIT licensed.

1. Read [docs/PLATFORMS.md](docs/PLATFORMS.md). Linux x86_64 is tested end to
   end; macOS and Windows (WSL2) support is new and not yet tested on real
   hardware.
2. Download the bundle for your platform and the installer from the
   [latest release](https://github.com/omoresearch/omo-space/releases/latest)
   (`SHA256SUMS` covers every file), then run
   `bash install.sh omo-research-<version>-linux-x64.tar.gz` (or the macOS
   tarball; `install.ps1` with the Windows zip).
   Or build from source with [CONTRIBUTING.md](CONTRIBUTING.md#build-and-test)
   and the [install guide](docs/GUIDE.md#install).
   Either way the launcher downloads Minecraft 26.2, Fabric, CC: Tweaked, MCEF
   and Java from their official sources, with pinned hashes, into its own
   folder. It never touches your own Minecraft or Prism installs.
3. You need your own Minecraft Java account, Python 3.11+, and about 2 GB free.
   Hermes Agent is optional (plain terminals work without it); model access is
   your own.

Stuck, or want a build? Write to [harry@omo.space](mailto:harry@omo.space?subject=Omo%20Space)
or ask on the [live stream](https://www.twitch.tv/yiifannn).

## Questions

**Real agents, or a Minecraft simulation?** Real. Hermes runs on your machine
with its own profiles, memory, tools and approval prompts, and so does anything
you start in a shell. The mod sends your keys to them and paints their terminal
in the game.

**Does it survive quitting the game?** Yes, while your PC stays awake and the
session service runs. A reboot ends the processes; a saved Hermes conversation
can be resumed, in-flight shell commands can't.

**Are the web pages live?** Yes, while the game is open. The pinned page comes
back after a restart; its in-page state doesn't.

**Can another player use my shell?** This is for singleplayer and trusted local
worlds. A block is not a security sandbox. Read the [security model](SECURITY.md)
first.

## For builders

The repo holds the Fabric mod, the Python bridge, the tmux session server, the
launcher, world kits, an example workshop, tests and a bundle builder.

[Controls and commands](docs/GUIDE.md) ·
[Agents and terminals](docs/AGENTS-AND-TERMINALS.md) ·
[Building guide](docs/builders/README.md) ·
[Building maps with Claude Code](docs/MAPS.md) ·
[Platforms](docs/PLATFORMS.md) ·
[Release checklist](docs/RELEASE-CHECKLIST.md) ·
[Contributing](CONTRIBUTING.md)

```mermaid
flowchart LR
  A["Computer or pocket<br/>in Minecraft"] -->|keys and screen| B["Local bridge<br/>127.0.0.1 only"]
  C["Monitor walls<br/>terminal or web page"] --> B
  B --> D["tmux session server<br/>workspaces and tabs"]
  D --> E["Your shell<br/>tools, Claude Code"]
  D --> F["Hermes Agent<br/>your profile and conversation"]
```

<p align="center">
  <a href="https://github.com/omoresearch/omo-space"><img src="https://img.shields.io/github/stars/omoresearch/omo-space?style=for-the-badge&logo=github&label=Star&color=1a1008&labelColor=4a3b2f" alt="Star Omo Space on GitHub" height="43"></a><br>
  <sub>⭐ Star the repo to follow the build</sub>
</p>

---

Code: [MIT](LICENSE). [Third-party notices](THIRD_PARTY_NOTICES.md) cover
CC: Tweaked, MCEF Modern, Fabric, Prism, tmux and bundled Python packages.
Hermes Agent is by Nous Research.

**NOT AN OFFICIAL MINECRAFT PRODUCT. NOT APPROVED BY OR ASSOCIATED WITH MOJANG
OR MICROSOFT.** Minecraft is a trademark of Mojang/Microsoft.

<p align="center"><sub><a href="https://omo.sh">Omo Research</a> · <i>The next interface isn't a chat window.</i></sub></p>

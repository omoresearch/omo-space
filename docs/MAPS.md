# Building maps with Claude Code

You do not build Omo maps block by block. You describe the room, and
**Claude Code** writes and tests the build. You then run one command in game
to build it, and one command to undo it.

This page tells you what to ask for, what Claude Code does, and how you
install the result. The step-by-step rules Claude Code follows are in
[`MAP-BUILD-PROCEDURE.md`](MAP-BUILD-PROCEDURE.md). The brief below tells it
to read that file first.

---

## 1. What you can ask for

- **A new room or wing in a save you already play.** For example: "Add a
  glass meeting room under the sea, east of my office, with 5 screens".
- **Desks for agents.** One Hermes Computer per agent, each with its own
  name and Hermes profile. See [Agents and terminals](AGENTS-AND-TERMINALS.md).
- **Screens.** Terminal walls (a station's screen, larger) and web walls (a
  pinned web page, such as the Hermes Board).
- **A new room kit** that anyone can place with `/omo kit <name>`.
- **A whole demo world**, built by a script like `worlds/build_workshop.py`.

## 2. Start Claude Code

Quit Minecraft first, or at least leave the world you want changed. Claude
Code reads the save from disk, and a running game writes over it.

```bash
cd omo-space             # this repo, so Claude Code can read the procedure
claude
```

## 3. What to tell it

Copy this brief, fill in the parts in `<...>`, and paste it into Claude Code.
The more exact the room, the fewer rounds you need.

```text
Build a map extension for Omo Space. Read docs/MAP-BUILD-PROCEDURE.md first and follow it.

Save:      <save name, e.g. "Underwater Base">  (find which launcher copy I play now)
Where:     <next to / below / east of which existing room; which room the door opens from>
Style:     <glass under the sea / carved into rock / Japanese wood / match the room next to it>
Rooms:     <one line per room: name, size, how many screens, what is on each screen>
Agents:    <one line per desk: desk name -> Hermes profile (or "default"), tabs>
Terminals: <plain shell desks, if any>
Web walls: <url per wall; at most 6 pages can be live at once>
Keep:      <anything that must not be touched>

Deliver: a datapack with build, undo and visit functions, tested on a copy
of the save, plus the commands I run in game.
```

**Example:**

```text
Save:      Underwater Base
Where:     west of the Glass Office, door in its north wall beside the desk
Style:     glass under the sea, dark prismarine frame, like the Glass Office
Rooms:     Website room, 13x9, 5 screens (3 on the back wall, 1 per side wall)
Agents:    Website Dev -> web-dev (tabs Hermes, Shell); 4 more desks -> web-dev
Terminals: none
Web walls: none
Keep:      the Glass Office desk and its 5 screens
```

## 4. What Claude Code does

You do not need to run any of this. It is here so you can check its report.

1. **Checks the workspace headroom.** Every new station makes one
   workspace on your PC the first time it is opened. The bridge allows 128.
   It reads the count from `curl -s 127.0.0.1:8780/health` and stops to ask
   you if the build would go over.
2. **Reads the save without changing it,** to find walls, floors, water and
   caves around the new rooms.
3. **Writes a generator script** that produces a datapack:
   - `build` carves the rooms, wraps them in a solid shell so the sea or a
     cave cannot flood in, and places furniture, screens and stations;
   - `undo` puts back every original block, read from the save;
   - `visit` teleports you to the main view.
   Screens and stations come from structure templates made with
   `mod/tools/make_kits.py`, because only a template places a monitor wall
   that is already joined.
4. **Checks before it builds:** every block id exists in Minecraft 26.2, the
   dig does not cut into your existing build except at the doors you asked
   for, and each computer touches only its own screen.
5. **Tests on a copy** of the save with a plain Minecraft server: build,
   check every block against the plan, undo, check the save is back to the
   original.
6. **Backs up your save** (a `.tgz`) and copies the datapack into it.

## 5. Install it in your world

Open the world, then type:

```text
/reload
/function <namespace>:build
/function <namespace>:visit
```

Claude Code gives you the exact namespace. To remove the whole extension:

```text
/function <namespace>:undo
```

The first time you open each new station, Omo makes its workspace on your
PC. A Hermes Computer starts its Hermes conversation in the profile from the
brief. Nothing runs before you open it.

## 6. Check the result

- Walk the rooms. Every wall should be one screen, not a set of small ones.
- Open each desk once. Its header shows its name and **◆ profile**.
- Look at `http://127.0.0.1:8780/board` (or a web wall showing it). Every
  new desk is listed after you open it.
- Something wrong? Tell Claude Code what you see, in the same session. It
  edits the generator and you run `undo`, `/reload` and `build` again.

## 7. Limits to plan around

| Limit | Value | What happens past it |
| --- | --- | --- |
| Workspaces on your PC | 128 | A new station hangs on "creating workspace". Close old ones with `/omo forget <id>` first. |
| Tabs per workspace | 12 | The **+** button refuses. |
| Open screens (views) | 192 | A new screen asks you to close one. |
| Live web pages | 6 | The 7th web wall shows "Page limit reached". Unpin a page. |
| Idle Hermes tabs | sleep after 20 min | The conversation is saved and resumes when a screen shows the tab again. |

## 8. Sharing a map

Before you give a world to someone else, run `/omo export-check` and then
`/omo export-clean`. See [Sharing a map safely](builders/README.md#9-sharing-a-map-safely).

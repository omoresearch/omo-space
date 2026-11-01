# Skills for agents building in Omo Space

Drop-in agent skills (`SKILL.md` files) for **Hermes**, **Claude Code** and
**Codex**. They hold what real world builds taught: what breaks, in what order to build and undo, and how to
wire desks to agents. An agent loads them when a task matches.

| Skill | Use it for |
| --- | --- |
| [`omo-world-building`](omo-world-building/SKILL.md) | Rooms and worlds as reversible datapacks, tested on a copy. With [pitfalls](omo-world-building/references/pitfalls.md) and [mechanisms](omo-world-building/references/mechanics.md) (doors, lifts, waterfalls, display entities). |
| [`omo-stations`](omo-stations/SKILL.md) | Desks and screens: presets, binding workspaces, room profiles, one task per screen, fixing broken walls. |

The tools they use are in [`tools/mapbuild/`](../tools/mapbuild/): a read-only save reader and
`omo_scan.py` (stations, web-page budget, player positions).

## Install

From this repository's folder:

```sh
# Hermes
mkdir -p ~/.hermes/skills/omo && cp -r skills/omo-* ~/.hermes/skills/omo/
# Claude Code
mkdir -p ~/.claude/skills && cp -r skills/omo-* ~/.claude/skills/
```

Codex and other agents: point them at this folder ("read skills/omo-world-building/SKILL.md first").

Found a new pitfall while building? Add one line to the matching file and open a pull request.

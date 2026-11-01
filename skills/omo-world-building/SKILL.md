---
name: omo-world-building
description: "Use when building rooms or worlds into an Omo Space save. Reversible datapacks, tested on a copy."
version: 1.0.0
license: MIT
metadata:
  hermes:
    tags: [minecraft, omo-space, datapack, world-building]
    related_skills: [omo-stations]
---

# Building in an Omo Space world

You deliver a **reversible datapack** for a save the player already plays: `build` makes the rooms,
`undo` restores every block exactly, and both are tested on a copy before anything touches the real save.
The step-by-step procedure is [`docs/MAP-BUILD-PROCEDURE.md`](../../docs/MAP-BUILD-PROCEDURE.md); the
brief the player fills in is [`docs/MAPS.md`](../../docs/MAPS.md). This skill adds what real builds taught.
Read `references/pitfalls.md` before generating, and `references/mechanics.md` for doors, lifts,
water and display entities.

## Ground rules
- Never write region files and never run a test server on the live save. Read the save, generate a pack,
  test on a copy, back up, install. The player runs `/reload` and `/function <ns>:build` themselves.
- Never touch the bridge, its token, `omo-bridge` or `omo-tmux` (restarting tmux kills every shell).
  No token or home-folder path goes into a world, a pack or git.
- Other agents may be building into the same save right now. Before choosing a site, list sibling build
  directories, test servers and installed packs (`<save>/datapacks`), and keep your own build dir,
  namespace and test port. Re-read their volumes before you install.

## Triage the request first
Requests are often one line ("a lab, 2 web pages, 3 terminals"). Before designing:
1. **Was something just removed?** `grep 'Running function' logs/latest.log` shows `<ns>:undo` / `:build`
   lines. A build undone just before the request tells you the freed site and what the player rejected.
2. **Where is the player and what do they see?** `tools/mapbuild/omo_scan.py player`, then
   `omo_scan.py stations X Y Z` around that spot. The newest `station ... bound to workspace` line in the
   game log is the desk they just opened ("this room").
3. **What already lives there?** Hand-built rooms, signs, torches and user-placed computers are landmarks:
   keep them out of the plan. A marker the player placed against a wall usually means "build behind here".
4. **Ambiguous place names** ("the lab", "the office", "the terminals"): map the candidates, ask once, and
   if nobody answers pick one and say which in the report.
5. **Has the build already run?** A pack in `<save>/datapacks` proves nothing. Compare the plan with the
   live save block by block before telling the player to run it (twice would be bad).

## Design defaults
- **Match the world.** Find the player's favourite existing build and reuse its materials and lighting
  rather than inventing a new style. Open-plan zones, each with a strong material theme, a lit walkway and
  glowing signs read well on stream and in screenshots.
- **Rooms in pipeline order** when the brief is a workflow (ideas -> script -> edit -> publish): one big
  numbered sign per stage along the walk.
- **Screens:** use the counts the brief gives. With none, one terminal per room (two for the main room)
  and say so in the report. Desks carry presets pointing at real agent profiles (see `omo-stations`).
- **Short travel.** Players hate long staircases: use a drop shaft into a 1-deep pool going down and a
  bubble column going up, or a datapack lift (`references/mechanics.md`).
- **Web pages are scarce.** The client keeps at most **12 pages open within 64 blocks** of the player;
  the 13th wall shows "Page limit reached". Run `omo_scan.py web X Y Z` at several walkable points of the
  new room AND the corridor to it, counting walls you plan to add. If the area is already full, either
  build the room more than 64 blocks away, or build the web bays dark with `add_web` / `remove_web`
  functions the player can run later.

## What every generator needs
- A `plan.json` of every cell you set (`original` from the save, `built` state), used by `verify.py`.
- A **26-neighbour solid shell** around every open cell so caves, sea and sky stay out. Outdoor terraces:
  keep outdoor air out of the shell set or the sealer fills the sky with stone.
- **Asserts before writing anything:** no built block outside the cells you mean to change; every doorway
  cut into an existing build is exactly the block you expect; no open cell within 2 blocks of fluid or
  floating gravel; screens face open air and walls don't touch; template footprints are clear.
- Functions `build`, `undo`, `visit`, each wrapped in `forceload add` + `schedule ... 40t` so all chunks
  are loaded, and a tellraw at the end so the player sees it finished.
- **Order:** build shell -> water -> features -> air -> templates -> decor -> plants. Undo interior first,
  shell bottom-up, then everything whose original was a plant, sorted by y, last.
- Validate every block id against the 26.2 client jar's `assets/minecraft/blockstates` before testing:
  one unknown id stops the whole function from loading.

## Test, preview, install, report
- Test on a copy holding only the touched regions (`level.dat`, `data/`, and the `region`, `entities`,
  `poi` files for those regions): 100 MB instead of the whole save. Set `pause-when-empty-seconds=-1` in
  the test server or mechanisms are tested in a frozen world.
- `verify.py built` (plan == world, no water in air cells, zero diff around neighbours), restart, undo,
  `verify.py restored` (zero mismatches). Vanilla drops `omolife:` and `computercraft:` blocks: those
  cells read as air on the copy, which is expected; check template footprints offline instead.
- Look at it before installing: an ASCII top view at eye level, plus orthographic elevations or a
  first-person render for interiors. They catch doors into the wrong space and screens facing a wall.
- Back up the touched region files (or the save), copy the pack into `<save>/datapacks/`, and report:
  what gets built, the exact chat commands (`/reload`, `/function <ns>:build`, `:visit`, `:undo`), what
  was verified on the copy, the backup path, workspace headroom, the web-page count, and any desk left on
  the default profile.

## Changing a build the player already ran
- **Replacing it:** read the old build's originals from its `plan.json` (or the pre-install backup), and
  have the new `build` run the old `undo` behind a probe: `execute if block <cell only the old build set>`,
  with players teleported out of the old volume first. Pick a probe cell whose state differs from both the
  old original and the new build, so a second build never re-runs the old undo.
- **Fixing it in place:** freeze the old plan, generate a `fix` function of only the cells that change,
  and skip live state (bound stations, pistons, levers). Test the fix on a copy of the live save, then undo.
- **Re-pointing web walls or presets:** ship a tiny separate pack that `data merge`s the block entities,
  with a revert function. Don't rebuild the room for it.

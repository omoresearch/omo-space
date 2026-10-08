# Map build procedure (for Claude Code)

This is the procedure Claude Code follows when the user asks for a map build
with the brief in [`MAPS.md`](MAPS.md). The user pastes the brief; the brief
tells Claude Code to read this file first.

The rule: **the build must not damage the existing base, and `undo` must
restore it exactly.** You deliver a reversible datapack for a save the user
already plays, tested on a copy.

## Safety rules

- Never touch the live bridge, its token (`~/.local/share/omo/token`) or its
  tmux server. Never print, copy or commit the token. No token or home-folder
  path may enter a world, a datapack or git.
- Do not drive the game the user is playing in. Give them the commands.
- Do not restart `omo-tmux` (it kills every live shell) or `omo-bridge`
  (it drops every open screen) without asking.
- Never run the vanilla test server on the live save.

## Tools

Work in `~/dev/minecraft-builds`. It has a venv with `nbtlib`, the read-only
region reader `anvil.py`, and three worked generators to copy:

| Generator | Example of |
| --- | --- |
| `wing/gen_wing.py` | rooms carved underground |
| `office/gen_office.py` | a glass room on the seabed, plus a solid-walled server room |
| `team/gen_team.py` | a glass hall with several named rooms from a `ROOMS` table |

Each has a `verify.py`. Copy the closest one and change the geometry. Keep
its shell, assert, undo and `plan.json` machinery.

## Procedure

1. **Capacity.** `curl -s 127.0.0.1:8780/health` gives `workspaces`. Each new
   station makes one workspace when first opened; the bridge limit is 128.
   If `workspaces + new stations > 128`, stop and ask the user which old
   workspaces to close. A station that cannot get a workspace hangs forever.
2. **Find the save the user plays now.** Several launchers hold copies that
   drift apart. Check the running game
   (`pgrep -af java | grep -oE 'runtime/game|instances/[^/ ]+'`) and the
   newest `level.dat`. Usual places: `~/.local/share/omo/runtime/game/saves/`
   (Omo launcher) and
   `~/.local/share/PrismLauncher/instances/<name>/minecraft/saves/`.
   Regions are in `dimensions/minecraft/overworld/region/`, not `region/`.
3. **Read the save offline** with `anvil.py`. Never write region files.
   Render ASCII slices to find the walls, floor Y and doors of the existing
   build. Probe the dig volume plus 1 block for every non-natural block.
4. **Generate the datapack.** `pack.mcmeta`: `pack_format`, `min_format` and
   `max_format` all `107` (Minecraft 26.2).
   - Carved cells plus a 26-neighbour solid shell around every air or water
     cell, so the sea and caves stay out. Order: shell, water, features, air,
     templates, decor.
   - Assert that the dig overwrites no built block outside the cells you mean
     to change, and that each doorway cell cut into the old build is the
     block you expect.
   - Functions `build`, `undo` and `visit`. Wrap build and undo in
     `forceload add` + `schedule function ... 40t` so every chunk is loaded.
   - `undo` restores the ORIGINAL block states read from the save: interior
     first, shell last.
   - Write `plan.json` (every cell you set) for `verify.py`.
5. **Screens and stations come from structure templates.** `setblock` does
   not join a monitor wall; a template does. Import
   `mod/tools/make_kits.py`, set `make_kits.OUT` to
   `<datapack>/data/<ns>/structure`, draw with `Kit.wall()` and
   `Kit.station()`, and place with `place template <ns>:<name> x y z <rot>`.
   Templates are drawn facing south. Rotation maps (x, z) as: cw90 -> (-z, x),
   180 -> (-x, -z), ccw90 -> (z, -x).
   - **One computer per screen.** A station drives every monitor it touches.
     Hide each computer directly behind its own wall, and assert offline that
     it touches exactly one wall.
   - **Presets, never workspaces.** Each station carries
     `OmoPreset:{Name, Kind:"hermes"|"terminal", Profile, Tabs}`. Set
     `Profile` to a real Hermes profile (`ls ~/.hermes/profiles`). If none
     matches, leave it empty (default profile) and say so in the report.
     Never write a workspace id.
   - **Web walls:** at most 6 pages are live at once in the client. Count
     them across the whole world, not only the new rooms.
6. **Validate block ids.** Extract the names of
   `assets/minecraft/blockstates/*.json` from the 26.2 client jar and diff
   them against every id in the mcfunctions and template palettes. One
   unknown id stops the whole function from loading, and the server logs that
   only once, at startup.
7. **Test on a copy.** Vanilla server `~/Applications/minecraft/server/server.jar`
   with java `~/.local/share/Java/temurin-25/bin/java`. Re-copy the live save
   into the test dir before every test. Feed stdin through a fifo held open
   (`exec 3<>in.fifo`). After `Done (`, grep the log for
   `Failed to load function`. Run build, `save-all flush`, `stop`, then
   `verify.py built`: plan == world, no water in air cells, zero diff in the
   old build's box. Restart, run undo, then `verify.py restored`: zero
   mismatches. Keep diff boxes tight (about 30x25x40).
   - The vanilla server drops modded blocks (omolife, computercraft) from the
     chunks it saves, and it cannot place Omo templates. In the test copy
     those cells read as air. That is a test artefact, not damage. Check the
     template footprints offline instead.
8. **Print an ASCII top-down plan** at eye level (floor + 2) before
   installing. It catches doors into the wrong space and screens that face a
   wall.
9. **Install.** `tar czf` a backup of the live save into
   `~/dev/minecraft-builds/backups/`, then copy the datapack into
   `<save>/datapacks/`.
10. **Report** in plain words: what gets built; the in-game commands
    (`/reload`, `/function <ns>:build`, `/function <ns>:visit`,
    `/function <ns>:undo`); what was verified on the copy; the backup path;
    the workspace headroom left; and any desk left on the default profile.

## New room kits for everyone

To add a kit to the mod (placed with `/omo kit <name>`): add a function to
`mod/tools/make_kits.py`, add its name to `Kits.NAMES` in
`mod/src/main/java/space/omo/life/builder/Kits.java`, run
`python3 mod/tools/make_kits.py`, rebuild the jar with `mod/build.sh`, and
add a row to the kit table in `docs/builders/README.md`. Kits carry presets,
never workspaces.

## Whole demo worlds

`worlds/build_workshop.py` builds through the in-game test driver
(`tests/gamectl.py`) in a running test game, because joining monitor walls by
hand and pinning pages needs a real client. Run it only against the test
instance (`tests/launch-test.sh`), quit to title to flush the save, then run
`python3 worlds/pack_world.py SAVE "worlds/<name>"` to strip player data,
tokens and far chunks.

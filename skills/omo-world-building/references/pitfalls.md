# Pitfalls from real builds

Each line cost someone a failed verify or a broken room. Check the ones that apply before you generate.

## Reading the save
- Region file = block >> 9 (z 263 is in `r.x.0`, not `r.x.8`). The wrong region reads as "not generated".
- Chunk heightmaps include trees. Compute ground ignoring leaves, logs and plants (`anvil.surface`) before
  choosing floor or ceiling Y, or the ceiling ends up in the canopy.
- Match block NAMES, not substrings of states: `waterlogged=false` contains `water`.
- The save lags the game by up to one autosave. For live station bindings read `bridge.log`.
- Find a player's landmark fast by scanning section palettes (e.g. for `minecraft:torch`) or region bytes
  for sign text, then read the block entities. Protect only the landmark, its support and the cell above.
- Sign text in NBT: plain lines are `{'': "text"}`, styled lines `{'text': ...}`. Read both; convert plain
  lines to `{text: "..."}` before writing them back, or the whole function fails to load.
- Don't name a helper `inspect.py` next to your generator: numpy imports the stdlib `inspect` and gets yours.

## Block states the server rewrites
- Plan states must match what the server normalises: `cake` -> `cake[bites=0]`, `jukebox` ->
  `jukebox[has_record=false]`, corner stairs get their shape recomputed. Read the built test world for the
  real state.
- Stairs `facing` = the side of the tall back: a chair facing west uses `facing=east`.
- Glass panes and fences: compute `east/north/south/west` from the final neighbours (pane, glass or full
  block = true; slabs, stairs, plants, doors, air = false).
- Leaves next to new logs or stripped logs change their `distance`. After undo they differ only in
  distance (invisible, self-heals); avoid wood next to leaves if you need a zero diff, or use
  `persistent=true` leaves with a computed distance.
- Copper oxidises on the test world and fails verify: use `waxed_*` ids.
- Template cells read back with server defaults (computer `state=off`, wall sign `waterlogged=false`):
  verify templates by name plus a subset of properties.
- Sign colour must be a dye name (`light_blue`, `cyan`), not a chat colour like `aqua`, or the sign NBT
  fails to decode and the text shows black. Text components use `gray`, not `light_gray`.
- Hanging signs (`*_hanging_sign[attached=false,rotation=N]`) survive `setblock` under a full block; give
  `back_text` the same lines so they read from both sides.

## Things that pop, decay or fall
- Plants on top of changed cells pop: put them in the plan, clear them on build, restore them last.
- Grass under a new solid block turns to dirt while built, underground too. Collect natural `grass_block`
  cells directly under solid plan cells, re-set them at the end of undo, and let verify tolerate
  grass -> dirt only there.
- Floating worldgen gravel drops on any update to a neighbour. Plug only gravel sitting directly over an
  open cell, and keep open cells 2+ away from floating gravel and the air under it.
- Leaves give no support: hang glow berries, cave vines and lanterns from solid blocks or shroomlight. A
  lantern can't hang from a horizontal chain.
- Vines spread on the test world: if the area had none, start undo with `fill ... air replace minecraft:vine`
  in slabs under 32,768 blocks.
- Lily pads go in a phase after their pond water.
- `tar` of a live save exits 1 with "file changed as we read it" (autosave). Don't chain the install on it
  with `&&`.

## Screens, stations and templates
- `setblock` doesn't join a monitor wall; a structure template does. Generate them with
  `mod/tools/make_kits.py`. Templates are drawn facing south; rotation maps (x, z) as cw90 -> (-z, x),
  180 -> (-x, -z), ccw90 -> (z, -x). `Frame.put` y is relative to the frame origin.
- One computer per screen: a station drives every monitor it touches. Hide it under or beside its own wall
  (bottom-middle panel row, or one column over), never on the desk in front of the screen, and keep the
  cell in front of it open so it stays clickable.
- Walls on perpendicular walls must not share a corner column, and two walls on the same wall need a
  1-block gap, or the panels merge into one wall.
- Monitor wall size is capped by CC:T `config/computercraft-server.toml` `[term_sizes.monitor]`, not the mod.
- Never place a template over cells that still hold `omolife:monitor` panels: CC:T keeps the stale
  Width/Height and the wall splits into stretched pieces. Set every omolife cell of the footprint to air,
  top-down, first. (Symptom in the game log: the same URL `web page opened` from 2+ origins on one wall.)
- Decor crossing a template footprint (beams, hidden lights) trips the "template over furniture" assert:
  compute footprints first and skip them. Keep one empty cell between kit chairs and perpendicular stair
  rows, or the corner shape is recomputed.
- Changing kits in a built room: give templates new names (`_v2`) so no cached structure is reused, and
  only touch stations with no workspace bound.
- Functions that mention `omolife:` blocks in `execute if block` fail to LOAD on a vanilla test server.
  Guard with `execute if data block x y z OmoPreset unless data block x y z Workspace run data merge ...`
  so the same pack tests on vanilla and runs live. `data merge` deep-merges compounds.
- Vanilla logs `Unknown registry key omolife:*` for templates: expected. Only `Failed to load function`
  matters.

## Terrain, caves and water
- Room in a hillside: choose floor Y so the ceiling stays under the surface; a cut deck needs retaining
  walls plus one open side.
- Trees in the way: remove whole trees (26-connected logs plus the leaves they reach) and restore logs
  before leaves.
- Fluid margin: 2 rock blocks between OPEN cells and fluid; solid shell cells may touch fluid. Scan water
  and air by y before choosing the floor.
- A shared wall with another build's open space must stay natural on their side: skip skin cells that face
  their open cells.
- Terrain-only edits (blending a build into a hill): fill columns only above the original ground and
  compute ground from solid natural blocks, or roof eaves count as ground.
- Partition columns in a terrain-following cavern must be reserved from any rim knockout, or the wall gets
  holes and a lower ceiling.

## Testing
- Vanilla test servers pause ticking 60 s after start with nobody online: `pause-when-empty-seconds=-1`.
- Entity-only packs test on a minimal copy: `level.dat` + `data/` (without it the seed is random) + the
  needed region/entities/poi files.
- Entities can wander into template cells on the vanilla copy (the omolife blocks are missing there); a
  pig inside a monitor position is not a bug.
- `execute if block` fails in unloaded chunks: `forceload add`, then `schedule` the real work 40-60 ticks
  later. An undo that ends in `forceload remove` needs you to forceload again before the next step.

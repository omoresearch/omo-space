# Mechanisms: doors, lifts, water, display entities

All of these were built and tested on vanilla 26.2 copies first. Test with pigs or armor stands standing in
for players (`summon`, wait, then `data get entity ... Pos` and `Health`), and with a real headless player
(mineflayer, offline mode) when riding matters.

## Fast vertical travel
- **Down:** a drop shaft into a 1-deep water pool (no fall damage). **Up:** `soul_sand` under a
  `bubble_column[drag=false]`. Enter and exit through non-waterlogged wall signs, which hold the water back.
- **Datapack lift:** a cabin of block/text display entities sharing ONE origin (parts placed by their
  transformation), `tp ~ ~dy ~` every tick with `teleport_duration:1` from a precomputed eased table; snap
  to absolute coordinates on arrival. Use decimals (`-2.0`): integer x/z in tp/summon get +0.5.
- Riders sit on a per-rider invisible block_display seat (`ride <e> mount <seat>`); teleporting the seat
  carries them. A player sits 0.6 below the seat. `/tp` of a passenger dismounts it and sneaking dismounts,
  so re-mount each tick from an empty seat. Displays aren't solid: put real floor blocks only at the parked
  level and keep the other level's doorway closed.
- Calls: queue presses (a rising edge sets a flag, served when idle) so a press during a ride is never lost.
  Pressure plates only fetch the cabin; inside buttons call or ride. Wait while someone is still in the
  cabin, with a dwell cap of about 5 s.
- Controls without redstone: poll a button/plate in `tick` with a rising-edge latch:
  `execute if block X Y Z <id>[powered=true] if score #k matches 0 run function <call>` then
  `execute store success score #k if block X Y Z <id>[powered=true]`.

## Doors
- **Hidden 1x2 piston door:** door blocks in the wall face, sticky pistons 2 blocks behind them facing the
  hall, a lever through a redstone-torch inverter, an inside button for a short exit window. Set pistons,
  heads and dust with explicit states; undo the pistons before their torch; list the unchanged door stone
  in the plan so undo restores it if a piston fired.
- A `setblock`'ed lever or button doesn't update the dust behind it: re-place the dust (air, then
  `redstone_wire[...]`) and test by swapping the button's support for a `redstone_block` and back.
- Iron doors: set the lower half, then the upper.
- **Sliding door / doorman as a state machine:** interaction entities polled in `tick` call the same
  function chat or voice commands call; a 1 s cooldown and ignore-while-busy keep it idempotent; before every
  close step, hold the door while `execute if entity @e[...]` finds someone in the doorway (exclude items
  and XP orbs with an entity-type tag).
- Selectors with only y/dy take x/z from the executor (the console is at spawn): use
  `positioned ... if entity @e[distance=..]`.

## Water
- **Walk-through waterfall:** waterlogged BOTTOM slabs at pool level under the curtain. Falling water over a
  waterlogged bottom slab never spreads sideways, so a passage right behind the curtain stays dry. Keep
  every step 0.5 blocks or less. (Pigs swimming up a waterfall are not a leak.)
- **Waterfall off a sloped cliff:** run a 1-wide source channel out on a rock prow so the lip is free air,
  and land it in a 3x3 source basin with a rock rim.
- **Glass skylight under a river:** glass ceiling, then fill the rock up to the riverbed with water sources
  TOP-DOWN on build (gravel falls into water) and restore bottom-up on undo. Take the seagrass and kelp on
  the old bed into the plan too.
- **Shaft through a flooded cave:** give each column a full 3x3 ring so it covers every neighbour; ring
  cells that were water become wall or glass. Undo plugs water cells with stone top-down first.

## Display entities and props
- **Detail smaller than a block** (models, specimens, data viz): tagged `block_display` entities scaled to
  sub-block voxels. No block changes, so undo is `kill @e[tag=<ns>]`; verify by reading the entities region
  files. Keep it to about 1,200 displays.
- **Textured props from a 3D model:** bake orthographic views to PNGs and ship them in a resource pack
  (`pack_format` and `min_format` 88 for 26.2) as item models, shown with `item_display` and
  `minecraft:item_model`. The model's NORTH face is the display's front. Elements are limited to -16..32
  (3 blocks), so scale the display. Animate with `start_interpolation`/`interpolation_duration` on a later
  tick than the summon, each rotation step under 180 degrees.
- `data merge entity` needs a single target: `execute as ... run data merge entity @s`.
- Item kits ("give me the blocks for X"): chat caps commands at 256 characters, so ship a function that
  gives labelled shulker boxes, checking item ids against the jar's item list.

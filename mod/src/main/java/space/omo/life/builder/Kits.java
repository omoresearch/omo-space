package space.omo.life.builder;

import java.util.List;
import java.util.Optional;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Mirror;
import net.minecraft.world.level.block.Rotation;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructurePlaceSettings;
import net.minecraft.world.level.levelgen.structure.templatesystem.StructureTemplate;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;

import space.omo.life.OmoLife;

/**
 * Room kits: structure templates in {@code data/omolife/structure/}, drawn by
 * {@code mod/tools/make_kits.py}. Builders place them with
 * {@code /place template omolife:<kit>}, a structure block, or
 * {@code /omo kit <kit>}, which puts the kit where they look, facing them.
 */
public final class Kits {

    public static final List<String> NAMES = List.of(
        "hermes_desk", "terminal_nook", "web_gallery", "meeting_room", "pocket_stand");

    /** How far {@code /omo kit} looks for the floor. */
    private static final double REACH = 32;

    private Kits() {
    }

    /**
     * Place a kit so its front edge stands on the block the player looks at
     * and faces them. Returns why not, or null once placed.
     */
    public static String place(ServerPlayer player, String name) {
        ServerLevel level = player.level();
        Optional<StructureTemplate> template = NAMES.contains(name)
            ? level.getStructureManager().get(OmoLife.id(name)) : Optional.empty();
        if (template.isEmpty()) {
            return "No kit called '" + name + "'. Kits: " + String.join(", ", NAMES);
        }
        HitResult hit = player.pick(REACH, 0.0F, false);
        if (!(hit instanceof BlockHitResult bh) || hit.getType() == HitResult.Type.MISS) {
            return "Look at the floor where the kit should stand (within " + (int) REACH + " blocks).";
        }
        BlockPos target = bh.getDirection() == Direction.UP ? bh.getBlockPos().above()
            : bh.getBlockPos().relative(bh.getDirection());
        Direction front = player.getDirection().getOpposite();       // the kit looks back at the player
        Rotation rotation = Rotation.NONE;
        for (Rotation r : Rotation.values()) {
            if (r.rotate(Direction.SOUTH) == front) {                 // kits are drawn facing south
                rotation = r;
            }
        }
        StructureTemplate t = template.get();
        BlockPos frontCentre = new BlockPos(t.getSize().getX() / 2, 0, t.getSize().getZ() - 1);
        BlockPos origin = target.subtract(StructureTemplate.transform(frontCentre, Mirror.NONE, rotation, BlockPos.ZERO));
        StructurePlaceSettings settings = new StructurePlaceSettings().setRotation(rotation);
        if (!t.placeInWorld(level, origin, origin, settings, level.getRandom(), Block.UPDATE_CLIENTS)) {
            return "The kit could not be placed there.";
        }
        OmoLife.LOG.info("{} placed kit {} at {} facing {}", player.getPlainTextName(), name, origin.toShortString(),
            front);
        return null;
    }
}

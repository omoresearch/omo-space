package space.omo.life.block;

import java.util.Queue;
import java.util.concurrent.ConcurrentLinkedQueue;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.world.level.Level;

import space.omo.life.mixin.MonitorExpandInvoker;

/**
 * Monitor walls form however their panels get into the world (see
 * {@code MonitorWallMixin}): at the end of the tick a panel arrived in, a
 * lone 1x1 panel with a matching neighbour has CC:T expand it, which joins
 * everything compatible around it into one wall, as a player's placement
 * does. So {@code /fill} of a 4x3 area is one 4x3 wall, and so is a room
 * placed from a structure template.
 *
 * <p>Cheap by design: panels already part of a wall (every panel of a saved
 * wall, when its chunk loads) are skipped at once, and a panel whose
 * neighbours are not all loaded waits for them to arrive and join it
 * instead, so forming never loads a chunk.
 */
public final class WallForming {

    // Filled from block entity lifecycle calls; drained on the server thread.
    private static final Queue<OmoMonitorBlockEntity> PENDING = new ConcurrentLinkedQueue<>();

    private WallForming() {
    }

    public static void register() {
        ServerTickEvents.END_SERVER_TICK.register(server -> drain());
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> PENDING.clear());
    }

    public static void queue(OmoMonitorBlockEntity panel) {
        Level level = panel.getLevel();
        if (level != null && !level.isClientSide()) {
            PENDING.add(panel);
        }
    }

    private static void drain() {
        OmoMonitorBlockEntity panel;
        while ((panel = PENDING.poll()) != null) {
            if (!panel.isRemoved() && panel.getWidth() == 1 && panel.getHeight() == 1 && hasPartner(panel)) {
                ((MonitorExpandInvoker) panel).omolife$expand();
            }
        }
    }

    /** All four neighbours in the wall's plane are loaded, and one is the same kind of panel, facing the same way. */
    private static boolean hasPartner(OmoMonitorBlockEntity panel) {
        Level level = panel.getLevel();
        BlockPos pos = panel.getBlockPos();
        boolean partner = false;
        for (Direction d : new Direction[]{panel.getRight(), panel.getRight().getOpposite(), panel.getDown(),
                                           panel.getDown().getOpposite()}) {
            BlockPos p = pos.relative(d);
            if (level == null || !level.isLoaded(p)) {
                return false;
            }
            partner |= level.getBlockEntity(p) instanceof OmoMonitorBlockEntity other
                && other.getBlockState().getBlock() == panel.getBlockState().getBlock()
                && other.getFront() == panel.getFront() && other.getDirection() == panel.getDirection();
        }
        return partner;
    }
}

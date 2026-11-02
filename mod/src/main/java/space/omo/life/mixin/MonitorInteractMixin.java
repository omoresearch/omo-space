package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlock;
import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

import net.minecraft.core.BlockPos;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;

import space.omo.life.block.OmoMonitorBlock;
import space.omo.life.station.StationComputers;
import space.omo.life.station.Stations;

/**
 * Right-clicking the front of an Omo monitor takes control of the wall;
 * Shift+right-click opens its controls.
 *
 * <p>CC:T declares {@code MonitorBlock.useWithoutItem} final, so the Omo
 * monitor cannot override it; this injection hands the click to
 * {@link OmoMonitorBlock#omoUse} first, and CC:T's own behaviour
 * (monitor_touch) runs when that passes. Placing blocks against a wall still
 * works while crouching with a block in hand: vanilla never asks the block
 * then. On any monitor (CC:T's own too) that a station computer is attached
 * to, CC:T's touch is refused to players who may not use stations.
 */
@Mixin(MonitorBlock.class)
public abstract class MonitorInteractMixin {

    @Inject(method = "useWithoutItem", at = @At("HEAD"), cancellable = true)
    private void omolife$use(BlockState state, Level level, BlockPos pos, Player player,
                             BlockHitResult hit, CallbackInfoReturnable<InteractionResult> cir) {
        if ((Object) this instanceof OmoMonitorBlock block) {
            InteractionResult r = block.omoUse(level, pos, player, hit);
            if (r != InteractionResult.PASS) {
                cir.setReturnValue(r);
                return;
            }
        }
        // What is left is CC:T's monitor_touch. A touch reaches every computer
        // attached to the wall, over wired modems too, and a station turns it
        // into a click in its host terminal: only players who may use stations
        // touch a monitor that a station is attached to (any monitor, CC:T's own included).
        if (player instanceof ServerPlayer sp && level.getBlockEntity(pos) instanceof MonitorBlockEntity panel
            && omolife$station(panel) && !Stations.allowed(sp)) {
            Stations.refuse(sp);
            cir.setReturnValue(InteractionResult.SUCCESS);
        }
    }

    /** A station computer is attached to this panel or to its wall's origin panel (where CC:T queues the touch). */
    @Unique
    private static boolean omolife$station(MonitorBlockEntity panel) {
        boolean[] found = new boolean[1];
        for (MonitorBlockEntity m : new MonitorBlockEntity[]{panel, omolife$origin(panel)}) {
            if (m != null) {
                ((MonitorComputersAccessor) m).omolife$computers().forEach(c -> {
                    if (StationComputers.byComputerId(c.getID()) != null) {
                        found[0] = true;
                    }
                });
            }
        }
        return found[0];
    }

    @Unique
    private static MonitorBlockEntity omolife$origin(MonitorBlockEntity panel) {
        if (panel.getLevel() == null) {
            return null;
        }
        BlockPos origin = panel.getBlockPos()
            .relative(panel.getRight(), -panel.getXIndex())
            .relative(panel.getDown(), -panel.getYIndex());
        return panel.getLevel().getBlockEntity(origin) instanceof MonitorBlockEntity o ? o : null;
    }
}

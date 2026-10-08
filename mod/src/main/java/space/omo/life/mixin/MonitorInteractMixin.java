package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlock;

import net.minecraft.core.BlockPos;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.BlockHitResult;

import space.omo.life.block.OmoMonitorBlock;

/**
 * Right-clicking the front of an Omo monitor takes control of the wall;
 * Shift+right-click opens its controls.
 *
 * <p>CC:T declares {@code MonitorBlock.useWithoutItem} final, so the Omo
 * monitor cannot override it; this injection hands the click to
 * {@link OmoMonitorBlock#omoUse} first, and CC:T's own behaviour
 * (monitor_touch) runs when that passes. Placing blocks against a wall still
 * works while crouching with a block in hand: vanilla never asks the block
 * then. CC:T's own monitors are untouched: the block type is checked first.
 */
@Mixin(MonitorBlock.class)
public abstract class MonitorInteractMixin {

    @Inject(method = "useWithoutItem", at = @At("HEAD"), cancellable = true)
    private void omolife$use(BlockState state, Level level, BlockPos pos, Player player,
                             BlockHitResult hit, CallbackInfoReturnable<InteractionResult> cir) {
        if (!((Object) this instanceof OmoMonitorBlock block)) {
            return;
        }
        InteractionResult r = block.omoUse(level, pos, player, hit);
        if (r != InteractionResult.PASS) {
            cir.setReturnValue(r);
        }
    }
}

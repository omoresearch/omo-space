package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.WallForming;

/**
 * Two rules Omo adds to CC:T's monitor walls.
 *
 * <p>Walls form however their panels arrive. CC:T joins a panel only when a
 * player places it; one that appears through {@code /setblock},
 * {@code /fill}, a structure block, world generation or another mod stays a
 * lone 1x1 screen. Every Omo panel that enters the world (placed, or loaded
 * with its chunk) is handed to {@link WallForming}, which has CC:T form the
 * wall exactly as it does for a player.
 *
 * <p>A wall is one kind of block. CC:T joins any two compatible monitors;
 * an Omo Monitor must not join a Slim Screen or a CC:T monitor beside it,
 * because they are shaped and drawn differently.
 */
@Mixin(MonitorBlockEntity.class)
public abstract class MonitorWallMixin {

    @Inject(method = "clearRemoved", at = @At("TAIL"))
    private void omolife$formWall(CallbackInfo ci) {
        if ((Object) this instanceof OmoMonitorBlockEntity be) {
            WallForming.queue(be);
        }
    }

    @Inject(method = "isCompatible", at = @At("RETURN"), cancellable = true, remap = false)
    private void omolife$sameBlock(MonitorBlockEntity other, CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValueZ()
            && ((MonitorBlockEntity) (Object) this).getBlockState().getBlock() != other.getBlockState().getBlock()) {
            cir.setReturnValue(false);
        }
    }
}

package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

import space.omo.life.block.OmoMonitorBlockEntity;

/**
 * CC:T re-forms a wall (a panel placed or broken) by resizing its new origin
 * panel, which renumbers every panel of the wall. Right after, the Omo
 * monitor gives the whole wall one binding, so a web wall that grows keeps
 * showing its page on every panel.
 */
@Mixin(MonitorBlockEntity.class)
public abstract class MonitorResizeMixin {

    @Inject(method = "resize", at = @At("TAIL"), remap = false)
    private void omolife$unifyWall(int width, int height, CallbackInfo ci) {
        if ((Object) this instanceof OmoMonitorBlockEntity be) {
            be.unifyWall();
        }
    }
}

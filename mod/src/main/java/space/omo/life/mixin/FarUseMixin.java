package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import net.minecraft.client.Minecraft;

import space.omo.life.client.remote.RemoteWall;

/**
 * Right-click reaches a monitor wall across the room: when nothing is within
 * vanilla reach, a wall under the crosshair (up to 24 blocks, in plain
 * sight) is used instead. While a wall is controlled the use key does
 * nothing in the world.
 */
@Mixin(Minecraft.class)
public abstract class FarUseMixin {

    @Inject(method = "startUseItem", at = @At("HEAD"), cancellable = true)
    private void omolife$useWall(CallbackInfo ci) {
        if (RemoteWall.use()) {
            ci.cancel();
        }
    }
}

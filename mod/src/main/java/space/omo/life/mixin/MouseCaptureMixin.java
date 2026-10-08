package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.input.MouseButtonInfo;

import space.omo.life.client.remote.RemoteWall;

/**
 * While the player controls a monitor wall, mouse buttons and the wheel act
 * on the wall at the crosshair instead of attacking, using or changing the
 * hotbar slot. Looking at a web wall, the wheel scrolls its page. Mouse
 * movement is left alone: it turns the camera, which moves the pointer.
 */
@Mixin(MouseHandler.class)
public abstract class MouseCaptureMixin {

    @Inject(method = "onButton", at = @At("HEAD"), cancellable = true)
    private void omolife$buttonToWall(long window, MouseButtonInfo info, int action, CallbackInfo ci) {
        if (window == Minecraft.getInstance().getWindow().handle() && RemoteWall.button(info, action)) {
            ci.cancel();
        }
    }

    @Inject(method = "onScroll", at = @At("HEAD"), cancellable = true)
    private void omolife$scrollToWall(long window, double dx, double dy, CallbackInfo ci) {
        if (window == Minecraft.getInstance().getWindow().handle() && RemoteWall.scroll(dy)) {
            ci.cancel();
        }
    }
}

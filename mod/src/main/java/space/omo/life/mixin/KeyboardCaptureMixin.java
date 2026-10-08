package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import net.minecraft.client.KeyboardHandler;
import net.minecraft.client.Minecraft;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;

import space.omo.life.client.remote.RemoteWall;

/**
 * While the player controls a monitor wall, keys and characters go to the
 * wall before the game binds them to walking, the inventory or chat.
 */
@Mixin(KeyboardHandler.class)
public abstract class KeyboardCaptureMixin {

    @Inject(method = "keyPress", at = @At("HEAD"), cancellable = true)
    private void omolife$keyToWall(long window, int action, KeyEvent event, CallbackInfo ci) {
        if (window == Minecraft.getInstance().getWindow().handle() && RemoteWall.key(event, action)) {
            ci.cancel();
        }
    }

    @Inject(method = "charTyped", at = @At("HEAD"), cancellable = true)
    private void omolife$charToWall(long window, CharacterEvent event, CallbackInfo ci) {
        if (window == Minecraft.getInstance().getWindow().handle() && RemoteWall.chars(event)) {
            ci.cancel();
        }
    }
}

package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import net.minecraft.client.Minecraft;

/**
 * The game window is Omo Space's: the Omo app launched it and hands back
 * to it on exit, so the title says whose window this is.
 */
@Mixin(Minecraft.class)
public abstract class WindowTitleMixin {

    @Inject(method = "createTitle", at = @At("RETURN"), cancellable = true)
    private void omolife$title(CallbackInfoReturnable<String> cir) {
        cir.setReturnValue("Omo Space");
    }
}

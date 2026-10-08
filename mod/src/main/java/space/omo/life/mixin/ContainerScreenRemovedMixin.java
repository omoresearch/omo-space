package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;

import space.omo.life.client.StationScreens;

/**
 * Tear the station panel down when its screen goes away.
 *
 * <p>{@code removed()} is declared on {@link AbstractContainerScreen}, not on
 * CC:T's subclass, and a mixin can only inject into methods its target
 * declares. The registry is keyed by screen, so this is a no-op for every
 * other container screen.
 */
@Mixin(AbstractContainerScreen.class)
public abstract class ContainerScreenRemovedMixin {

    @Inject(method = "removed", at = @At("HEAD"))
    private void omolife$closePanel(CallbackInfo ci) {
        StationScreens.onScreenRemoved((Screen) (Object) this);
    }
}

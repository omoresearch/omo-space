package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import dan200.computercraft.client.gui.widgets.TerminalWidget;
import net.minecraft.client.Minecraft;

import space.omo.life.client.StationScreens;

/**
 * On a station, holding Ctrl+S / Ctrl+R / Ctrl+T must be a shell's Ctrl+S /
 * Ctrl+R / Ctrl+T, not CC:T's "shut down / reboot / terminate this computer"
 * after a second. Plain CC:T computers keep their shortcuts.
 */
@Mixin(value = TerminalWidget.class, remap = false)
public abstract class TerminalShortcutsMixin {

    @Shadow private float terminateTimer;
    @Shadow private float rebootTimer;
    @Shadow private float shutdownTimer;

    @Inject(method = "update", at = @At("HEAD"))
    private void omolife$noHoldShortcuts(CallbackInfo ci) {
        if (StationScreens.forScreen(Minecraft.getInstance().gui.screen()) != null) {
            terminateTimer = rebootTimer = shutdownTimer = -1;
        }
    }
}

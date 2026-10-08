package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import dan200.computercraft.client.gui.AbstractComputerScreen;
import dan200.computercraft.core.terminal.Terminal;

/** Read the client-side terminal buffer of an open computer screen (test harness + diagnostics). */
@Mixin(AbstractComputerScreen.class)
public interface ComputerScreenAccessor {
    @Accessor(value = "terminalData", remap = false)
    Terminal omolife$terminalData();
}

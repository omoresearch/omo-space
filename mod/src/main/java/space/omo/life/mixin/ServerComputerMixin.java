package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Final;
import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import dan200.computercraft.core.terminal.Terminal;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.computer.terminal.NetworkedTerminal;

import space.omo.life.link.TerminalSource;

/**
 * Lets a linked wall read a station computer's screen. CC:T keeps the
 * terminal private and tells only the players with the GUI open when it
 * changed ({@code onTerminalChanged}, once per server tick); this counts
 * those ticks too, so a wall across the room can follow.
 */
@Mixin(ServerComputer.class)
public abstract class ServerComputerMixin implements TerminalSource {

    @Shadow(remap = false)
    @Final
    private NetworkedTerminal terminal;

    @Unique
    private int omolife$version;

    @Inject(method = "onTerminalChanged", at = @At("HEAD"), remap = false)
    private void omolife$changed(CallbackInfo ci) {
        omolife$version++;
    }

    @Override
    public Terminal omolife$terminal() {
        return terminal;
    }

    @Override
    public int omolife$terminalVersion() {
        return omolife$version;
    }
}

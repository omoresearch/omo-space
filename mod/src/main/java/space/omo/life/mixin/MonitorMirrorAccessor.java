package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

/**
 * A linked wall's own CC:T monitor, made on demand: the server monitor and
 * its terminal are normally only built when a computer asks for them
 * through the peripheral, and a linked wall has no peripheral.
 */
@Mixin(MonitorBlockEntity.class)
public interface MonitorMirrorAccessor {
    @Invoker(value = "createServerTerminal", remap = false)
    void omolife$createServerTerminal();
}

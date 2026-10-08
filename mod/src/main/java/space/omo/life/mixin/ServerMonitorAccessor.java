package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

import dan200.computercraft.shared.peripheral.monitor.ServerMonitor;

/** A monitor's text scale, in CC:T's units (half steps: 1 = 0.5, 10 = 5), as {@code setTextScale} sets it. */
@Mixin(ServerMonitor.class)
public interface ServerMonitorAccessor {
    @Invoker(value = "getTextScale", remap = false)
    int omolife$textScale();

    @Invoker(value = "setTextScale", remap = false)
    void omolife$setTextScale(int scale);
}

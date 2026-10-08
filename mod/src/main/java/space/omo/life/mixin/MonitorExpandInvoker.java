package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

/**
 * CC:T's own wall forming: join this panel with every compatible neighbour
 * (what placing a monitor by hand does). Used by {@code WallForming} for
 * panels that arrive any other way.
 */
@Mixin(MonitorBlockEntity.class)
public interface MonitorExpandInvoker {
    @Invoker(value = "expand", remap = false)
    void omolife$expand();
}

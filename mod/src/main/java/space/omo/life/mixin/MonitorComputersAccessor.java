package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import dan200.computercraft.api.peripheral.AttachedComputerSet;
import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

/**
 * The computers CC:T has attached to one monitor panel (through its
 * peripheral). A wall is typed into remotely through these, exactly where
 * CC:T itself queues {@code monitor_touch}.
 */
@Mixin(MonitorBlockEntity.class)
public interface MonitorComputersAccessor {
    @Accessor(value = "computers", remap = false)
    AttachedComputerSet omolife$computers();
}

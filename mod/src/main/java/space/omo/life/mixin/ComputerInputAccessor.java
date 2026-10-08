package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import dan200.computercraft.core.input.ComputerInput;
import dan200.computercraft.core.input.UserComputerInput;

/**
 * Reaches {@code UserComputerInput.delegate}, the raw {@link ComputerInput}
 * underneath.
 *
 * <p>{@code UserComputerInput.mouseScroll} starts with
 * {@code if (mouseSupport) ... else return;} and {@code mouseSupport} is set
 * from {@code terminal.isColour()}. So on a basic (non-Advanced) computer CC:T
 * silently drops every scroll event. Calling the delegate directly queues the
 * {@code mouse_scroll} event regardless of the computer's family.
 */
@Mixin(UserComputerInput.class)
public interface ComputerInputAccessor {

    @Accessor("delegate")
    ComputerInput omolife$delegate();

    @Accessor("mouseSupport")
    boolean omolife$mouseSupport();
}

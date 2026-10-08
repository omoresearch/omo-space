package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Invoker;

import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;

/**
 * Reaches {@code Screen.addRenderableWidget} and {@code removeWidget}, which
 * are protected.
 *
 * <p>{@code addRenderableWidget}'s type variable has several bounds and Java
 * erases to the FIRST one, so the runtime descriptor takes and returns a
 * {@code GuiEventListener}; declaring the invoker with a narrower type finds
 * no target.
 */
@Mixin(Screen.class)
public interface ScreenAccessor {

    @Invoker("addRenderableWidget")
    GuiEventListener omolife$addRenderableWidget(GuiEventListener widget);

    @Invoker("removeWidget")
    void omolife$removeWidget(GuiEventListener widget);
}

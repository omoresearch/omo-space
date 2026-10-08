package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import net.minecraft.client.gui.screens.inventory.AbstractContainerScreen;

/**
 * Reads and moves {@code topPos}, the GUI's top edge, and reads its size.
 *
 * <p>They live on {@link AbstractContainerScreen}, not on CC:T's
 * {@code AbstractComputerScreen}. A {@code @Shadow} only resolves fields
 * declared on the mixin's own target class, so an accessor on the declaring
 * class is required.
 */
@Mixin(AbstractContainerScreen.class)
public interface ContainerScreenAccessor {

    @Accessor("topPos")
    int omolife$topPos();

    @Accessor("topPos")
    void omolife$setTopPos(int top);

    @Accessor("imageHeight")
    int omolife$imageHeight();

    @Accessor("imageWidth")
    int omolife$imageWidth();
}

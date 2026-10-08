package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.screens.Screen;

@Mixin(Screen.class)
public interface ScreenFontAccessor {
    @Accessor("font")
    Font omolife$font();
}

package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.gen.Accessor;

import net.minecraft.commands.CommandSource;
import net.minecraft.commands.CommandSourceStack;

/** Who really issued a command (player, console, command block, function), for {@code CommandGate}. */
@Mixin(CommandSourceStack.class)
public interface CommandSourceAccessor {
    @Accessor("source")
    CommandSource omolife$source();
}

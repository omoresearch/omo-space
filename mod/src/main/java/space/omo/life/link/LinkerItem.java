package space.omo.life.link;

import java.util.function.Consumer;

import net.minecraft.ChatFormatting;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.Identifier;
import net.minecraft.resources.ResourceKey;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.item.component.TooltipDisplay;
import net.minecraft.world.level.Level;

/**
 * The Omo Linker: a builder's wand for wiring screens to stations without
 * wires. Right-click a station to choose it, then right-click screens
 * (monitor walls, name plates, lamps) to link them to it; Shift+right-click
 * a screen unlinks it, Shift+right-click a station opens its setup,
 * Shift+right-click the air forgets the chosen station. The clicks on blocks
 * are handled in {@link Links}, before the block hears of them.
 *
 * <p>The chosen station rides on the stack as custom data (its position,
 * dimension and name; nothing secret), which also makes the Linker shimmer
 * and lets the client draw the links while it is held.
 */
public final class LinkerItem extends Item {

    private static final String NBT = "LinkTo";

    /** The station a Linker has chosen. */
    public record Selection(BlockPos pos, ResourceKey<Level> dimension, String name) {
    }

    public LinkerItem(Properties properties) {
        super(properties);
    }

    public static Selection selection(ItemStack stack) {
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        if (data == null) {
            return null;
        }
        CompoundTag tag = data.copyTag().getCompoundOrEmpty(NBT);
        Identifier dim = Identifier.tryParse(tag.getStringOr("Dim", ""));
        if (tag.isEmpty() || dim == null) {
            return null;
        }
        return new Selection(new BlockPos(tag.getIntOr("X", 0), tag.getIntOr("Y", 0), tag.getIntOr("Z", 0)),
            ResourceKey.create(Registries.DIMENSION, dim), tag.getStringOr("Name", ""));
    }

    public static void select(ItemStack stack, Selection s) {
        if (s == null) {
            stack.remove(DataComponents.CUSTOM_DATA);
            stack.remove(DataComponents.ENCHANTMENT_GLINT_OVERRIDE);
            return;
        }
        CompoundTag tag = new CompoundTag();
        tag.putInt("X", s.pos().getX());
        tag.putInt("Y", s.pos().getY());
        tag.putInt("Z", s.pos().getZ());
        tag.putString("Dim", s.dimension().identifier().toString());
        tag.putString("Name", s.name());
        CompoundTag root = new CompoundTag();
        root.put(NBT, tag);
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(root));
        stack.set(DataComponents.ENCHANTMENT_GLINT_OVERRIDE, true);
    }

    /** Right-click in the air: say what is chosen; with Shift, forget it. */
    @Override
    public InteractionResult use(Level level, Player player, InteractionHand hand) {
        ItemStack stack = player.getItemInHand(hand);
        if (player instanceof ServerPlayer sp) {
            Selection s = selection(stack);
            if (player.isSecondaryUseActive() && s != null) {
                select(stack, null);
                Links.say(sp, "Linker cleared.", false);
            } else {
                Links.say(sp, s == null ? "Right-click a station to choose it, then right-click screens to link them."
                    : "Linking to " + Links.label(s.name(), s.pos()) + " · Shift+right-click the air to clear", true);
            }
        }
        return InteractionResult.SUCCESS;
    }

    // Deprecated in 26.2 in favour of data-driven tooltip components, but it
    // is still the hook for a line computed from the stack's own data.
    @Override
    @SuppressWarnings("deprecation")
    public void appendHoverText(ItemStack stack, TooltipContext context, TooltipDisplay display,
                                Consumer<Component> out, TooltipFlag flag) {
        out.accept(Component.translatable("omolife.tooltip.linker").withStyle(ChatFormatting.GRAY));
        Selection s = selection(stack);
        out.accept(s == null
            ? Component.translatable("omolife.tooltip.linker_empty").withStyle(ChatFormatting.DARK_GRAY)
            : Component.translatable("omolife.tooltip.linker_to", Links.label(s.name(), s.pos()))
                .withStyle(ChatFormatting.GOLD));
    }
}

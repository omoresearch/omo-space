package space.omo.life.pocket;

import java.util.function.Consumer;

import dan200.computercraft.shared.computer.core.ComputerFamily;
import dan200.computercraft.shared.pocket.items.PocketComputerItem;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.minecraft.ChatFormatting;
import net.minecraft.core.Registry;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceKey;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.item.component.TooltipDisplay;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;

/**
 * The Omo Pocket: a CC:T advanced pocket computer that is a whole station
 * from the start, in the Omo colours.
 *
 * <p>Everything a station pocket does (the {@code omo} API, the station
 * screen, the header, the workspace on the item) comes from {@link Pockets},
 * which treats every CC:T pocket computer the same way. What an Omo Pocket
 * adds is its flavour, like the blocks: Hermes (its workspace opens with a
 * Hermes tab, gold) and Terminal (a plain shell, copper).
 */
public final class OmoPocketItem extends PocketComputerItem {

    public static Item HERMES;
    public static Item TERMINAL;

    private final boolean hermes;
    private final String tooltipKey;

    private OmoPocketItem(Properties properties, boolean hermes, String name) {
        super(properties, ComputerFamily.ADVANCED);
        this.hermes = hermes;
        this.tooltipKey = "omolife.tooltip." + name;
    }

    /** Items (the creative tab lists them, see {@code OmoLife}), and forgetting the running pockets when a world closes. */
    public static void register() {
        HERMES = item("pocket_hermes", true);
        TERMINAL = item("pocket_terminal", false);
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> PocketStation.forgetAll());
    }

    private static Item item(String name, boolean hermes) {
        ResourceKey<Item> key = ResourceKey.create(Registries.ITEM, OmoLife.id(name));
        return Registry.register(BuiltInRegistries.ITEM, key,
            new OmoPocketItem(new Item.Properties().setId(key).stacksTo(1), hermes, name));
    }

    public boolean isHermes() {
        return hermes;
    }

    // Deprecated in 26.2 in favour of data-driven tooltip components, but it
    // is still the hook for a line computed from the stack's own data.
    @Override
    @SuppressWarnings("deprecation")
    public void appendHoverText(ItemStack stack, TooltipContext context, TooltipDisplay display,
                                Consumer<Component> out, TooltipFlag flag) {
        out.accept(Component.translatable(tooltipKey).withStyle(ChatFormatting.GRAY));
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        CompoundTag tag = data == null ? new CompoundTag() : data.copyTag();
        String ws = tag.getStringOr(StationBlockEntity.NBT_WS_NAME, "");
        out.accept(ws.isEmpty()
            ? Component.translatable("omolife.tooltip.pocket_new").withStyle(ChatFormatting.DARK_GRAY)
            : Component.translatable("omolife.tooltip.workspace", ws).withStyle(ChatFormatting.GOLD));
    }
}

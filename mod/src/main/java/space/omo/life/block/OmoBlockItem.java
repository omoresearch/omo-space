package space.omo.life.block;

import java.util.function.Consumer;

import net.minecraft.ChatFormatting;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.world.item.BlockItem;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.item.component.BlockItemStateProperties;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.item.component.TooltipDisplay;
import net.minecraft.world.level.block.Block;

/**
 * Item form of the Omo blocks: a one-line description, plus what the item
 * carries (a picked-up station's workspace, a monitor's pinned page, a Slim
 * Screen's frame finish).
 */
public final class OmoBlockItem extends BlockItem {

    private final String tooltipKey;

    public OmoBlockItem(Block block, Properties properties, String name) {
        super(block, properties);
        this.tooltipKey = "omolife.tooltip." + name;
    }

    // Deprecated in 26.2 in favour of data-driven tooltip components, but it
    // is still the hook for a line computed from the stack's own data.
    @Override
    @SuppressWarnings("deprecation")
    public void appendHoverText(ItemStack stack, TooltipContext context, TooltipDisplay display,
                                Consumer<Component> out, TooltipFlag flag) {
        out.accept(Component.translatable(tooltipKey).withStyle(ChatFormatting.GRAY));
        if (getBlock() instanceof SlimScreenBlock) {
            BlockItemStateProperties props = stack.getOrDefault(DataComponents.BLOCK_STATE, BlockItemStateProperties.EMPTY);
            FrameFinish finish = props.get(SlimScreenBlock.FINISH);
            out.accept(Component.translatable("omolife.tooltip.finish",
                Component.translatable("omolife.finish." + (finish == null ? FrameFinish.CREAM : finish).getSerializedName()))
                .withStyle(ChatFormatting.GOLD));
            out.accept(Component.translatable("omolife.tooltip.slim_screen_dye").withStyle(ChatFormatting.DARK_GRAY));
        }
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        if (data == null) {
            return;
        }
        CompoundTag tag = data.copyTag();
        String ws = tag.getStringOr(StationBlockEntity.NBT_WS_NAME, "");
        if (ws.isEmpty()) {
            ws = tag.getStringOr(StationBlockEntity.NBT_WORKSPACE, "");
        }
        if (!ws.isEmpty()) {
            out.accept(Component.translatable("omolife.tooltip.workspace", ws).withStyle(ChatFormatting.GOLD));
        }
        String url = tag.getStringOr("Url", "");
        if (!url.isEmpty()) {
            out.accept(Component.translatable("omolife.tooltip.page", url).withStyle(ChatFormatting.GOLD));
        }
    }
}

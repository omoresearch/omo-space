package space.omo.life.block;

import java.util.Locale;

import net.minecraft.util.StringRepresentable;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.Items;

/**
 * The frame finishes of a Slim Screen, in the Omo palette. A finish is a
 * block state, so it costs nothing to draw: the frame texture is grey and
 * the finish tints it. Right-click a screen with the item to refinish its
 * whole wall.
 */
public enum FrameFinish implements StringRepresentable {
    CREAM(0xFBF7EE, Items.BONE_MEAL),
    ESPRESSO(0x4A3A2F, Items.DYE.brown()),
    GOLD(0xE8B54E, Items.DYE.yellow()),
    COPPER(0xD07A48, Items.DYE.orange()),
    BLACK(0x2C2927, Items.DYE.black()),
    WHITE(0xFFFFFF, Items.DYE.white());

    private final int tint;
    private final Item item;

    FrameFinish(int tint, Item item) {
        this.tint = tint;
        this.item = item;
    }

    /** The colour the frame's brightest pixels take, 0xRRGGBB. */
    public int tint() {
        return tint;
    }

    /** The finish this item applies, or null. */
    public static FrameFinish of(ItemStack stack) {
        for (FrameFinish f : values()) {
            if (stack.is(f.item)) {
                return f;
            }
        }
        return null;
    }

    @Override
    public String getSerializedName() {
        return name().toLowerCase(Locale.ROOT);
    }
}

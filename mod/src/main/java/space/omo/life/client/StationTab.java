package space.omo.life.client;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.gui.narration.NarratedElementType;
import net.minecraft.client.gui.narration.NarrationElementOutput;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.client.sounds.SoundManager;
import net.minecraft.network.chat.Component;

/**
 * One tab of the station header: its state dot, its name, and a close cross
 * that appears on hover. The active tab is filled with the bezel's colour
 * and open at the bottom, so it runs into the frame around the terminal it
 * shows. Click selects; double-click renames; the cross or a middle click
 * closes (the panel asks first).
 */
final class StationTab extends AbstractWidget {

    /** Bezel colour: the active tab and the frame around the terminal share it. */
    static final int FILL = OmoStyle.ESPRESSO;
    private static final int CLOSE_W = 11;

    private final Font font;
    private final int accent;
    private final int dot;
    private final boolean current;
    private final boolean armed;
    private final Runnable onSelect;
    private final Runnable onClose;
    private final Runnable onRename;

    StationTab(Font font, int accent, String name, int dot, boolean current, boolean armed, int x, int y, int h,
               Runnable onSelect, Runnable onClose, Runnable onRename) {
        super(x, y, widthFor(font, name), h, Component.literal(name));
        this.font = font;
        this.accent = accent;
        this.dot = dot;
        this.current = current;
        this.armed = armed;
        this.onSelect = onSelect;
        this.onClose = onClose;
        this.onRename = onRename;
    }

    static int widthFor(Font font, String name) {
        return 6 + 8 + font.width(name) + 3 + CLOSE_W + 1;
    }

    StationTab tip(String text) {
        setTooltip(Tooltip.create(Component.literal(text)));
        return this;
    }

    private boolean overClose(double mx, double my) {
        return mx >= getRight() - CLOSE_W - 1 && mx < getRight() - 1 && my >= getY() && my < getBottom();
    }

    @Override
    protected void extractWidgetRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
        int x0 = getX(), y0 = getY(), x1 = getRight(), y1 = getBottom();
        boolean hover = isHovered();
        if (current) {
            g.fill(x0, y0, x1, y1 + 1, FILL);                       // over the baseline: one surface with the bezel
            g.fill(x0, y0, x1, y0 + 2, accent);
            g.fill(x0, y0 + 2, x0 + 1, y1 + 1, OmoStyle.alpha(accent, 0x90));
            g.fill(x1 - 1, y0 + 2, x1, y1 + 1, OmoStyle.alpha(accent, 0x90));
        } else if (hover) {
            g.fill(x0, y0 + 1, x1, y1 - 1, OmoStyle.mix(OmoStyle.ESPRESSO_DEEP, FILL, 0.7F));
        }
        int ty = y0 + (getHeight() - 7) / 2 + 1;
        OmoStyle.dot(g, x0 + 6, ty + 1, dot);
        int colour = current ? OmoStyle.CREAM_BRIGHT : hover ? OmoStyle.CREAM : OmoStyle.MUTED;
        g.text(font, getMessage().getString(), x0 + 14, ty, colour, false);
        if (hover || armed) {
            boolean hot = hover && overClose(mouseX, mouseY);
            int cx = x1 - CLOSE_W - 1;
            if (hot || armed) {
                g.fill(cx, ty - 2, cx + CLOSE_W, ty + 9, OmoStyle.alpha(OmoStyle.RED, armed ? 0xB0 : 0x60));
            }
            OmoStyle.glyph(g, OmoStyle.Glyph.CROSS, cx + 3, ty + 1, hot || armed ? OmoStyle.CREAM_BRIGHT
                : OmoStyle.FAINT);
        }
    }

    @Override
    protected boolean isValidClickButton(MouseButtonInfo info) {
        return info.button() == 0 || info.button() == 2;
    }

    @Override
    public void onClick(MouseButtonEvent event, boolean doubleClick) {
        if (event.button() == 2 || overClose(event.x(), event.y())) {
            onClose.run();
        } else if (doubleClick) {
            onRename.run();
        } else {
            onSelect.run();
        }
    }

    @Override
    public void playDownSound(SoundManager sounds) {
        OmoStyle.click();
    }

    @Override
    protected void updateWidgetNarration(NarrationElementOutput out) {
        out.add(NarratedElementType.TITLE, Component.literal("Tab " + getMessage().getString()));
    }
}

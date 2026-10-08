package space.omo.life.client;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.Button;
import net.minecraft.client.gui.components.Tooltip;
import net.minecraft.client.sounds.SoundManager;
import net.minecraft.network.chat.Component;

/**
 * A flat Omo button: espresso fill, cream text, a 1px accent rim that
 * brightens on hover. Styles: a plain action, a tab (optional state dot;
 * the active tab gets a lighter fill and a solid accent underline), a
 * danger action (red), a chip (accent-tinted, for the profile), a bare text
 * link, a ghost (no chrome until hovered, for header controls) and a pill
 * (tinted by its dot, for a connection state).
 *
 * <p>Extras: a {@link OmoStyle.Glyph} before the label (or instead of it, see
 * {@link #icon}) and a caret after it, for buttons that open a menu.
 */
public final class OmoButton extends Button {

    public enum Style { ACTION, TAB, DANGER, CHIP, LINK, GHOST, PILL }

    private final Font font;
    private final Style style;
    private final int accent;
    private boolean selected;
    private int dot;
    private OmoStyle.Glyph glyph;
    private int glyphColour;
    private boolean caret;
    private boolean showLabel = true;

    private OmoButton(int x, int y, int w, int h, Component label, OnPress press, Font font, Style style,
                      int accent) {
        super(x, y, w, h, label, press, DEFAULT_NARRATION);
        this.font = font;
        this.style = style;
        this.accent = accent;
    }

    /** A button sized to its label (plus room for a dot on tabs). */
    public static OmoButton of(Font font, Style style, int accent, String label, int x, int y, int h,
                               Runnable action) {
        int w = font.width(label) + (style == Style.LINK ? 2 : 10) + (style == Style.TAB ? 8 : 0);
        return new OmoButton(x, y, w, h, Component.literal(label), b -> action.run(), font, style, accent);
    }

    /** A glyph-only button; {@code name} is what narration and tests read. */
    public static OmoButton icon(Font font, Style style, int accent, OmoStyle.Glyph glyph, String name, int x, int y,
                                 int h, Runnable action) {
        OmoButton b = new OmoButton(x, y, glyph.width() + 8, h, Component.literal(name), p -> action.run(), font,
            style, accent);
        b.glyph = glyph;
        b.glyphColour = OmoStyle.CREAM;
        b.showLabel = false;
        return b;
    }

    public OmoButton tip(String text) {
        setTooltip(Tooltip.create(Component.literal(text)));
        return this;
    }

    public OmoButton selected(boolean on) {
        selected = on;
        return this;
    }

    /** A state dot drawn before the label (tabs); 0 = none. */
    public OmoButton dot(int colour) {
        if (dot == 0 && colour != 0 && style != Style.TAB) {
            setWidth(getWidth() + 8);
        }
        dot = colour;
        return this;
    }

    /** A glyph before the label, in its own colour. */
    public OmoButton glyph(OmoStyle.Glyph g, int colour) {
        setWidth(getWidth() + g.width() + 4);
        glyph = g;
        glyphColour = colour;
        return this;
    }

    /** A small caret after the label: this button opens a menu. */
    public OmoButton caret() {
        setWidth(getWidth() + 8);
        caret = true;
        return this;
    }

    @Override
    protected void extractContents(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
        int x0 = getX(), y0 = getY(), x1 = getRight(), y1 = getBottom();
        boolean hot = isHoveredOrFocused() && active;
        int text = active ? OmoStyle.CREAM : OmoStyle.FAINT;
        switch (style) {
            case LINK -> {
                text = hot ? OmoStyle.CREAM_BRIGHT : OmoStyle.mix(OmoStyle.CREAM, accent, 0.35F);
                if (hot) {
                    g.fill(x0 + 1, y1 - 1, x1 - 1, y1, OmoStyle.alpha(accent, 0xC0));
                }
            }
            case DANGER -> {
                g.fill(x0, y0, x1, y1, hot ? 0xFF6B2A25 : 0xFF4A201C);
                OmoStyle.frame(g, x0, y0, x1, y1, hot ? OmoStyle.RED : OmoStyle.alpha(OmoStyle.RED, 0xB0));
                text = OmoStyle.CREAM_BRIGHT;
            }
            case CHIP -> {
                g.fill(x0, y0, x1, y1, OmoStyle.mix(OmoStyle.ESPRESSO, accent, hot ? 0.34F : 0.22F));
                OmoStyle.frame(g, x0, y0, x1, y1, hot ? accent : OmoStyle.alpha(accent, 0xA0));
                text = OmoStyle.mix(OmoStyle.CREAM_BRIGHT, accent, 0.25F);
            }
            case TAB -> {
                g.fill(x0, y0, x1, y1, selected ? OmoStyle.mix(OmoStyle.ESPRESSO, accent, 0.18F)
                    : hot ? OmoStyle.ESPRESSO_HOVER : OmoStyle.ESPRESSO);
                OmoStyle.frame(g, x0, y0, x1, y1, OmoStyle.alpha(accent, selected || hot ? 0xD0 : 0x55));
                if (selected) {
                    g.fill(x0 + 1, y1 - 2, x1 - 1, y1, accent);
                }
                text = selected ? OmoStyle.CREAM_BRIGHT : hot ? OmoStyle.CREAM : OmoStyle.MUTED;
            }
            case GHOST -> {
                if (hot || selected) {
                    g.fill(x0, y0, x1, y1, OmoStyle.ESPRESSO_HOVER);
                    OmoStyle.frame(g, x0, y0, x1, y1, OmoStyle.alpha(accent, hot ? 0xC0 : 0x70));
                }
                text = hot || selected ? OmoStyle.CREAM_BRIGHT : OmoStyle.CREAM;
            }
            case PILL -> {
                g.fill(x0 + 1, y0, x1 - 1, y1, OmoStyle.mix(OmoStyle.ESPRESSO_DEEP, dot, hot ? 0.28F : 0.16F));
                g.fill(x0, y0 + 1, x0 + 1, y1 - 1, OmoStyle.alpha(dot, 0x90));
                g.fill(x1 - 1, y0 + 1, x1, y1 - 1, OmoStyle.alpha(dot, 0x90));
                g.fill(x0 + 1, y0, x1 - 1, y0 + 1, OmoStyle.alpha(dot, 0x90));
                g.fill(x0 + 1, y1 - 1, x1 - 1, y1, OmoStyle.alpha(dot, 0x90));
                text = OmoStyle.mix(OmoStyle.CREAM_BRIGHT, dot, hot ? 0.2F : 0.35F);
            }
            default -> {
                g.fill(x0, y0, x1, y1, hot ? OmoStyle.ESPRESSO_HOVER : OmoStyle.ESPRESSO);
                OmoStyle.frame(g, x0, y0, x1, y1, OmoStyle.alpha(accent, hot ? 0xF0 : 0x70));
                if (hot) {
                    text = OmoStyle.CREAM_BRIGHT;
                }
            }
        }
        int gw = glyph == null ? 0 : glyph.width() + (showLabel ? 4 : 0);
        int dw = dot != 0 ? 8 : 0;
        int cw = caret ? 8 : 0;
        int room = getWidth() - (style == Style.LINK ? 0 : 6) - gw - dw - cw;
        String label = showLabel ? OmoStyle.fit(font, getMessage().getString(), room) : "";
        int tw = gw + dw + font.width(label) + cw;
        int tx = x0 + (getWidth() - tw) / 2;
        int ty = y0 + (getHeight() - 7) / 2;
        if (glyph != null) {
            OmoStyle.glyph(g, glyph, tx, y0 + (getHeight() - glyph.height()) / 2, hot ? OmoStyle.CREAM_BRIGHT
                : glyphColour);
            tx += gw;
        }
        if (dot != 0) {
            OmoStyle.dot(g, tx, ty + 1, dot);
            tx += 8;
        }
        g.text(font, label, tx, ty, text, false);
        if (caret) {
            OmoStyle.glyph(g, OmoStyle.Glyph.CARET, tx + font.width(label) + 3, ty + 2,
                hot ? OmoStyle.CREAM_BRIGHT : OmoStyle.MUTED);
        }
    }

    @Override
    public void playDownSound(SoundManager sounds) {
        OmoStyle.click();
    }
}

package space.omo.life.client;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.resources.sounds.SimpleSoundInstance;
import net.minecraft.sounds.SoundEvent;
import net.minecraft.sounds.SoundEvents;

/**
 * The Omo palette and a few drawing and sound helpers shared by the station
 * panel and the monitor screen. Same colours as the launcher.
 */
public final class OmoStyle {

    public static final int CREAM = 0xFFF3EFE7;
    public static final int CREAM_BRIGHT = 0xFFFBF7EE;
    public static final int MUTED = 0xFFB3A99C;
    public static final int FAINT = 0xFF7D7268;
    public static final int ESPRESSO = 0xFF2B221C;
    public static final int ESPRESSO_HOVER = 0xFF3B2F27;
    public static final int ESPRESSO_DEEP = 0xFF1A1511;
    public static final int GOLD = 0xFFDDA744;
    public static final int GOLD_LIGHT = 0xFFF7D774;
    public static final int COPPER = 0xFFC8703F;
    public static final int GREEN = 0xFF6FD08C;
    public static final int AMBER = 0xFFF2B04A;
    public static final int RED = 0xFFE5645A;
    public static final int GREY = 0xFF8C837A;

    private OmoStyle() {
    }

    /** Accent for a station: gold for Hermes Computers, copper for Terminals. */
    public static int accent(boolean hermes) {
        return hermes ? GOLD : COPPER;
    }

    /** Same colour at a new alpha (0-255). */
    public static int alpha(int argb, int a) {
        return (a << 24) | (argb & 0x00FFFFFF);
    }

    /** Blend two opaque colours; t=0 gives a, t=1 gives b. */
    public static int mix(int a, int b, float t) {
        int r = (int) (((a >> 16) & 0xFF) * (1 - t) + ((b >> 16) & 0xFF) * t);
        int g = (int) (((a >> 8) & 0xFF) * (1 - t) + ((b >> 8) & 0xFF) * t);
        int bl = (int) ((a & 0xFF) * (1 - t) + (b & 0xFF) * t);
        return 0xFF000000 | (r << 16) | (g << 8) | bl;
    }

    /** A 5x5 round dot whose top-left corner is (x, y). */
    public static void dot(GuiGraphicsExtractor g, int x, int y, int colour) {
        g.fill(x + 1, y, x + 4, y + 5, colour);
        g.fill(x, y + 1, x + 5, y + 4, colour);
    }

    /** A 1px frame inside the rectangle. */
    public static void frame(GuiGraphicsExtractor g, int x0, int y0, int x1, int y1, int colour) {
        g.fill(x0, y0, x1, y0 + 1, colour);
        g.fill(x0, y1 - 1, x1, y1, colour);
        g.fill(x0, y0 + 1, x0 + 1, y1 - 1, colour);
        g.fill(x1 - 1, y0 + 1, x1, y1 - 1, colour);
    }

    /** An espresso card with a soft shadow and a 1px accent rim. */
    public static void card(GuiGraphicsExtractor g, int x0, int y0, int x1, int y1, int accent) {
        g.fill(x0 + 2, y0 + 2, x1 + 2, y1 + 2, 0x66000000);
        g.fill(x0, y0, x1, y1, ESPRESSO_DEEP);
        frame(g, x0, y0, x1, y1, alpha(accent, 0x90));
    }

    /** Small pixel glyphs ('#' = one GUI pixel): crisp at every GUI scale, unlike font symbols. */
    public enum Glyph {
        /** A Hermes workspace or tab: a spark. */
        SPARK("...#...", "...#...", "..###..", "#######", "..###..", "...#...", "...#..."),
        /** A terminal workspace or shell tab: a prompt. */
        PROMPT("#......", ".#.....", "..#....", ".#.....", "#......", ".......", "...####"),
        CARET("#####", ".###.", "..#.."),
        CROSS("#...#", ".#.#.", "..#..", ".#.#.", "#...#"),
        PLUS("..#..", "..#..", "#####", "..#..", "..#.."),
        DOTS("##..##..##", "##..##..##"),
        /** A mouse whose right button is pressed: "right-click". */
        MOUSE(".####.", "#..###", "#..###", "######", "#....#", "#....#", ".####.");

        private final String[] rows;

        Glyph(String... rows) {
            this.rows = rows;
        }

        public int width() {
            return rows[0].length();
        }

        public int height() {
            return rows.length;
        }
    }

    /** The glyph for a workspace or tab kind. */
    public static Glyph kind(boolean hermes) {
        return hermes ? Glyph.SPARK : Glyph.PROMPT;
    }

    /** Draw a glyph with its top-left corner at (x, y), one fill per run of pixels. */
    public static void glyph(GuiGraphicsExtractor g, Glyph glyph, int x, int y, int colour) {
        for (int r = 0; r < glyph.rows.length; r++) {
            String row = glyph.rows[r];
            for (int c = 0; c < row.length(); c++) {
                if (row.charAt(c) != '#') {
                    continue;
                }
                int end = c;
                while (end < row.length() && row.charAt(end) == '#') {
                    end++;
                }
                g.fill(x + c, y + r, x + end, y + r + 1, colour);
                c = end;
            }
        }
    }

    /** Text cut to a width with an ellipsis. */
    public static String fit(Font font, String s, int width) {
        if (font.width(s) <= width) {
            return s;
        }
        String cut = font.plainSubstrByWidth(s, Math.max(0, width - font.width("…")));
        return cut + "…";
    }

    // -- sounds: quiet, client-side, never spammy --------------------------------------

    public static void click() {
        play(SoundEvents.UI_BUTTON_CLICK.value(), 1.6F, 0.22F);
    }

    public static void chime() {
        play(SoundEvents.AMETHYST_BLOCK_CHIME, 1.25F, 0.55F);
    }

    public static void lowNote() {
        play(SoundEvents.NOTE_BLOCK_BASS.value(), 0.6F, 0.45F);
    }

    private static void play(SoundEvent sound, float pitch, float volume) {
        Minecraft.getInstance().getSoundManager().play(SimpleSoundInstance.forUI(sound, pitch, volume));
    }
}

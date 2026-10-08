package space.omo.life.client.remote;

import java.util.HashMap;
import java.util.Map;

import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.core.BlockPos;

import space.omo.life.client.OmoStyle;

/**
 * The HUD for remote walls, kept quiet. While controlling one: a slim pill at
 * the top, "Typing into <workspace> · Esc lets go". While only looking at
 * one: after the crosshair has rested on it for a moment, a small faint
 * right-click glyph beside the crosshair that fades within two seconds, once
 * per wall until the player has looked elsewhere for a minute. Discoverable
 * once, never nagging. (Shift+right-click still opens a wall's settings.)
 */
final class RemoteWallHud {

    private static final long DWELL_MS = 500;
    private static final long SHOW_MS = 2000;
    private static final long FADE_IN_MS = 200;
    private static final long FADE_OUT_MS = 700;
    private static final long AWAY_MS = 60_000;

    /** Per wall origin: {when the crosshair was last on it, when its hint began this visit (0 = not yet)}. */
    private static final Map<BlockPos, long[]> VISITS = new HashMap<>();

    private RemoteWallHud() {
    }

    static void extract(GuiGraphicsExtractor g, DeltaTracker delta) {
        Minecraft mc = Minecraft.getInstance();
        if (mc.gui.screen() != null) {
            return;
        }
        RemoteWall.Control c = RemoteWall.active();
        WallPointer.Hit look = RemoteWall.pointer();
        if (c != null) {
            pill(g, mc.font, c, look);
        } else if (look != null) {
            hint(g, mc.font, look, RemoteWall.lookingFor());
        }
    }

    private static void pill(GuiGraphicsExtractor g, Font font, RemoteWall.Control c, WallPointer.Hit p) {
        int accent = c.web() ? OmoStyle.GOLD : OmoStyle.accent(c.hermes());
        String what = (c.web() ? "Browsing " : "Typing into ") + (c.label().isEmpty() ? "this wall" : c.label());
        String how = "  ·  Esc lets go";
        what = OmoStyle.fit(font, what, g.guiWidth() - font.width(how) - 40);
        int w = 15 + font.width(what) + font.width(how) + 6;
        int x = (g.guiWidth() - w) / 2;
        int y = 4;
        g.fill(x, y, x + w, y + 13, OmoStyle.alpha(OmoStyle.ESPRESSO_DEEP, 0xC0));
        g.fill(x, y + 12, x + w, y + 13, OmoStyle.alpha(accent, 0xA0));
        OmoStyle.dot(g, x + 5, y + 4, p != null && p.onGlass() ? OmoStyle.GREEN : OmoStyle.FAINT);
        g.text(font, what, x + 15, y + 2, OmoStyle.CREAM, false);
        g.text(font, how, x + 15 + font.width(what), y + 2, OmoStyle.MUTED, false);
    }

    private static void hint(GuiGraphicsExtractor g, Font font, WallPointer.Hit look, long dwell) {
        long now = System.currentTimeMillis();
        BlockPos wall = look.wall().getBlockPos();
        long[] visit = VISITS.get(wall);
        if (visit == null || now - visit[0] > AWAY_MS) {
            VISITS.values().removeIf(v -> now - v[0] > AWAY_MS);
            visit = new long[]{now, 0};
            VISITS.put(wall.immutable(), visit);
        }
        visit[0] = now;
        if (visit[1] == 0 && dwell >= DWELL_MS) {
            visit[1] = now;
        }
        long t = visit[1] == 0 ? SHOW_MS : now - visit[1];
        if (t >= SHOW_MS) {
            return;
        }
        float fade = Math.min(1F, t / (float) FADE_IN_MS) * Math.min(1F, (SHOW_MS - t) / (float) FADE_OUT_MS);
        if (fade * 0xD0 < 8) {
            return;                                  // text alpha this low would draw opaque
        }
        String text = look.wall().isWeb() ? "use · scroll" : "type";
        int x = g.guiWidth() / 2 + 7;
        int y = g.guiHeight() / 2 + 5;
        int w = 14 + font.width(text) + 5;
        g.fill(x, y, x + w, y + 12, OmoStyle.alpha(OmoStyle.ESPRESSO_DEEP, (int) (fade * 0x90)));
        OmoStyle.glyph(g, OmoStyle.Glyph.MOUSE, x + 4, y + 3, OmoStyle.alpha(OmoStyle.CREAM, (int) (fade * 0xB0)));
        g.text(font, text, x + 14, y + 2, OmoStyle.alpha(OmoStyle.MUTED, (int) (fade * 0xD0)), false);
    }
}

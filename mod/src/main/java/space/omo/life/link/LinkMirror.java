package space.omo.life.link;

import java.util.ArrayList;
import java.util.HashMap;
import java.util.Map;

import dan200.computercraft.core.terminal.Palette;
import dan200.computercraft.core.terminal.Terminal;
import dan200.computercraft.core.terminal.TextBuffer;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.peripheral.monitor.ServerMonitor;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.core.BlockPos;
import net.minecraft.resources.ResourceKey;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.world.level.Level;

import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.mixin.MonitorMirrorAccessor;
import space.omo.life.mixin.ServerMonitorAccessor;

/**
 * Linked walls show their station's screen, done on the server: each tick
 * the station computer's terminal changed, it is copied into the wall's own
 * CC:T monitor terminal, and CC:T sends that to the players who can see the
 * wall, exactly as it does for any monitor a program draws on.
 *
 * <p>So a linked wall needs no connection to the host of its own, and any
 * number of walls can show one station: they all show the computer's own
 * screen (the tab, the status bar, the cursor), at the largest text that
 * fits the wall, centred (a station screen is 80x30: a 5x3 wall holds it
 * whole). A wall too small for that shows, at the smallest text, the part of
 * the screen around the cursor, with the status bar kept on its last row.
 * When the station is off or gone, the wall says so.
 *
 * <p>An adjacent wall is different (TERMINAL's two views): station.lua
 * draws it as a second view of the tab at the wall's own size. A linked
 * wall is no peripheral, so a station never draws on it; both kinds can
 * show one station at once.
 */
public final class LinkMirror {

    /** Linked panels per dimension; wall origins keep what they show. */
    private static final Map<ResourceKey<Level>, Map<BlockPos, Frame>> WALLS = new HashMap<>();

    /** The station's background slot (espresso under the Omo palette). */
    private static final char BG = 'f';

    /**
     * Where a station screen of {@code cols x rows} sits on a wall of
     * {@code height} rows: station cell (1, 1) is wall cell (1 + ox, 1 + oy).
     * Offsets are negative when the wall shows only part of the screen; then
     * ({@code clipped}) the wall's last row is the station's status bar.
     */
    public record Layout(int ox, int oy, int cols, int rows, int height, boolean clipped) {
        /** The station row (0-based) a wall row shows, or -1 for none. */
        int rowFor(int wallRow) {
            int sy = clipped && wallRow == height - 1 ? rows - 1 : wallRow - oy;
            return sy >= 0 && sy < rows && !(clipped && sy == rows - 1 && wallRow != height - 1) ? sy : -1;
        }

        /** The station cell under a wall cell (both 1-based), kept on the screen. */
        public int[] toStation(int x, int y) {
            int sy = rowFor(Math.max(1, Math.min(height, y)) - 1);
            return new int[]{Math.max(1, Math.min(cols, x - ox)), sy < 0 ? Math.max(1, Math.min(rows, y - oy)) : sy + 1};
        }
    }

    private static final class Frame {
        ServerComputer computer;
        int version = -1;
        int width = -1;
        int height = -1;
        String card;
        Layout layout;
    }

    private LinkMirror() {
    }

    public static void register() {
        ServerTickEvents.END_SERVER_TICK.register(LinkMirror::tick);
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> WALLS.clear());
    }

    /** A linked panel is loaded (or was just linked). Server thread. */
    public static void track(OmoMonitorBlockEntity panel) {
        WALLS.computeIfAbsent(panel.getLevel().dimension(), k -> new HashMap<>())
            .computeIfAbsent(panel.getBlockPos().immutable(), k -> new Frame());
    }

    public static void untrack(OmoMonitorBlockEntity panel) {
        Map<BlockPos, Frame> walls = WALLS.get(panel.getLevel().dimension());
        if (walls != null) {
            walls.remove(panel.getBlockPos());
        }
    }

    /** A wall was unlinked: blank its glass and give it back to CC:T's default text size. */
    public static void release(OmoMonitorBlockEntity panel) {
        untrack(panel);
        if (!panel.isOrigin()) {
            return;
        }
        ServerMonitor monitor = panel.getCachedServerMonitor();
        Terminal term = monitor == null ? null : monitor.getTerminal();
        if (term != null) {
            term.reset();
            ((ServerMonitorAccessor) monitor).omolife$setTextScale(2);
        }
    }

    /** How the station's screen lies on this linked wall right now, or null (nothing mirrored yet). */
    public static Layout layout(OmoMonitorBlockEntity origin) {
        Map<BlockPos, Frame> walls = WALLS.get(origin.getLevel().dimension());
        Frame f = walls == null ? null : walls.get(origin.getBlockPos());
        return f == null || f.card != null ? null : f.layout;
    }

    private static void tick(MinecraftServer server) {
        for (Map.Entry<ResourceKey<Level>, Map<BlockPos, Frame>> dim : WALLS.entrySet()) {
            ServerLevel level = server.getLevel(dim.getKey());
            if (level == null) {
                continue;
            }
            for (BlockPos pos : new ArrayList<>(dim.getValue().keySet())) {
                if (!level.isLoaded(pos)) {
                    continue;
                }
                if (!(level.getBlockEntity(pos) instanceof OmoMonitorBlockEntity wall) || !wall.isLinked()) {
                    dim.getValue().remove(pos);
                } else if (wall.isOrigin()) {
                    mirror(level, wall, dim.getValue().get(pos));
                }
            }
        }
    }

    private static void mirror(ServerLevel level, OmoMonitorBlockEntity wall, Frame f) {
        StationBlockEntity station = wall.station();
        if (station == null) {
            if (level.isLoaded(wall.linkedStation())) {         // unloaded: keep the last picture
                card(wall, f, "lost", "Link lost", "link it again with the Omo Linker");
            }
            return;
        }
        ServerComputer computer = station.getServerComputer();
        if (computer == null || !computer.isOn()) {
            card(wall, f, "off " + station.displayName(), station.displayName(), "off · right-click the station");
            return;
        }
        Terminal src = ((TerminalSource) computer).omolife$terminal();
        Terminal dst = glass(wall, fitScale(wall, src.getWidth(), src.getHeight()));
        int version = ((TerminalSource) computer).omolife$terminalVersion();
        if (dst == null || f.card == null && f.computer == computer && f.version == version
            && f.width == dst.getWidth() && f.height == dst.getHeight()) {
            return;
        }
        f.layout = copy(src, dst);
        f.computer = computer;
        f.version = version;
        f.width = dst.getWidth();
        f.height = dst.getHeight();
        f.card = null;
    }

    /** The wall's own terminal at {@code scale}, built if CC:T has not yet. */
    private static Terminal glass(OmoMonitorBlockEntity wall, int scale) {
        ServerMonitor monitor = wall.getCachedServerMonitor();
        if (monitor == null || monitor.getTerminal() == null) {
            ((MonitorMirrorAccessor) wall).omolife$createServerTerminal();
            monitor = wall.getCachedServerMonitor();
        }
        if (monitor == null) {
            return null;
        }
        ((ServerMonitorAccessor) monitor).omolife$setTextScale(scale);
        return monitor.getTerminal();
    }

    /** The largest text (CC:T half steps, 1..10) at which the wall holds {@code cols x rows}; else the smallest. */
    static int fitScale(OmoMonitorBlockEntity wall, int cols, int rows) {
        for (int s = 10; s > 1; s--) {
            if (cells(wall.getWidth(), s, 6) >= cols && cells(wall.getHeight(), s, 9) >= rows) {
                return s;
            }
        }
        return 1;
    }

    /** Characters across {@code blocks} of glass at a scale, as CC:T's ServerMonitor counts them. */
    private static int cells(int blocks, int scale, int charPixels) {
        return (int) Math.max(1, Math.round((blocks - 0.3125) / (scale * 0.5 * charPixels / 64.0)));
    }

    /** Copy the whole screen: text, colours, palette and cursor. */
    private static Layout copy(Terminal src, Terminal dst) {
        synchronized (src) {                              // the computer thread writes it
            int cols = src.getWidth(), rows = src.getHeight();
            int w = dst.getWidth(), h = dst.getHeight();
            int cx = src.getCursorX(), cy = src.getCursorY();
            // Centred when it fits; else a window that keeps the cursor in view (above the status bar).
            int ox = w >= cols ? (w - cols) / 2 : -Math.max(0, Math.min(cols - w, cx - w + 1));
            int oy = h >= rows ? (h - rows) / 2 : -Math.max(0, Math.min(rows - h, cy - (h - 1) + 1));
            Layout layout = new Layout(ox, oy, cols, rows, h, h < rows);
            for (int y = 0; y < h; y++) {
                TextBuffer text = dst.getLine(y), fg = dst.getTextColourLine(y), bg = dst.getBackgroundColourLine(y);
                text.fill(' ');
                fg.fill('0');
                bg.fill(BG);
                int sy = layout.rowFor(y);
                if (sy >= 0) {
                    text.write(src.getLine(sy).toString(), ox);
                    fg.write(src.getTextColourLine(sy).toString(), ox);
                    bg.write(src.getBackgroundColourLine(sy).toString(), ox);
                }
            }
            for (int i = 0; i < Palette.PALETTE_SIZE; i++) {
                double[] c = src.getPalette().getColour(i);
                dst.getPalette().setColour(i, c[0], c[1], c[2]);
            }
            int wx = cx + ox, wy = cy == rows - 1 && layout.clipped() ? h - 1 : cy + oy;
            dst.setCursorPos(wx, wy);
            dst.setCursorBlink(src.getCursorBlink() && wx >= 0 && wx < w && wy >= 0 && wy < h);
            dst.setTextColour(src.getTextColour());
            dst.setChanged();
            return layout;
        }
    }

    /** A still card instead of the screen: the Omo mark, a title and a hint, centred. */
    private static void card(OmoMonitorBlockEntity wall, Frame f, String what, String title, String hint) {
        String key = what + " " + wall.getWidth() + "x" + wall.getHeight();
        if (key.equals(f.card)) {
            return;
        }
        int scale = 1;
        for (int s = 4; s > 1 && scale == 1; s--) {
            if (cells(wall.getWidth(), s, 6) >= 30 && cells(wall.getHeight(), s, 9) >= 5) {
                scale = s;
            }
        }
        Terminal t = glass(wall, scale);
        if (t == null) {
            return;
        }
        t.reset();                                        // blank, on the background slot
        palette(t, 0xFBF7EE, '0');
        palette(t, 0xDDA744, '1');
        palette(t, 0x7D7268, '7');
        palette(t, 0x1A1511, BG);
        String[][] lines = {{"Omo", "1"}, {title.isEmpty() ? "Omo station" : title, "0"}, {hint, "7"}};
        int top = Math.max(0, (t.getHeight() - lines.length) / 2);
        for (int i = 0; i < lines.length && top + i < t.getHeight(); i++) {
            String text = lines[i][0].length() > t.getWidth() ? lines[i][0].substring(0, t.getWidth()) : lines[i][0];
            int x = (t.getWidth() - text.length()) / 2;
            t.getLine(top + i).write(text, x);
            t.getTextColourLine(top + i).write(lines[i][1].repeat(text.length()), x);
        }
        t.setCursorBlink(false);
        t.setChanged();
        f.card = key;
        f.computer = null;
    }

    private static void palette(Terminal t, int rgb, char slot) {
        double[] c = Palette.decodeRGB8(rgb);
        t.getPalette().setColour(15 - Character.digit(slot, 16), c[0], c[1], c[2]);
    }
}

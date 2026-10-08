package space.omo.life.client;

import java.util.HashMap;
import java.util.Map;

import com.mojang.blaze3d.platform.Window;

import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.Screen;

import space.omo.life.net.StationPackets;

/** Open station panels by screen, and the GUI scale a station screen borrows while it is open. */
public final class StationScreens {
    private static final Map<Screen, StationPanel> BY_SCREEN = new HashMap<>();

    /** True while a station screen runs at a smaller GUI scale than the player's. */
    private static boolean scaledDown;

    private StationScreens() {
    }

    /** A (re)initialised screen's panel; the panel it had before stops. */
    public static void register(Screen screen, StationPanel panel) {
        StationPanel old = BY_SCREEN.put(screen, panel);
        if (old != null && old != panel) {
            old.close();
        }
    }

    public static StationPanel forScreen(Screen screen) {
        return BY_SCREEN.get(screen);
    }

    /**
     * Called when any container screen is removed (closed or replaced), never
     * on a mere re-init: the panel stops and the player's GUI scale comes back.
     */
    public static void onScreenRemoved(Screen screen) {
        StationPanel p = BY_SCREEN.remove(screen);
        if (p != null) {
            p.close();
        }
        if (scaledDown) {
            scaledDown = false;
            Minecraft mc = Minecraft.getInstance();
            mc.execute(mc::resizeGui);          // back to the player's own GUI scale
        }
    }

    /**
     * Lower the GUI scale, for this screen only, until {@code needW x needH}
     * GUI pixels fit the window. Never raises it; never touches the saved option.
     */
    public static void fitGuiScale(Screen screen, int needW, int needH) {
        Window window = Minecraft.getInstance().getWindow();
        int scale = window.getGuiScale();
        int fit = scale;
        while (fit > 1 && (window.getWidth() / fit < needW || window.getHeight() / fit < needH)) {
            fit--;
        }
        if (fit != scale) {
            window.setGuiScale(fit);
            screen.width = window.getGuiScaledWidth();
            screen.height = window.getGuiScaledHeight();
            scaledDown = true;
        }
    }

    public static void onInfo(StationPackets.Info info) {
        for (StationPanel p : BY_SCREEN.values()) {
            if (p.station().equals(info.station())) {
                p.onStationInfo(info);
            }
        }
    }
}

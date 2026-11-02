package space.omo.life.client;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.screens.ConfirmLinkScreen;
import net.minecraft.client.gui.screens.Screen;

import space.omo.life.station.BridgeLink;

/**
 * Why stations on this computer cannot reach the bridge, in words a player
 * can act on, plus the way to the setup guide.
 *
 * <p>Three problems, checked in this order: no bridge token (the bridge was
 * never installed), CC: Tweaked's HTTP rules block 127.0.0.1 (a store
 * install without {@code omo_setup.py link}), and the bridge not answering
 * (installed, not running). The guide opens through vanilla's
 * {@link ConfirmLinkScreen}; the mod never downloads or runs anything.
 */
public final class BridgeHelp {

    public record Problem(String title, String detail) {
    }

    private static final long HEALTH_EVERY_MS = 5_000;
    private static final long RULE_EVERY_MS = 5_000;

    private static volatile Boolean healthy;
    private static volatile long healthAskedAt;
    private static volatile CcHttpRule.State rule;
    private static volatile long ruleReadAt;

    private BridgeHelp() {
    }

    /** The current problem, or null when stations should connect. Cheap; call it every frame. */
    public static Problem problem() {
        if (!BridgeLink.available()) {
            return new Problem("The Omo Space bridge is not installed on this computer.",
                "Terminals need the free Omo Space bridge, a small program on this computer (127.0.0.1 only). "
                    + "The setup guide shows how to install it for CurseForge, Modrinth or Prism.");
        }
        if (!CcHttpRule.allows(ruleState())) {
            return new Problem("CC: Tweaked is blocking the bridge.",
                "config/computercraft-server.toml has no allow rule for 127.0.0.1 port " + BridgeLink.port()
                    + ". Run `omo_setup.py link --game-dir <this instance>` (see the guide), then restart the game.");
        }
        askHealth();
        if (Boolean.FALSE.equals(healthy)) {
            return new Problem("The Omo Space bridge is not running.",
                "Nothing answers on 127.0.0.1:" + BridgeLink.port() + ". Start it (Linux: systemctl --user start "
                    + "omo-tmux omo-bridge) or see the setup guide.");
        }
        return null;
    }

    /** Open the setup guide, asking first (vanilla's link confirmation), then come back to {@code parent}. */
    public static void openGuide(Screen parent) {
        ConfirmLinkScreen.confirmLinkNow(parent == null ? Minecraft.getInstance().gui.screen() : parent,
            BridgeLink.SETUP_GUIDE);
    }

    private static CcHttpRule.State ruleState() {
        long now = System.currentTimeMillis();
        CcHttpRule.State s = rule;
        if (s == null || now - ruleReadAt > RULE_EVERY_MS) {
            Path file = FabricLoader.getInstance().getConfigDir().resolve("computercraft-server.toml");
            String text;
            try {
                text = Files.readString(file, StandardCharsets.UTF_8);
            } catch (IOException e) {
                text = null;
            }
            s = CcHttpRule.state(text, BridgeLink.port());
            rule = s;
            ruleReadAt = now;
        }
        return s;
    }

    private static void askHealth() {
        long now = System.currentTimeMillis();
        if (now - healthAskedAt < HEALTH_EVERY_MS) {
            return;
        }
        healthAskedAt = now;
        BridgeLink.health().thenAccept(ok -> healthy = ok);
    }
}

package space.omo.life.client;

import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.brigadier.context.CommandContext;

import net.fabricmc.fabric.api.client.command.v2.ClientCommandRegistrationCallback;
import net.fabricmc.fabric.api.client.command.v2.ClientCommands;
import net.fabricmc.fabric.api.client.command.v2.FabricClientCommandSource;
import net.fabricmc.fabric.api.client.rendering.v1.hud.HudElementRegistry;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.ChatFormatting;
import net.minecraft.client.DeltaTracker;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.network.chat.Component;
import net.minecraft.util.FormattedCharSequence;

import space.omo.life.OmoLife;

/**
 * Hermes agent notices in the HUD, so a {@code notify} is seen during play,
 * not only inside a station panel.
 *
 * <p>The bridge sends {@code {"t":"notice","m":text,"profile":p,"level":l}} to
 * every ui-role connection (next to the older {@code toast}, which the station
 * panel keeps showing). While the player is in a world this class keeps the ui
 * link up, queues notices in a {@link NoticeQueue} and draws them top-left as
 * small Omo cards when no screen is open. Plain bridge toasts (copies, restored
 * terminals) never reach the HUD.
 *
 * <p>Off with {@code hermesNotices=false} in config/omolife-client.properties
 * or {@code /omonotices off} (a client command: {@code /omo} itself belongs to
 * the server).
 */
public final class HermesNotices {

    private static final Path CONFIG = FabricLoader.getInstance().getConfigDir().resolve("omolife-client.properties");
    /** How often the ui link is checked while in a world (ticks). */
    private static final int LINK_EVERY = 100;
    private static final long CHIME_GAP_MS = 2000L;

    private static final int WIDTH = 200;
    private static final int PAD = 8;
    private static final int LINE = 10;
    private static final int MAX_LINES = 2;
    /** Agent Toast (a separate user mod) draws 250-wide cards at the same corner; sit beside them. */
    private static final int AGENT_TOAST_W = 250;

    /** The Hermes wing (station.lua's WING at HUD size), one GUI pixel per '#'. */
    private static final String[] WING = {
        "..........##",
        "........####",
        ".######.###.",
        "..#####.##..",
        "........#...",
        "...####.#...",
        "....###.....",
    };

    private static final NoticeQueue QUEUE = new NoticeQueue();
    private static boolean enabled = true;
    private static int ticks;
    private static long lastChime;
    private static int left = 6;

    private HermesNotices() {
    }

    public static void init() {
        enabled = NoticeQueue.readEnabled(CONFIG);
        if (!Files.exists(CONFIG)) {
            NoticeQueue.writeEnabled(CONFIG, true);       // so the toggle is there to find
        }
        if (FabricLoader.getInstance().isModLoaded("agenttoast")) {
            left = 6 + AGENT_TOAST_W + 6;
        }
        BridgeClient.get().addListener(HermesNotices::onBridge);
        HudElementRegistry.addLast(OmoLife.id("hermes_notices"), HermesNotices::render);
        ClientCommandRegistrationCallback.EVENT.register((dispatcher, ctx) -> dispatcher.register(
            ClientCommands.literal("omonotices")
                .executes(c -> status(c))
                .then(ClientCommands.literal("on").executes(c -> set(c, true)))
                .then(ClientCommands.literal("off").executes(c -> set(c, false)))
                .then(ClientCommands.literal("test").executes(HermesNotices::test))));
    }

    /** Client tick: keep the ui link up while in a world, so notices arrive with no panel open. */
    public static void tick(Minecraft mc) {
        if (++ticks % LINK_EVERY != 0 || !enabled || mc.level == null) {
            return;
        }
        BridgeClient.get().ensureConnected();             // no-op when connected; never more than once a second
    }

    public static boolean enabled() {
        return enabled;
    }

    private static void onBridge(JsonObject msg) {
        if (!enabled || !"notice".equals(str(msg, "t"))) {
            return;
        }
        Minecraft mc = Minecraft.getInstance();
        if (mc.gui.screen() != null && StationScreens.forScreen(mc.gui.screen()) != null) {
            return;                                       // the panel shows the matching toast
        }
        if (QUEUE.add(str(msg, "profile"), str(msg, "m"), str(msg, "level"), System.currentTimeMillis())) {
            long now = System.currentTimeMillis();
            if (now - lastChime > CHIME_GAP_MS) {
                lastChime = now;
                OmoStyle.softChime();
            }
        }
    }

    private static String str(JsonObject o, String k) {
        JsonElement e = o.get(k);
        return e != null && e.isJsonPrimitive() ? e.getAsString() : "";
    }

    // -- the command ---------------------------------------------------------------

    private static int status(CommandContext<FabricClientCommandSource> c) {
        c.getSource().sendFeedback(Component.literal("Hermes notices are " + (enabled ? "on" : "off")
            + " (/omonotices on|off|test)").withStyle(ChatFormatting.GOLD));
        return 1;
    }

    private static int set(CommandContext<FabricClientCommandSource> c, boolean on) {
        enabled = on;
        if (!on) {
            QUEUE.clear();
        }
        NoticeQueue.writeEnabled(CONFIG, on);
        return status(c);
    }

    private static int test(CommandContext<FabricClientCommandSource> c) {
        if (!enabled) {
            return status(c);
        }
        QUEUE.add("hermes", "Notices show up here while you play.", "success", System.currentTimeMillis());
        OmoStyle.softChime();
        return 1;
    }

    // -- drawing -------------------------------------------------------------------

    private static void render(GuiGraphicsExtractor g, DeltaTracker delta) {
        if (QUEUE.isEmpty()) {
            return;
        }
        Minecraft mc = Minecraft.getInstance();
        long now = System.currentTimeMillis();
        List<NoticeQueue.Notice> showing = QUEUE.visible(now);
        if (showing.isEmpty() || mc.gui.screen() != null) {
            return;                                       // a screen hides them; they keep ageing
        }
        Font font = mc.font;
        int y = 6;
        for (NoticeQueue.Notice n : showing) {
            y += draw(g, font, n, now, left, y) + 4;
        }
    }

    private static int accent(NoticeQueue.Level level) {
        return switch (level) {
            case SUCCESS -> OmoStyle.GREEN;
            case WARN -> OmoStyle.AMBER;
            case INFO -> OmoStyle.GOLD;
        };
    }

    /** One card: espresso, accent rim and stripe, wing + profile in gold, cream text, timer hairline. */
    private static int draw(GuiGraphicsExtractor g, Font font, NoticeQueue.Notice n, long now, int x0, int y0) {
        float o = NoticeQueue.opacity(n, now);
        int textW = WIDTH - PAD - 6;
        List<FormattedCharSequence> lines = font.split(Component.literal(n.text()), textW);
        if (lines.size() > MAX_LINES) {
            lines = new ArrayList<>(lines.subList(0, MAX_LINES));
            String last = OmoStyle.fit(font, flatten(lines.get(MAX_LINES - 1)) + "\u2026", textW);
            lines.set(MAX_LINES - 1, Component.literal(last).getVisualOrderText());
        }
        int h = 19 + lines.size() * LINE + 3;
        if (o < 0.08f) {
            return h;                                     // keep the slot so cards below don't jump
        }
        int x1 = x0 + WIDTH;
        int y1 = y0 + h;
        int accent = accent(n.level());

        g.fill(x0 + 2, y0 + 2, x1 + 2, y1 + 2, fade(0x55000000, o));          // soft shadow
        g.fill(x0, y0, x1, y1, fade(OmoStyle.alpha(OmoStyle.ESPRESSO_DEEP, 0xE6), o));
        int rim = fade(OmoStyle.alpha(accent, 0x70), o);
        g.fill(x0 + 1, y0, x1 - 1, y0 + 1, rim);                               // 1px-clipped corners
        g.fill(x0 + 1, y1 - 1, x1 - 1, y1, rim);
        g.fill(x0, y0 + 1, x0 + 1, y1 - 1, rim);
        g.fill(x1 - 1, y0 + 1, x1, y1 - 1, rim);
        g.fill(x0 + 1, y0 + 1, x0 + 3, y1 - 1, fade(accent, o));              // left stripe

        int gold = fade(OmoStyle.GOLD, o);
        wing(g, x0 + PAD, y0 + 6, gold);
        String who = OmoStyle.fit(font, n.profile(), WIDTH - PAD - 20 - 6);
        g.text(font, who, x0 + PAD + 16, y0 + 6, fade(OmoStyle.GOLD_LIGHT, o), false);

        int ly = y0 + 19;
        int cream = fade(OmoStyle.CREAM, o);
        for (FormattedCharSequence line : lines) {
            g.text(font, line, x0 + PAD, ly, cream, false);
            ly += LINE;
        }
        int bar = Math.round((WIDTH - 4) * NoticeQueue.remaining(n, now));
        g.fill(x0 + 3, y1 - 2, x0 + 3 + bar, y1 - 1, fade(OmoStyle.alpha(accent, 0x90), o));
        return h;
    }

    private static void wing(GuiGraphicsExtractor g, int x, int y, int colour) {
        for (int r = 0; r < WING.length; r++) {
            String row = WING[r];
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

    /** The colour with its alpha scaled by the card's opacity. */
    private static int fade(int argb, float o) {
        int a = Math.round(((argb >>> 24) & 0xFF) * o);
        return (a << 24) | (argb & 0x00FFFFFF);
    }

    private static String flatten(FormattedCharSequence seq) {
        StringBuilder sb = new StringBuilder();
        seq.accept((i, style, cp) -> {
            sb.appendCodePoint(cp);
            return true;
        });
        return sb.toString().stripTrailing();
    }
}

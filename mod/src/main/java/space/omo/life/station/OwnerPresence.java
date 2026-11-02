package space.omo.life.station;

import java.util.Map;
import java.util.Set;
import java.util.UUID;
import java.util.concurrent.ConcurrentHashMap;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * Live owner presence: which stations the world owner (or an operator) has
 * really used in this session, and who of them is online. The {@code omo}
 * Lua API asks {@link #ticketRefusal} before minting a ticket and
 * {@link #recent} before creating a workspace, so a loaded world on its own
 * (a shared map, a powered station, a pocket in a chest) never reaches the
 * host. The rule itself is {@link StationTrust#refusal}.
 *
 * <p>Interactions that count: opening a station, its panel actions, taking
 * over one of its walls and typing there, typing in its computer GUI, and
 * right-clicking a station pocket; each only for a player
 * {@link Stations#allowed} lets in.
 *
 * <p>Read from CC:T's computer threads, written on the server thread.
 */
public final class OwnerPresence {

    private record Touch(UUID player, long at) {
    }

    private static final Map<String, Touch> TOUCHES = new ConcurrentHashMap<>();
    private static final Set<UUID> ONLINE = ConcurrentHashMap.newKeySet();
    private static volatile StationTrust trust;
    private static int ticks;

    private OwnerPresence() {
    }

    public static void register() {
        ServerLifecycleEvents.SERVER_STARTED.register(server -> {
            trust = new StationTrust(FabricLoader.getInstance().getConfigDir().resolve("omolife-trusted-stations.txt"));
            refresh(server);
        });
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> {
            TOUCHES.clear();
            ONLINE.clear();
        });
        ServerTickEvents.END_SERVER_TICK.register(server -> {
            if (++ticks % 20 == 0) {
                refresh(server);
            }
        });
    }

    /** Who of the owner and operators is in the game now. */
    private static void refresh(MinecraftServer server) {
        Set<UUID> now = ConcurrentHashMap.newKeySet();
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            if (Stations.allowed(p)) {
                now.add(p.getUUID());
            }
        }
        ONLINE.retainAll(now);
        ONLINE.addAll(now);
    }

    /**
     * An allowed player used this station just now. The first touch after a
     * quiet spell nudges the station's screen ({@code omo_owner}) so a screen
     * that was waiting connects at once. Server thread.
     */
    public static void touch(Station station, ServerPlayer player) {
        if (station == null || player == null || !Stations.allowed(player)) {
            return;
        }
        String id = station.stationIdOrEmpty();
        if (id.isEmpty()) {
            return;
        }
        ONLINE.add(player.getUUID());
        long now = System.currentTimeMillis();
        Touch before = TOUCHES.put(id, new Touch(player.getUUID(), now));
        if (before == null || !StationTrust.recent(before.at(), now)) {
            station.nudge();
        }
    }

    /** An allowed player, still online, used this station within the window. */
    public static boolean recent(Station station) {
        Touch t = TOUCHES.get(station.stationIdOrEmpty());
        return t != null && StationTrust.recent(t.at(), System.currentTimeMillis()) && ONLINE.contains(t.player());
    }

    /** Null when this station may mint a ticket now; otherwise the reason it waits. Any thread. */
    public static String ticketRefusal(Station station) {
        String ws = station.workspace();
        String id = station.stationIdOrEmpty();
        StationTrust t = trust;
        boolean live = recent(station);
        String why = StationTrust.refusal(RomCheck.problem(), live, t != null && t.contains(id, ws), !ONLINE.isEmpty());
        if (why == null && live && t != null) {
            t.add(id, ws);                         // the owner used this station with this workspace here
        }
        return why;
    }
}

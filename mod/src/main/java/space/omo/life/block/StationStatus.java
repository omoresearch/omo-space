package space.omo.life.block;

import java.util.Collections;
import java.util.HashMap;
import java.util.HashSet;
import java.util.IdentityHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerTickEvents;
import net.minecraft.server.MinecraftServer;

import space.omo.life.station.Stations;

/**
 * Keeps every loaded Name Plate and Status Lamp showing its station.
 *
 * <p>Every {@link #PERIOD} ticks, while any plate or lamp is loaded, each
 * finds its station and the bridge is asked to describe each workspace they
 * show: one request per distinct workspace, however many plates and lamps
 * watch it, and never a second while one is in flight. That is the bridge's
 * existing {@code get}; nothing new runs on the host. Server thread only.
 */
public final class StationStatus {

    private static final int PERIOD = 40;

    private static final Set<StatusBlockEntity> WATCHERS = Collections.newSetFromMap(new IdentityHashMap<>());
    /** The last description of each watched workspace. */
    private static final Map<String, Seen> SEEN = new HashMap<>();
    private static final Set<String> ASKING = new HashSet<>();
    private static int ticks;
    private static boolean soon;

    private record Seen(StationGlow glow, String name) {
    }

    private StationStatus() {
    }

    public static void register() {
        ServerTickEvents.END_SERVER_TICK.register(StationStatus::tick);
        ServerLifecycleEvents.SERVER_STOPPED.register(server -> {
            WATCHERS.clear();
            SEEN.clear();
            ASKING.clear();
        });
    }

    static void watch(StatusBlockEntity be) {
        WATCHERS.add(be);
        soon = true;
    }

    static void unwatch(StatusBlockEntity be) {
        WATCHERS.remove(be);
    }

    /** Look again on the next tick (a new link, a new block). */
    static void refresh() {
        soon = true;
    }

    private static void tick(MinecraftServer server) {
        if (WATCHERS.isEmpty() || (++ticks % PERIOD != 0 && !soon)) {
            return;
        }
        soon = false;
        Set<String> wanted = new HashSet<>();
        for (StatusBlockEntity be : List.copyOf(WATCHERS)) {
            StationBlockEntity station = be.station();
            show(be, station);
            if (station != null && station.isBound()) {
                wanted.add(station.workspace());
            }
        }
        SEEN.keySet().retainAll(wanted);
        for (String ws : wanted) {
            if (ASKING.add(ws)) {
                Stations.onServer(server, Stations.getWorkspace(ws), reply -> {
                    ASKING.remove(ws);
                    SEEN.put(ws, new Seen(glow(reply), reply.has("name") ? reply.get("name").getAsString() : ""));
                    for (StatusBlockEntity be : List.copyOf(WATCHERS)) {
                        StationBlockEntity station = be.following();
                        if (station != null && ws.equals(station.workspace())) {
                            show(be, station);
                        }
                    }
                });
            }
        }
    }

    private static void show(StatusBlockEntity be, StationBlockEntity station) {
        if (station == null) {
            be.show(null, StationGlow.OFF, "");
            return;
        }
        Seen seen = station.isBound() ? SEEN.get(station.workspace()) : null;
        String name = seen != null && !seen.name().isEmpty() ? seen.name() : station.workspaceName();
        be.show(station, seen == null ? StationGlow.OFF : seen.glow(), name);
    }

    /** The bridge's workspace description, as a lamp colour. An error (bridge down, no such workspace) is off. */
    static StationGlow glow(JsonObject ws) {
        if (ws.has("error") || !ws.has("state")) {
            return StationGlow.OFF;
        }
        String state = ws.get("state").getAsString();
        if ("interrupted".equals(state)) {
            return StationGlow.INTERRUPTED;
        }
        if (!"live".equals(state)) {
            return StationGlow.OFF;
        }
        if (ws.has("tabs")) {
            for (JsonElement t : ws.getAsJsonArray("tabs")) {
                JsonObject tab = t.getAsJsonObject();
                if ("live".equals(tab.get("state").getAsString()) && tab.has("busy") && tab.get("busy").getAsBoolean()) {
                    return StationGlow.WORKING;
                }
            }
        }
        return StationGlow.IDLE;
    }
}

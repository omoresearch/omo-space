package space.omo.life.station;

import java.util.HashMap;
import java.util.Map;
import java.util.Set;
import java.util.WeakHashMap;
import java.util.concurrent.ConcurrentHashMap;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import net.minecraft.world.level.Level;

import space.omo.life.block.StationBlockEntity;

/**
 * What a station tells redstone: its workspace's state, read from the
 * bridge, as a comparator signal (and, when switched on, a plain signal).
 *
 * <pre>
 *   comparator   meaning
 *        0       no workspace yet, or the bridge cannot be reached
 *        1       ended: every tab has exited
 *        3       idle: shells at their prompt, Hermes waiting for you
 *        8       working: a command running in a shell tab, or Hermes on a reply
 *       15       interrupted: the host or the session server restarted
 * </pre>
 * The plain signal ({@link StationBlockEntity#signalsWork()}) is 15 on every
 * side while the comparator says working, 0 otherwise; it adds to whatever
 * Lua sets with CC:T's {@code redstone} API, never replaces it.
 *
 * <p>Each loaded, bound station asks the bridge about its workspace at most
 * every {@link #PERIOD} ticks, off the server thread; stations sharing a
 * workspace share the read. Neighbours hear about a change only when the
 * level actually changes.
 */
public final class StationSignals {

    public static final int NONE = 0;
    public static final int ENDED = 1;
    public static final int IDLE = 3;
    public static final int WORKING = 8;
    public static final int INTERRUPTED = 15;

    /** Ticks between bridge reads of one workspace (2 s). */
    static final int PERIOD = 40;

    /** Latest level per workspace id: written by bridge callbacks, read on the server thread. */
    private static final Map<String, Integer> READINGS = new ConcurrentHashMap<>();
    private static final Set<String> IN_FLIGHT = ConcurrentHashMap.newKeySet();
    /** Server thread only: when each workspace was last asked about, and what each station last showed. */
    private static final Map<String, Long> ASKED = new HashMap<>();
    private static final Map<StationBlockEntity, Integer> SHOWN = new WeakHashMap<>();

    private StationSignals() {
    }

    /** Every server tick of a station block: refresh the reading, tell neighbours when it changed. */
    public static void tick(StationBlockEntity be) {
        Level level = be.getLevel();
        if (level == null || level.isClientSide()) {
            return;
        }
        String ws = be.workspace();
        long now = level.getGameTime();
        if (!ws.isEmpty() && now - ASKED.getOrDefault(ws, Long.MIN_VALUE / 2) >= PERIOD && IN_FLIGHT.add(ws)) {
            ASKED.put(ws, now);
            Stations.getWorkspace(ws).whenComplete((reply, err) -> {
                READINGS.put(ws, err == null ? levelOf(reply) : NONE);
                IN_FLIGHT.remove(ws);
            });
        }
        int signal = ws.isEmpty() ? NONE : READINGS.getOrDefault(ws, NONE);
        Integer shown = SHOWN.put(be, signal);
        if (shown == null || shown != signal) {
            level.updateNeighbourForOutputSignal(be.getBlockPos(), be.getBlockState().getBlock());
            if (be.signalsWork() && (shown == null || (shown == WORKING) != (signal == WORKING))) {
                be.updateRedstone();
            }
        }
    }

    /** The comparator level this station shows. */
    public static int comparator(StationBlockEntity be) {
        return SHOWN.getOrDefault(be, NONE);
    }

    /** The plain signal this station adds on every side: 15 while working, if switched on. */
    public static int plain(StationBlockEntity be) {
        return be.signalsWork() && comparator(be) == WORKING ? 15 : 0;
    }

    /** The level for one bridge {@code get} reply. */
    static int levelOf(JsonObject ws) {
        if (ws == null || ws.has("error")) {
            return NONE;
        }
        String state = ws.has("state") ? ws.get("state").getAsString() : "";
        if ("interrupted".equals(state)) {
            return INTERRUPTED;
        }
        if ("ended".equals(state)) {
            return ENDED;
        }
        for (JsonElement el : ws.getAsJsonArray("tabs")) {
            JsonObject t = el.getAsJsonObject();
            if (!"live".equals(t.get("state").getAsString()) || !t.get("busy").getAsBoolean()) {
                continue;
            }
            // Hermes is always running; its status bar says whether it works or waits ("ready").
            String agent = t.has("agent") ? t.get("agent").getAsString() : null;
            if (agent == null || !agent.isEmpty() && !agent.startsWith("ready")) {
                return WORKING;
            }
        }
        return IDLE;
    }
}

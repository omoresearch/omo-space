package space.omo.life.lua;

import java.util.HashMap;
import java.util.Map;
import java.util.concurrent.atomic.AtomicInteger;
import java.util.function.Supplier;

import dan200.computercraft.api.lua.IComputerSystem;
import dan200.computercraft.api.lua.ILuaAPI;
import dan200.computercraft.api.lua.ILuaCallback;
import dan200.computercraft.api.lua.LuaFunction;
import dan200.computercraft.api.lua.MethodResult;

import space.omo.life.OmoLife;
import space.omo.life.station.BridgeLink;
import space.omo.life.station.Station;

/**
 * The {@code omo} Lua API. It exists on Omo stations (Hermes Computers,
 * Terminals and Omo Pockets) and nowhere else.
 *
 * <pre>
 *   omo.station() -> { workspace=, station=, name=, hermes=, bound= }
 *   omo.ticket()  -> ticket, workspaceId | nil, reason
 *   omo.bridge()  -> "ws://127.0.0.1:&lt;port&gt;/ws"
 *   omo.ensure()  -> asks the server to create this station's workspace if it has none
 * </pre>
 *
 * <p>The bridge token is never exposed to Lua. {@code omo.ticket()} takes no
 * argument: it always mints for the computer's <em>own</em> station and the
 * workspace that station is bound to, and the bridge only lets that ticket
 * look at and type into that one workspace. A program on some other computer
 * has no {@code omo} API at all.
 *
 * <p>How "only on stations" works: CC:T builds every computer's APIs inside
 * the {@code ServerComputer} constructor, on the server thread, while the
 * level must not be touched. A station (block or pocket) creates its computer
 * through {@link #createFor}, which marks the thread for the duration of that
 * constructor; the factory returns null for every other computer.
 */
public final class OmoLuaApi implements ILuaAPI {

    private static final String TICKET_EVENT = "omo_ticket";
    private static final ThreadLocal<Station> CONSTRUCTING = new ThreadLocal<>();
    private static final AtomicInteger REQUESTS = new AtomicInteger();

    private final IComputerSystem computer;
    private final Station station;

    private OmoLuaApi(IComputerSystem computer, Station station) {
        this.computer = computer;
        this.station = station;
    }

    /** Build a station's computer; its APIs are created while this runs. */
    public static <T> T createFor(Station station, Supplier<T> build) {
        CONSTRUCTING.set(station);
        try {
            return build.get();
        } finally {
            CONSTRUCTING.remove();
        }
    }

    /** The CC:T API factory: an {@code omo} API for stations, nothing for any other computer. */
    public static ILuaAPI create(IComputerSystem computer) {
        Station station = CONSTRUCTING.get();
        return station == null ? null : new OmoLuaApi(computer, station);
    }

    @Override
    public String[] getNames() {
        return new String[]{"omo"};
    }

    @LuaFunction
    public final Map<String, Object> station() {
        Map<String, Object> out = new HashMap<>();
        out.put("workspace", station.workspace());
        out.put("station", station.stationIdOrEmpty());
        out.put("name", station.workspaceName());
        out.put("hermes", station.isHermes());
        out.put("bound", station.isBound());
        return out;
    }

    /**
     * Mint a single-use ticket for this station and its workspace.
     *
     * <p>The loopback HTTP call runs on the HTTP client's own threads; this
     * computer yields until the answer arrives as an event, so CC:T's
     * computer thread (shared by every computer in the world) never waits
     * on the network.
     */
    @LuaFunction
    public final MethodResult ticket() {
        if (!station.isPresent()) {
            return MethodResult.of(null, "not an Omo station");
        }
        String stationId = station.stationIdOrEmpty();
        String ws = station.workspace();
        if (stationId.isEmpty() || ws.isEmpty()) {
            return MethodResult.of(null, "unbound");
        }
        int request = REQUESTS.incrementAndGet();
        BridgeLink.ticket(stationId, ws).thenAccept(t -> {
            try {
                computer.queueEvent(TICKET_EVENT, request, t.ticket(), t.ticket() == null ? t.error() : ws);
            } catch (RuntimeException e) {
                OmoLife.LOG.debug("station computer gone before its ticket arrived", e);
            }
        });
        return MethodResult.pullEvent(TICKET_EVENT, new TicketWait(request));
    }

    /** Resumes {@link #ticket} with its own answer; ignores answers meant for an earlier call. */
    private record TicketWait(int request) implements ILuaCallback {
        @Override
        public MethodResult resume(Object[] args) {
            if (args.length < 4 || !(args[1] instanceof Number n) || n.intValue() != request) {
                return MethodResult.pullEvent(TICKET_EVENT, this);
            }
            return MethodResult.of(args[2], args[3]);
        }
    }

    @LuaFunction
    public final String bridge() {
        return BridgeLink.wsUrl();
    }

    @LuaFunction(mainThread = true)
    public final void ensure() {
        station.ensureWorkspace();
    }
}

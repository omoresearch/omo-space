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
import space.omo.life.block.StationBlockEntity;
import space.omo.life.station.BridgeLink;
import space.omo.life.station.OwnerPresence;
import space.omo.life.station.StationTrust;
import space.omo.life.station.Station;

/**
 * The {@code omo} Lua API. It exists on Omo stations (Hermes Computers,
 * Terminals and Omo Pockets) and nowhere else.
 *
 * <pre>
 *   omo.station() -> { workspace=, station=, name=, hermes=, bound= }
 *   omo.ticket()  -> ticket, workspaceId | nil, reason ("waiting for owner" until the owner uses it)
 *   omo.bridge()  -> "ws://127.0.0.1:&lt;port&gt;/ws"
 *   omo.ensure()  -> true if the server creates this station's workspace (owner used it just now)
 * </pre>
 *
 * <p>The bridge token is never exposed to Lua. {@code omo.ticket()} takes no
 * argument: it always mints for the computer's <em>own</em> station and the
 * workspace that station is bound to, and the bridge only lets that ticket
 * look at and type into that one workspace. A program on some other computer
 * has no {@code omo} API at all. And world data alone never gets a ticket:
 * one is only minted while the computer ROM is the mods' own
 * ({@code RomCheck}) and the owner has used this station live
 * ({@link OwnerPresence}), so a shared map cannot reach the host.
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

    private static final String BOOT_SHADOWED =
        "refused: this computer's own disk has a /cc folder, which runs before the station; remove it";

    private final IComputerSystem computer;
    private final Station station;
    private volatile String bootProblem;
    private boolean guardMounted;

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

    /**
     * Before the BIOS runs (CC:T starts APIs right after it builds the
     * computer's file system): put an empty read-only {@code /cc} over the
     * computer's own disk, so CraftOS's boot cannot {@code require} world
     * code from it ({@link EmptyMount}). A disk that already has a
     * {@code /cc} would run its code anyway, so this boot gets no tickets.
     */
    @Override
    public void startup() {
        bootProblem = null;
        try {
            String at = computer.mount("cc", EmptyMount.INSTANCE);
            guardMounted = "cc".equals(at);
            if (!guardMounted) {
                if (at != null) {
                    computer.unmount(at);
                }
                bootProblem = BOOT_SHADOWED;
            }
        } catch (RuntimeException e) {
            OmoLife.LOG.warn("station {}: could not guard its boot", station.stationIdOrEmpty(), e);
            bootProblem = BOOT_SHADOWED;
        }
        if (bootProblem != null) {
            OmoLife.LOG.warn("station {}: its computer's own disk has a /cc folder, which CraftOS loads before the "
                + "station program; no tickets until it is removed", station.stationIdOrEmpty());
        }
    }

    @Override
    public void shutdown() {
        if (guardMounted) {
            guardMounted = false;
            computer.unmount("cc");
        }
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
        if (bootProblem != null) {
            return MethodResult.of(null, bootProblem);
        }
        String stationId = station.stationIdOrEmpty();
        String ws = station.workspace();
        if (stationId.isEmpty() || ws.isEmpty()) {
            return MethodResult.of(null, "unbound");
        }
        if (station instanceof StationBlockEntity be && be.isStale()) {
            return MethodResult.of(null, OwnerPresence.recent(station) ? "unbound" : StationTrust.WAITING);
        }
        String refused = OwnerPresence.ticketRefusal(station);
        if (refused != null) {
            return MethodResult.of(null, refused);
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

    /**
     * Create this station's workspace if it has none: only within a moment of
     * the owner using the station ({@link OwnerPresence#recent}), never for a
     * world that merely loaded. True when creation may go ahead, false while
     * the station waits for its owner.
     */
    @LuaFunction(mainThread = true)
    public final boolean ensure() {
        if (bootProblem != null || !OwnerPresence.recent(station)) {
            return false;
        }
        station.ensureWorkspace();
        return true;
    }
}

package space.omo.life.block;

import java.util.List;
import java.util.UUID;

import com.google.gson.JsonObject;

import dan200.computercraft.shared.computer.blocks.ComputerBlockEntity;
import dan200.computercraft.shared.computer.core.ComputerFamily;
import dan200.computercraft.shared.computer.core.ServerComputer;

import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderLookup;
import net.minecraft.core.component.DataComponentGetter;
import net.minecraft.core.component.DataComponentMap;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.protocol.game.ClientboundBlockEntityDataPacket;
import net.minecraft.server.MinecraftServer;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.level.storage.ValueOutput;

import space.omo.life.OmoLife;
import space.omo.life.lua.OmoLuaApi;
import space.omo.life.net.StationPackets;
import space.omo.life.station.Station;
import space.omo.life.station.StationPreset;
import space.omo.life.station.StationSignals;
import space.omo.life.station.Stations;

/**
 * A station: a CC:T advanced computer bound to one host workspace.
 *
 * <p>What the block stores in the world, and all it stores:
 * <ul>
 *   <li>{@code Workspace} - the bridge's workspace id (12 hex chars), the
 *       durable identity. Independent of coordinates, label and CC id. It
 *       travels with the item, so a moved station keeps its workspace.</li>
 *   <li>{@code Station} - a random UUID for this placed block, used to steer
 *       its screen. Never copied onto items: every placement (including a
 *       creative copy) mints a fresh one, so two blocks never share it.</li>
 *   <li>{@code Hermes} - whether the first tab should be a Hermes tab (and
 *       the accent gold).</li>
 *   <li>{@code OmoPreset} - the station's recipe: name, kind, profile and
 *       tabs of the workspace it makes ({@link StationPreset}). Set by an
 *       authored world or the Station Setup screen, and kept: a copy of the
 *       map on another PC, where the workspace id means nothing, makes a
 *       fresh workspace from it.</li>
 *   <li>{@code OmoLocked} - a map maker's lock: players can use the station
 *       but, outside creative mode, not rename, rebind or re-profile it.</li>
 *   <li>{@code OmoSignal} - whether it also gives a plain redstone signal
 *       while its workspace is working ({@link StationSignals}).</li>
 * </ul>
 * No shell, token, credential or transcript is ever written here.
 *
 * <p>The computer underneath is a normal advanced computer running the rom
 * client; it gets the {@code omo} Lua API (see {@link OmoLuaApi}) because this
 * block entity builds it.
 */
public class StationBlockEntity extends ComputerBlockEntity implements Station {

    public static final String NBT_WORKSPACE = "Workspace";
    public static final String NBT_STATION = "Station";
    public static final String NBT_HERMES = "Hermes";
    public static final String NBT_WS_NAME = "WorkspaceName";
    public static final String NBT_LOCKED = "OmoLocked";
    public static final String NBT_SIGNAL = "OmoSignal";

    /** Ticks between asks while the bridge cannot say whether this station's workspace exists. */
    private static final int VERIFY_RETRY_TICKS = 200;

    // Read by the omo Lua API on CC:T's computer thread, written on the server thread.
    private volatile String workspace = "";
    private volatile String station = "";
    private volatile String workspaceName = "";
    private volatile boolean hermes;
    private boolean locked;
    private boolean creating;
    private CompoundTag preset;
    private boolean signalsWork;
    private long verifyAt;                          // game time of the next check that the workspace exists; -1 done

    public StationBlockEntity(BlockEntityType<? extends StationBlockEntity> type, BlockPos pos,
                              BlockState state) {
        super(type, pos, state, ComputerFamily.ADVANCED);
        this.hermes = state.getBlock() instanceof HermesComputerBlock hc && hc.isHermes();
    }

    // -- identity -----------------------------------------------------------

    @Override
    public String workspace() {
        return workspace;
    }

    /** This block's station id, minted on first use. Server thread only. */
    public String station() {
        if (station.isEmpty()) {
            station = UUID.randomUUID().toString();
            setChanged();
        }
        return station;
    }

    @Override
    public String stationIdOrEmpty() {
        return station;
    }

    @Override
    public boolean isHermes() {
        return hermes;
    }

    @Override
    public String workspaceName() {
        return workspaceName;
    }

    @Override
    public boolean isPresent() {
        return !isRemoved();
    }

    /** What players see this station called: its workspace, else the name its preset will give it. */
    public String displayName() {
        return !workspaceName.isEmpty() ? workspaceName
            : preset == null ? "" : preset.getStringOr("Name", "");
    }

    public boolean isLocked() {
        return locked;
    }

    /** A locked station keeps its name, workspace and profile, except for players in creative mode. */
    public boolean lockedFor(Player player) {
        return locked && !player.isCreative();
    }

    /** The saved preset, or null. */
    public StationPreset preset() {
        return StationPreset.from(preset, hermes);
    }

    /**
     * What a fresh workspace for this station is made from: the preset, under
     * the name the station shows now (else the preset's, else its CC label).
     */
    public StationPreset recipe() {
        StationPreset p = preset();
        String label = getLabel();
        String name = !workspaceName.isEmpty() ? workspaceName
            : p != null && !p.name().isEmpty() ? p.name()
            : label != null && !label.isBlank() ? label : "";
        return p == null ? new StationPreset(name, hermes, "", List.of())
            : new StationPreset(name, p.hermes(), p.profile(), p.tabs());
    }

    /** The Station Setup screen's settings: the recipe (kind included) and the lock. Server thread. */
    public void configure(StationPreset recipe, boolean lock) {
        preset = recipe.toTag();
        hermes = recipe.hermes();
        locked = lock;
        setChanged();
        sync();
    }

    /**
     * Bind this station to a workspace (first creation, recovery, rebind).
     * The screen is told at once so it reconnects with a ticket for it.
     */
    @Override
    public void bind(String ws, String name) {
        workspace = ws == null ? "" : ws;
        workspaceName = name == null ? "" : name;
        verifyAt = -1;                               // just made or chosen here: it exists
        setChanged();
        sync();
        ServerComputer computer = getServerComputer();
        if (computer != null) {
            computer.queueEvent("omo_bound", new Object[]{workspace, workspaceName});
        }
    }

    @Override
    public void setWorkspaceName(String name) {
        if (name != null && !name.equals(workspaceName)) {
            workspaceName = name;
            setChanged();
            sync();
        }
    }

    /**
     * Back to an unbound station that makes its first workspace as
     * {@code preset} says (a builder's preset, or a shared map's). The
     * computer is shut down so its screen does not make one at once; the
     * workspace itself stays on the host. Server thread.
     */
    public void resetToPreset(CompoundTag newPreset) {
        ServerComputer computer = getServerComputer();
        if (computer != null) {
            computer.shutdown();
        }
        workspace = "";
        workspaceName = "";
        preset = newPreset == null || newPreset.isEmpty() ? null : newPreset.copy();
        setChanged();
        sync();
    }

    // -- redstone -------------------------------------------------------------

    /** Whether this station also gives a plain signal while working. */
    public boolean signalsWork() {
        return signalsWork;
    }

    public void setSignalsWork(boolean on) {
        if (on != signalsWork) {
            signalsWork = on;
            setChanged();
            updateRedstone();
        }
    }

    private void sync() {
        if (level != null && !level.isClientSide()) {
            level.sendBlockUpdated(worldPosition, getBlockState(), getBlockState(), 3);
        }
    }

    // -- lifecycle ------------------------------------------------------------

    /** Every station computer is built here, so it (and only it) gets the omo API. */
    @Override
    protected ServerComputer createComputer(int id) {
        station();                                   // mint on the server thread, never on the Lua side
        return OmoLuaApi.createFor(this, () -> super.createComputer(id));
    }

    /**
     * Make sure a workspace exists for this station. Called when a player
     * opens it (and from Lua), so a station placed while the bridge was down
     * gets its workspace the moment the bridge is back. Never creates a
     * second workspace for a bound station.
     */
    @Override
    public void ensureWorkspace() {
        if (level == null || level.isClientSide() || isBound() || creating) {
            return;
        }
        MinecraftServer server = level.getServer();
        if (server == null) {
            return;
        }
        creating = true;
        station();                                   // minted before the bridge hears of it
        Stations.createFor(server, this, recipe(), error -> creating = false);
    }

    /**
     * A running station checks once that its workspace exists on this host.
     * A map made on another PC carries workspace ids that mean nothing here:
     * such a station makes a fresh workspace from its recipe instead of
     * showing an error. The id is never guessed at; only the bridge's
     * "no such workspace" does this, and a bridge that is down is asked again
     * later.
     */
    @Override
    protected void serverTick() {
        super.serverTick();
        if (verifyAt < 0 || !isBound() || creating || getServerComputer() == null || level == null
            || level.getGameTime() < verifyAt) {
            return;
        }
        MinecraftServer server = level.getServer();
        String ws = workspace;
        verifyAt = level.getGameTime() + VERIFY_RETRY_TICKS;
        Stations.onServer(server, Stations.getWorkspace(ws), (JsonObject reply) -> {
            if (isRemoved() || !ws.equals(workspace) || creating) {
                return;
            }
            String error = reply.has("error") ? reply.get("error").getAsString() : null;
            if (error == null) {
                verifyAt = -1;
            } else if (error.startsWith("no such workspace")) {
                verifyAt = -1;
                creating = true;
                OmoLife.LOG.info("station {}: workspace {} is not on this host; making a fresh one from its preset",
                    station, ws);
                Stations.createFor(server, this, recipe(), e -> {
                    creating = false;
                    if (e == null) {
                        StationPackets.tellAll(server, this, "A fresh workspace for this map, from its preset.");
                    }
                });
            }
        });
    }

    // -- persistence ------------------------------------------------------------

    @Override
    public void saveAdditional(ValueOutput out) {
        super.saveAdditional(out);
        if (!workspace.isEmpty()) {
            out.putString(NBT_WORKSPACE, workspace);
        }
        if (!station.isEmpty()) {
            out.putString(NBT_STATION, station);
        }
        out.putBoolean(NBT_HERMES, hermes);
        if (!workspaceName.isEmpty()) {
            out.putString(NBT_WS_NAME, workspaceName);
        }
        if (preset != null) {
            out.store(StationPreset.NBT, CompoundTag.CODEC, preset);
        }
        if (locked) {
            out.putBoolean(NBT_LOCKED, true);
        }
        if (signalsWork) {
            out.putBoolean(NBT_SIGNAL, true);
        }
    }

    @Override
    protected void loadServer(ValueInput in) {
        super.loadServer(in);
        readBinding(in);
        signalsWork = in.getBooleanOr(NBT_SIGNAL, false);
    }

    @Override
    protected void loadClient(ValueInput in) {
        super.loadClient(in);
        readBinding(in);
    }

    private void readBinding(ValueInput in) {
        workspace = in.getStringOr(NBT_WORKSPACE, workspace);
        station = in.getStringOr(NBT_STATION, station);
        hermes = in.getBooleanOr(NBT_HERMES, hermes);
        workspaceName = in.getStringOr(NBT_WS_NAME, workspaceName);
        preset = in.read(StationPreset.NBT, CompoundTag.CODEC).orElse(preset);
        locked = in.getBooleanOr(NBT_LOCKED, locked);
    }

    // -- client sync (name, binding, recipe and lock; never anything secret) ----------------

    @Override
    public CompoundTag getUpdateTag(HolderLookup.Provider registries) {
        CompoundTag tag = super.getUpdateTag(registries);
        tag.putString(NBT_WORKSPACE, workspace);
        tag.putString(NBT_STATION, station);
        tag.putBoolean(NBT_HERMES, hermes);
        tag.putString(NBT_WS_NAME, workspaceName);
        if (preset != null) {
            tag.put(StationPreset.NBT, preset.copy());
        }
        tag.putBoolean(NBT_LOCKED, locked);
        return tag;
    }

    @Override
    public ClientboundBlockEntityDataPacket getUpdatePacket() {
        return ClientboundBlockEntityDataPacket.create(this);
    }

    // -- item components: the workspace rides along with the item -----------------

    /**
     * Into the item (broken block, pick-block): the workspace and its name.
     * Deliberately not the station id; see the class comment.
     */
    @Override
    protected void collectSafeComponents(DataComponentMap.Builder builder) {
        super.collectSafeComponents(builder);
        if (workspace.isEmpty()) {
            return;
        }
        CompoundTag tag = new CompoundTag();
        tag.putString(NBT_WORKSPACE, workspace);
        if (!workspaceName.isEmpty()) {
            tag.putString(NBT_WS_NAME, workspaceName);
        }
        builder.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
    }

    /** From the item: placing a station that was picked up restores its workspace. */
    @Override
    protected void applyImplicitComponents(DataComponentGetter components) {
        super.applyImplicitComponents(components);
        CustomData data = components.get(DataComponents.CUSTOM_DATA);
        if (data != null) {
            CompoundTag tag = data.copyTag();
            workspace = tag.getStringOr(NBT_WORKSPACE, workspace);
            workspaceName = tag.getStringOr(NBT_WS_NAME, workspaceName);
            setChanged();
        }
    }

    /**
     * The stack CC:T hands the client when the GUI opens: the binding plus
     * this block's station id and position, which is how the client screen
     * knows it is a station and which block to address. Never dropped.
     */
    public ItemStack screenStack() {
        ItemStack stack = new ItemStack(getBlockState().getBlock());
        DataComponentMap.Builder safe = DataComponentMap.builder();
        collectSafeComponents(safe);
        stack.applyComponents(safe.build());
        CompoundTag tag = new CompoundTag();
        tag.putString(NBT_WORKSPACE, workspace);
        tag.putString(NBT_WS_NAME, workspaceName);
        tag.putString(NBT_STATION, station());
        tag.putBoolean(NBT_HERMES, hermes);
        tag.putBoolean(NBT_LOCKED, locked);
        tag.putInt("X", worldPosition.getX());
        tag.putInt("Y", worldPosition.getY());
        tag.putInt("Z", worldPosition.getZ());
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
        return stack;
    }
}

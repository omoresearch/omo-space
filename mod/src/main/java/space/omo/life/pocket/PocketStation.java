package space.omo.life.pocket;

import java.util.HashMap;
import java.util.List;
import java.util.Map;
import java.util.UUID;

import dan200.computercraft.shared.ModRegistry;
import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.computer.core.ServerContext;
import dan200.computercraft.shared.pocket.items.PocketComputerItem;

import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Inventory;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;

import space.omo.life.block.StationBlockEntity;
import space.omo.life.station.Station;
import space.omo.life.station.StationPreset;
import space.omo.life.station.Stations;

/**
 * The station behind one running pocket computer, server side: an Omo
 * Pocket or any CC:T pocket computer (see {@link Pockets}).
 *
 * <p>The item is the station, so its binding lives on the stack, in custom
 * data under the same keys a Hermes Computer uses ({@code Workspace},
 * {@code WorkspaceName}, {@code Station}, {@code Hermes}); {@link Pockets}
 * writes it back whenever it changes. Never a token, ticket or transcript.
 * An authored world may add an {@code OmoPreset} ({@link StationPreset}),
 * used for the first workspace and then removed.
 * This object is the live copy for the pocket's computer: the Lua API reads
 * it from CC:T's computer thread, hence the volatile fields.
 *
 * <p>Station ids are unique among running pockets. A stack whose id is
 * already live on another computer (a copy of a running pocket) gets a fresh
 * id and keeps the workspace, as a picked-up Hermes Computer placed twice would.
 */
public final class PocketStation implements Station {

    /** Running pockets by station id. Server thread only. */
    private static final Map<String, PocketStation> LIVE = new HashMap<>();

    private final MinecraftServer server;
    private final String station;
    private final boolean hermes;
    private final StationPreset preset;
    private volatile String workspace;
    private volatile String workspaceName;
    private ServerComputer computer;
    private boolean creating;
    private ItemStack tickedStack;
    private long tickedAt = -1;

    private PocketStation(MinecraftServer server, String station, String workspace, String name, boolean hermes,
                          StationPreset preset) {
        this.server = server;
        this.station = station;
        this.workspace = workspace;
        this.workspaceName = name;
        this.hermes = hermes;
        this.preset = preset;
    }

    private static CompoundTag tag(ItemStack stack) {
        CustomData data = stack.get(DataComponents.CUSTOM_DATA);
        return data == null ? new CompoundTag() : data.copyTag();
    }

    /** The station id written on a stack, "" for a pocket never used as a station. */
    static String idOn(ItemStack stack) {
        return tag(stack).getStringOr(StationBlockEntity.NBT_STATION, "");
    }

    /** A station for a pocket whose computer is about to be built, from what its stack carries. */
    static PocketStation claim(MinecraftServer server, ItemStack stack, boolean hermes) {
        LIVE.values().removeIf(p -> !p.isPresent());
        CompoundTag tag = tag(stack);
        String id = tag.getStringOr(StationBlockEntity.NBT_STATION, "");
        if (id.isEmpty() || LIVE.containsKey(id)) {
            id = UUID.randomUUID().toString();
        }
        return new PocketStation(server, id, tag.getStringOr(StationBlockEntity.NBT_WORKSPACE, ""),
            tag.getStringOr(StationBlockEntity.NBT_WS_NAME, ""), hermes,
            StationPreset.from(tag.getCompoundOrEmpty(StationPreset.NBT), hermes));
    }

    /** The world is closing: its computers go with it. */
    static void forgetAll() {
        LIVE.clear();
    }

    void attach(ServerComputer built) {
        computer = built;
        LIVE.put(station, this);
    }

    static PocketStation of(ServerComputer computer) {
        for (PocketStation p : LIVE.values()) {
            if (p.computer == computer) {
                return p;
            }
        }
        return null;
    }

    /** The running pocket with this station id, if {@code player} carries it. */
    public static PocketStation carried(ServerPlayer player, String id) {
        PocketStation p = LIVE.get(id);
        if (p == null || !p.isPresent()) {
            return null;
        }
        Inventory inv = player.getInventory();
        for (int i = 0; i < inv.getContainerSize(); i++) {
            ItemStack s = inv.getItem(i);
            if (s.getItem() instanceof PocketComputerItem && id.equals(idOn(s))) {
                return p;
            }
        }
        return null;
    }

    /** The running pocket in the player's main hand, or null. */
    public static PocketStation held(ServerPlayer player) {
        ItemStack s = player.getMainHandItem();
        return s.getItem() instanceof PocketComputerItem ? carried(player, idOn(s)) : null;
    }

    // -- Station -------------------------------------------------------------------

    @Override
    public String workspace() {
        return workspace;
    }

    @Override
    public String workspaceName() {
        return workspaceName;
    }

    @Override
    public String stationIdOrEmpty() {
        return station;
    }

    @Override
    public boolean isHermes() {
        return hermes;
    }

    /** CC:T drops a pocket's computer a few seconds after nothing holds it any more. */
    @Override
    public boolean isPresent() {
        return computer != null && ServerContext.get(server).registry().get(computer.getInstanceUUID()) == computer;
    }

    @Override
    public void ensureWorkspace() {
        if (isBound() || creating || computer == null) {
            return;
        }
        creating = true;
        String label = computer.getLabel();
        String name = preset != null && !preset.name().isEmpty() ? preset.name() : label == null ? "" : label;
        Stations.createFor(server, this, preset == null ? new StationPreset(name, hermes, "", List.of())
            : new StationPreset(name, preset.hermes(), preset.profile(), preset.tabs()), error -> creating = false);
    }

    @Override
    public void bind(String ws, String name) {
        workspace = ws == null ? "" : ws;
        workspaceName = name == null ? "" : name;
        if (computer != null) {
            computer.queueEvent("omo_bound", new Object[]{workspace, workspaceName});
        }
    }

    @Override
    public void setWorkspaceName(String name) {
        if (name != null) {
            workspaceName = name;
        }
    }

    // -- the stack -------------------------------------------------------------------

    /** Copy the binding onto the stack when it differs; true when the stack changed. */
    boolean writeTo(ItemStack stack) {
        CompoundTag before = tag(stack);
        CompoundTag after = before.copy();
        after.putString(StationBlockEntity.NBT_STATION, station);
        after.putBoolean(StationBlockEntity.NBT_HERMES, hermes);
        if (workspace.isEmpty()) {
            after.remove(StationBlockEntity.NBT_WORKSPACE);
            after.remove(StationBlockEntity.NBT_WS_NAME);
        } else {
            after.putString(StationBlockEntity.NBT_WORKSPACE, workspace);
            after.putString(StationBlockEntity.NBT_WS_NAME, workspaceName);
            after.remove(StationPreset.NBT);            // used once: the workspace now exists
        }
        if (after.equals(before)) {
            return false;
        }
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(after));
        return true;
    }

    /**
     * True when a second stack ticks this computer within one game tick: a
     * copy of a running pocket (creative middle-click, /item). CC:T would let
     * both stacks drive one computer; a station pocket splits instead.
     */
    boolean isCopy(ItemStack stack, long gameTime) {
        if (gameTime == tickedAt && stack != tickedStack) {
            return true;
        }
        tickedStack = stack;
        tickedAt = gameTime;
        return false;
    }

    /** Make a copied stack its own pocket: its next tick builds its own computer and station id. */
    static void split(ItemStack stack) {
        stack.remove(ModRegistry.DataComponents.COMPUTER.get());
        CompoundTag tag = tag(stack);
        tag.remove(StationBlockEntity.NBT_STATION);
        stack.set(DataComponents.CUSTOM_DATA, CustomData.of(tag));
    }
}

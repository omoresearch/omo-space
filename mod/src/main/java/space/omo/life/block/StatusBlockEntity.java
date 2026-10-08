package space.omo.life.block;

import net.minecraft.core.BlockPos;
import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.network.protocol.game.ClientboundBlockEntityDataPacket;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.BlockEntityType;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.level.storage.ValueOutput;

/**
 * A Name Plate or Status Lamp: one station's name and state, live.
 *
 * <p>The world stores only the link (an offset to the station, see
 * {@link StationLinked}). What the block shows is worked out on the server
 * by {@link StationStatus} from the station block and the bridge's
 * description of its workspace, and reaches clients in the update tag: a
 * plate draws it, a lamp turns it into its {@link StatusLampBlock#GLOW}.
 */
public class StatusBlockEntity extends BlockEntity implements StationLinked {

    /** How far an unlinked plate or lamp looks for a station. */
    public static final int REACH = 8;

    private static final String NBT_LINK = "OmoLink";
    private static final String TAG_NAME = "OmoName";
    private static final String TAG_GLOW = "OmoGlow";
    private static final String TAG_KIND = "OmoKind";

    /** Offset from this block to its linked station; null follows the nearest. */
    private BlockPos link;

    // What is shown. Server: last computed. Client: from the update tag.
    private String name = "";
    private StationGlow glow = StationGlow.OFF;
    /** "hermes", "terminal", or "" when no station is in reach. */
    private String kind = "";
    /** Server: the station shown last. */
    private StationBlockEntity following;

    public StatusBlockEntity(BlockEntityType<? extends StatusBlockEntity> type, BlockPos pos, BlockState state) {
        super(type, pos, state);
    }

    public String name() {
        return name;
    }

    public StationGlow glow() {
        return glow;
    }

    public String kind() {
        return kind;
    }

    // -- the link -----------------------------------------------------------

    @Override
    public BlockPos linkedStation() {
        return link == null ? null : worldPosition.offset(link);
    }

    @Override
    public void setLinkedStation(BlockPos station) {
        link = station == null ? null : station.subtract(worldPosition);
        setChanged();
        StationStatus.refresh();
    }

    /**
     * The station this block follows now: its linked one (none if that is
     * gone or unloaded), or the nearest within {@link #REACH}. Looks only at
     * block entities in loaded chunks, so it never loads one.
     */
    StationBlockEntity station() {
        if (level == null) {
            return null;
        }
        if (link != null) {
            BlockPos p = worldPosition.offset(link);
            return level.isLoaded(p) && level.getBlockEntity(p) instanceof StationBlockEntity s ? s : null;
        }
        StationBlockEntity best = null;
        double bestDist = REACH * REACH + 1;
        for (int cx = (worldPosition.getX() - REACH) >> 4; cx <= (worldPosition.getX() + REACH) >> 4; cx++) {
            for (int cz = (worldPosition.getZ() - REACH) >> 4; cz <= (worldPosition.getZ() + REACH) >> 4; cz++) {
                LevelChunk chunk = level.getChunkSource().getChunkNow(cx, cz);
                if (chunk == null) {
                    continue;
                }
                for (BlockEntity be : chunk.getBlockEntities().values()) {
                    double d = be.getBlockPos().distSqr(worldPosition);
                    if (be instanceof StationBlockEntity s && d < bestDist && !s.isRemoved()) {
                        best = s;
                        bestDist = d;
                    }
                }
            }
        }
        return best;
    }

    StationBlockEntity following() {
        return following;
    }

    /** Show a station (null: none in reach) in a state. Clients hear only of changes. */
    void show(StationBlockEntity station, StationGlow newGlow, String newName) {
        following = station;
        String newKind = station == null ? "" : station.isHermes() ? "hermes" : "terminal";
        boolean changed = newGlow != glow || !newName.equals(name) || !newKind.equals(kind);
        glow = newGlow;
        name = newName;
        kind = newKind;
        if (level == null || level.isClientSide()) {
            return;
        }
        // A lamp's block state can differ from what it shows even when nothing
        // changed here: /setblock or a structure template sets it too.
        BlockState state = getBlockState();
        if (state.hasProperty(StatusLampBlock.GLOW) && state.getValue(StatusLampBlock.GLOW) != glow) {
            level.setBlock(worldPosition, state.setValue(StatusLampBlock.GLOW, glow), 3);
        }
        if (changed) {
            level.sendBlockUpdated(worldPosition, getBlockState(), getBlockState(), 3);
        }
    }

    /** One line for the action bar: what this block follows, and how it is. */
    public Component describe() {
        if (kind.isEmpty()) {
            return Component.translatable(link == null ? "omolife.status.none" : "omolife.status.link_gone", REACH);
        }
        Component station = Component.translatable("block.omolife." + (kind.equals("hermes") ? "hermes_computer" : "terminal"));
        Component state = Component.translatable("omolife.status." + glow.getSerializedName());
        return name.isEmpty()
            ? Component.translatable("omolife.status.unnamed", station, state)
            : Component.translatable(link == null ? "omolife.status.nearest" : "omolife.status.linked", name, station, state);
    }

    // -- lifecycle: the status watch knows every loaded plate and lamp --------

    @Override
    public void clearRemoved() {
        super.clearRemoved();
        if (level != null && !level.isClientSide()) {
            StationStatus.watch(this);
        }
    }

    @Override
    public void setRemoved() {
        super.setRemoved();
        StationStatus.unwatch(this);
    }

    // -- persistence: the link only -----------------------------------------

    @Override
    protected void saveAdditional(ValueOutput out) {
        super.saveAdditional(out);
        out.storeNullable(NBT_LINK, BlockPos.CODEC, link);
    }

    @Override
    protected void loadAdditional(ValueInput in) {
        super.loadAdditional(in);
        link = in.read(NBT_LINK, BlockPos.CODEC).orElse(null);
        name = in.getStringOr(TAG_NAME, name);
        kind = in.getStringOr(TAG_KIND, kind);
        String g = in.getStringOr(TAG_GLOW, glow.getSerializedName());
        for (StationGlow v : StationGlow.values()) {
            if (v.getSerializedName().equals(g)) {
                glow = v;
            }
        }
    }

    @Override
    public CompoundTag getUpdateTag(HolderLookup.Provider registries) {
        CompoundTag tag = super.getUpdateTag(registries);
        tag.putString(TAG_NAME, name);
        tag.putString(TAG_GLOW, glow.getSerializedName());
        tag.putString(TAG_KIND, kind);
        return tag;
    }

    @Override
    public ClientboundBlockEntityDataPacket getUpdatePacket() {
        return ClientboundBlockEntityDataPacket.create(this);
    }
}

package space.omo.life.link;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.world.level.Level;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.block.state.properties.BlockStateProperties;
import net.minecraft.world.level.storage.ValueInput;
import net.minecraft.world.level.storage.ValueOutput;

import space.omo.life.block.StationBlockEntity;
import space.omo.life.block.StationLinked;

/**
 * A screen the Omo Linker ties to a station, wirelessly: an Omo Monitor or
 * Slim Screen wall (it mirrors the station's screen). The Linker works on
 * every {@link StationLinked} block (Name Plates and Status Lamps too): it
 * links it, shows its line while held, and unlinks it on Shift+right-click;
 * nothing else to register. This adds the saved form and the station lookup.
 *
 * <p>The link is the station's position. In the world it is saved
 * <em>relative</em> to the linked block, with that block's facing, so a room
 * copied with {@code /clone}, a structure block (rotated too) or a world
 * copy keeps its links pointing at its own stations:
 * <pre>
 *   OmoLink: {X:-3, Y:0, Z:5, Facing:"north"}
 * </pre>
 * Nothing else: no workspace, no id, no secret. Use {@link #save} and
 * {@link #load} in the block entity's save/load and update tag, and
 * {@link #station} to read the station (its name, kind, workspace) on either
 * side; the client learns the link from the update tag.
 */
public interface Linkable extends StationLinked {

    String NBT = "OmoLink";
    /** Farthest a linked block may be from its station, in blocks. */
    int RANGE = 48;

    /** The linked station, if its chunk is loaded and it is still a station. */
    default StationBlockEntity station() {
        BlockPos at = linkedStation();
        Level level = ((BlockEntity) this).getLevel();
        return at != null && level != null && level.isLoaded(at)
            && level.getBlockEntity(at) instanceof StationBlockEntity st ? st : null;
    }

    // -- the saved form ---------------------------------------------------------------------

    /** Write {@code station} for {@code self}; nothing when unlinked. */
    static void save(ValueOutput out, BlockEntity self, BlockPos station) {
        if (station != null) {
            out.store(NBT, CompoundTag.CODEC, toTag(self, station));
        }
    }

    /** The station {@code self} is linked to, from saved data or an update tag; null when none. */
    static BlockPos load(ValueInput in, BlockEntity self) {
        return in.read(NBT, CompoundTag.CODEC).map(tag -> fromTag(self, tag)).orElse(null);
    }

    static CompoundTag toTag(BlockEntity self, BlockPos station) {
        BlockPos d = station.subtract(self.getBlockPos());
        CompoundTag tag = new CompoundTag();
        tag.putInt("X", d.getX());
        tag.putInt("Y", d.getY());
        tag.putInt("Z", d.getZ());
        facing(self.getBlockState()).ifPresent(f -> tag.putString("Facing", f.getSerializedName()));
        return tag;
    }

    /**
     * Back to a position. A block that was turned since the link was saved
     * (a rotated structure) turns its offset the same way.
     */
    static BlockPos fromTag(BlockEntity self, CompoundTag tag) {
        BlockPos d = new BlockPos(tag.getIntOr("X", 0), tag.getIntOr("Y", 0), tag.getIntOr("Z", 0));
        Direction saved = Direction.byName(tag.getStringOr("Facing", ""));
        Direction now = facing(self.getBlockState()).orElse(null);
        if (saved != null && now != null && saved.getAxis().isHorizontal()) {
            for (Direction f = saved; f != now; f = f.getClockWise()) {
                d = new BlockPos(-d.getZ(), d.getY(), d.getX());       // a quarter turn clockwise, seen from above
            }
        }
        return self.getBlockPos().offset(d);
    }

    private static java.util.Optional<Direction> facing(BlockState state) {
        return state.getOptionalValue(BlockStateProperties.HORIZONTAL_FACING);
    }
}

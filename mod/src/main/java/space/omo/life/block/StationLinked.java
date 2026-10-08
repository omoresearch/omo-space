package space.omo.life.block;

import net.minecraft.core.BlockPos;

/**
 * A block that follows one station: a Name Plate or a Status Lamp. The
 * Linker sets the station; until then (or after {@code null}) the block
 * follows the nearest station within {@link StatusBlockEntity#REACH} blocks.
 *
 * <p>The link is stored relative to the block, so a room saved as a
 * structure and placed elsewhere keeps pointing at its own station.
 */
public interface StationLinked {

    /** The linked station's position, or null to follow the nearest one. */
    BlockPos linkedStation();

    /** Link a station block at {@code station}, or pass null to follow the nearest. Server side. */
    void setLinkedStation(BlockPos station);
}

package space.omo.life.station;

import java.util.Optional;

import io.netty.buffer.ByteBuf;

import net.minecraft.core.BlockPos;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;

/**
 * How a screen names the station it shows, in a packet: a block by its
 * position, or an Omo Pocket by its station id alone (the server finds that
 * pocket in the player's own inventory). The station id always rides along,
 * so the server can check that the block at a position is still the station
 * the screen was opened on.
 */
public record StationTarget(String station, Optional<BlockPos> block) {

    public static final StreamCodec<ByteBuf, StationTarget> STREAM_CODEC = StreamCodec.composite(
        ByteBufCodecs.STRING_UTF8, StationTarget::station,
        ByteBufCodecs.optional(BlockPos.STREAM_CODEC), StationTarget::block,
        StationTarget::new);

    public static StationTarget block(BlockPos pos, String station) {
        return new StationTarget(station, Optional.of(pos.immutable()));
    }

    public static StationTarget pocket(String station) {
        return new StationTarget(station, Optional.empty());
    }
}

package space.omo.life.net;

import java.util.List;

import io.netty.buffer.ByteBuf;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.link.StationSetup;
import space.omo.life.station.StationPreset;
import space.omo.life.station.Stations;

/**
 * Packets for building with stations: the Station Setup screen (open it
 * with the station's settings, save them) and unlinking a monitor wall from
 * its settings screen. The server checks every one against
 * {@link Stations#allowed}, reach, and the station's lock.
 */
public final class LinkPackets {

    private static final double REACH_SQ = 64 * 64;

    private LinkPackets() {
    }

    /**
     * What the Station Setup edits: the workspace name, kind, Hermes profile
     * ("" = default), the tabs of a fresh workspace ("hermes:Name" /
     * "shell:Name") and the map maker's lock.
     */
    public record Setup(String name, boolean hermes, String profile, List<String> tabs, boolean locked) {
        public static final StreamCodec<ByteBuf, Setup> CODEC = StreamCodec.composite(
            ByteBufCodecs.STRING_UTF8, Setup::name,
            ByteBufCodecs.BOOL, Setup::hermes,
            ByteBufCodecs.STRING_UTF8, Setup::profile,
            ByteBufCodecs.STRING_UTF8.apply(ByteBufCodecs.list(16)), Setup::tabs,
            ByteBufCodecs.BOOL, Setup::locked,
            Setup::new);

        public static Setup of(StationPreset p, boolean locked) {
            return new Setup(p.name(), p.hermes(), p.profile(),
                p.tabs().stream().map(t -> (t.hermes() ? "hermes:" : "shell:") + t.name()).toList(), locked);
        }

        /** Back to a preset, trimmed to sane sizes. */
        public StationPreset preset() {
            List<StationPreset.Tab> t = tabs.stream().limit(8).map(s -> {
                boolean h = s.startsWith("hermes:");
                String n = s.substring(s.indexOf(':') + 1).strip();
                return new StationPreset.Tab(n.substring(0, Math.min(24, n.length())), h);
            }).toList();
            String n = name.strip();
            return new StationPreset(n.substring(0, Math.min(40, n.length())), hermes,
                "default".equals(profile) ? "" : profile.strip(), t);
        }
    }

    /**
     * Server to client: open the Station Setup for the station at {@code pos}.
     * {@code workspace} is "" while it has none; {@code profiles} are this
     * host's Hermes profiles (empty when the bridge could not say);
     * {@code note} explains anything missing.
     */
    public record OpenSetup(BlockPos pos, String station, String workspace, Setup setup, List<String> profiles,
                            String note) implements CustomPacketPayload {
        public static final Type<OpenSetup> TYPE = new Type<>(OmoLife.id("open_setup"));
        public static final StreamCodec<ByteBuf, OpenSetup> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, OpenSetup::pos,
            ByteBufCodecs.STRING_UTF8, OpenSetup::station,
            ByteBufCodecs.STRING_UTF8, OpenSetup::workspace,
            Setup.CODEC, OpenSetup::setup,
            ByteBufCodecs.STRING_UTF8.apply(ByteBufCodecs.list(256)), OpenSetup::profiles,
            ByteBufCodecs.STRING_UTF8, OpenSetup::note,
            OpenSetup::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: save the Station Setup. */
    public record SaveSetup(BlockPos pos, String station, Setup setup) implements CustomPacketPayload {
        public static final Type<SaveSetup> TYPE = new Type<>(OmoLife.id("save_setup"));
        public static final StreamCodec<ByteBuf, SaveSetup> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, SaveSetup::pos,
            ByteBufCodecs.STRING_UTF8, SaveSetup::station,
            Setup.CODEC, SaveSetup::setup,
            SaveSetup::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: unlink the monitor wall at {@code pos} (its settings screen). */
    public record Unlink(BlockPos pos) implements CustomPacketPayload {
        public static final Type<Unlink> TYPE = new Type<>(OmoLife.id("unlink"));
        public static final StreamCodec<ByteBuf, Unlink> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, Unlink::pos,
            Unlink::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    public static void register() {
        PayloadTypeRegistry.clientboundPlay().register(OpenSetup.TYPE, OpenSetup.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(SaveSetup.TYPE, SaveSetup.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(Unlink.TYPE, Unlink.CODEC);
        ServerPlayNetworking.registerGlobalReceiver(SaveSetup.TYPE, (p, ctx) ->
            StationSetup.save(ctx.player(), p.pos(), p.station(), p.setup()));
        ServerPlayNetworking.registerGlobalReceiver(Unlink.TYPE, (p, ctx) -> {
            ServerPlayer player = ctx.player();
            if (!near(player, p.pos())) {
                return;
            }
            if (!Stations.allowed(player)) {
                Stations.refuse(player);
            } else if (player.level().getBlockEntity(p.pos()) instanceof OmoMonitorBlockEntity wall) {
                wall.setLinkedStation(null);
            }
        });
    }

    /** Loaded and within reach of the player. */
    public static boolean near(ServerPlayer player, BlockPos pos) {
        return player.level().isLoaded(pos) && player.blockPosition().distSqr(pos) <= REACH_SQ;
    }
}

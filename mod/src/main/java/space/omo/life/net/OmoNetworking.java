package space.omo.life.net;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.network.RegistryFriendlyByteBuf;
import net.minecraft.network.chat.Component;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.Level;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.builder.OmoAdvancements;
import space.omo.life.station.Stations;

/**
 * Packets between the client screens and the server. Deliberately small:
 * the client talks to the bridge directly for tab controls (same machine,
 * same user), so the server only handles what touches world state: a
 * monitor wall's page binding and which screen to open here, a station's
 * workspace in {@link StationPackets}. Every serverbound packet is checked against
 * {@link Stations#allowed}, like the block interactions that open the screens.
 */
public final class OmoNetworking {

    private static final double REACH_SQ = 64 * 64;

    private OmoNetworking() {
    }

    /** Server to client: open the controls for the monitor wall whose origin panel is {@code origin}. */
    public record OpenMonitor(BlockPos origin, String source, String url, int zoom, int cols, int rows)
        implements CustomPacketPayload {
        public static final Type<OpenMonitor> TYPE = new Type<>(OmoLife.id("open_monitor"));
        public static final StreamCodec<RegistryFriendlyByteBuf, OpenMonitor> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, OpenMonitor::origin,
            ByteBufCodecs.STRING_UTF8, OpenMonitor::source,
            ByteBufCodecs.STRING_UTF8, OpenMonitor::url,
            ByteBufCodecs.VAR_INT, OpenMonitor::zoom,
            ByteBufCodecs.VAR_INT, OpenMonitor::cols,
            ByteBufCodecs.VAR_INT, OpenMonitor::rows,
            OpenMonitor::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Client to server: bind a monitor wall to a terminal or a page. */
    public record SetMonitor(BlockPos pos, String source, String url, int zoom) implements CustomPacketPayload {
        public static final Type<SetMonitor> TYPE = new Type<>(OmoLife.id("set_monitor"));
        public static final StreamCodec<RegistryFriendlyByteBuf, SetMonitor> CODEC = StreamCodec.composite(
            BlockPos.STREAM_CODEC, SetMonitor::pos,
            ByteBufCodecs.STRING_UTF8, SetMonitor::source,
            ByteBufCodecs.STRING_UTF8, SetMonitor::url,
            ByteBufCodecs.VAR_INT, SetMonitor::zoom,
            SetMonitor::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    public static void registerCommon() {
        PayloadTypeRegistry.clientboundPlay().register(OpenMonitor.TYPE, OpenMonitor.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(SetMonitor.TYPE, SetMonitor.CODEC);

        MonitorPackets.register();

        ServerPlayNetworking.registerGlobalReceiver(SetMonitor.TYPE, (payload, ctx) -> {
            ServerPlayer player = ctx.player();
            if (!reachable(player, payload.pos())
                || !(player.level().getBlockEntity(payload.pos()) instanceof OmoMonitorBlockEntity panel)) {
                return;
            }
            OmoMonitorBlockEntity be = player.level().getBlockEntity(panel.origin()) instanceof OmoMonitorBlockEntity o
                ? o : panel;
            if (OmoMonitorBlockEntity.SOURCE_WEB.equals(payload.source())) {
                String url = OmoMonitorBlockEntity.safeUrl(payload.url());
                if (url == null) {
                    player.sendSystemMessage(Component.literal("That address is not allowed on a monitor."), true);
                    return;
                }
                be.setWeb(url, payload.zoom());
                OmoAdvancements.award(player, OmoAdvancements.PIN_PAGE);
            } else {
                be.setTerminal();
            }
            be.shareWithWall();
            OmoLife.LOG.info("monitor {} bound: {} {} zoom={}", be.getBlockPos().toShortString(),
                be.source(), be.url(), be.zoom());
        });

        StationPackets.register();
    }

    /** Loaded, near, and allowed; a refused player is told why. */
    private static boolean reachable(ServerPlayer player, BlockPos pos) {
        Level level = player.level();
        if (!level.isLoaded(pos) || player.blockPosition().distSqr(pos) > REACH_SQ) {
            return false;
        }
        if (!Stations.allowed(player)) {
            Stations.refuse(player);
            return false;
        }
        return true;
    }

    public static void openMonitorScreen(ServerPlayer player, OmoMonitorBlockEntity clicked) {
        OmoMonitorBlockEntity be = player.level().getBlockEntity(clicked.origin()) instanceof OmoMonitorBlockEntity o
            ? o : clicked;
        ServerPlayNetworking.send(player, new OpenMonitor(be.getBlockPos(), be.source(), be.url(), be.zoom(),
            be.getWidth(), be.getHeight()));
    }
}

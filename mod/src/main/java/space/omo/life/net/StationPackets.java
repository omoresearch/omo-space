package space.omo.life.net;

import com.google.gson.JsonObject;

import io.netty.buffer.ByteBuf;

import net.fabricmc.fabric.api.networking.v1.PayloadTypeRegistry;
import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.network.codec.ByteBufCodecs;
import net.minecraft.network.codec.StreamCodec;
import net.minecraft.network.protocol.common.custom.CustomPacketPayload;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.pocket.PocketStation;
import space.omo.life.station.BridgeLink;
import space.omo.life.station.Station;
import space.omo.life.station.StationTarget;
import space.omo.life.station.Stations;

/**
 * The station panel's packets. Tab controls go straight from the client to
 * the bridge; these are the actions that touch world state (which workspace
 * a station shows, its name), so the server does them after
 * {@link Stations#allowed}. A block station is addressed by position, a
 * pocket by its station id in the player's own inventory ({@link StationTarget}).
 */
public final class StationPackets {

    private static final double REACH_SQ = 64 * 64;

    private StationPackets() {
    }

    /**
     * Client to server, one of:
     * <pre>
     *   ensure             create the station's workspace if it has none
     *   refresh            send the station's binding again
     *   rename  name       rename its workspace
     *   switch  ws-id      show another existing workspace (nothing is stopped)
     *   create  kind:name  make a new workspace ("hermes" or "terminal") and show it
     * </pre>
     */
    public record Action(StationTarget target, String action, String value) implements CustomPacketPayload {
        public static final Type<Action> TYPE = new Type<>(OmoLife.id("station_action"));
        public static final StreamCodec<ByteBuf, Action> CODEC = StreamCodec.composite(
            StationTarget.STREAM_CODEC, Action::target,
            ByteBufCodecs.STRING_UTF8, Action::action,
            ByteBufCodecs.STRING_UTF8, Action::value,
            Action::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    /** Server to client: a station's binding (workspace, name), with an optional message. */
    public record Info(String station, String workspace, String name, boolean hermes, String message)
        implements CustomPacketPayload {
        public static final Type<Info> TYPE = new Type<>(OmoLife.id("station_info"));
        public static final StreamCodec<ByteBuf, Info> CODEC = StreamCodec.composite(
            ByteBufCodecs.STRING_UTF8, Info::station,
            ByteBufCodecs.STRING_UTF8, Info::workspace,
            ByteBufCodecs.STRING_UTF8, Info::name,
            ByteBufCodecs.BOOL, Info::hermes,
            ByteBufCodecs.STRING_UTF8, Info::message,
            Info::new);

        @Override
        public Type<? extends CustomPacketPayload> type() {
            return TYPE;
        }
    }

    public static void register() {
        PayloadTypeRegistry.clientboundPlay().register(Info.TYPE, Info.CODEC);
        PayloadTypeRegistry.serverboundPlay().register(Action.TYPE, Action.CODEC);
        ServerPlayNetworking.registerGlobalReceiver(Action.TYPE, (payload, ctx) -> {
            ServerPlayer player = ctx.player();
            if (!Stations.allowed(player)) {
                Stations.refuse(player);
                return;
            }
            Station station = resolve(player, payload.target());
            if (station != null) {
                act(player, station, payload.action(), payload.value().strip());
            }
        });
    }

    /** The station a target names, if this player may reach it; else null. */
    private static Station resolve(ServerPlayer player, StationTarget target) {
        if (target.block().isEmpty()) {
            return PocketStation.carried(player, target.station());
        }
        BlockPos pos = target.block().get();
        if (player.level().isLoaded(pos) && player.blockPosition().distSqr(pos) <= REACH_SQ
            && player.level().getBlockEntity(pos) instanceof StationBlockEntity be
            && be.stationIdOrEmpty().equals(target.station())) {
            return be;
        }
        return null;
    }

    private static void act(ServerPlayer player, Station station, String action, String value) {
        if (station instanceof StationBlockEntity be && be.lockedFor(player)
            && ("rename".equals(action) || "switch".equals(action) || "create".equals(action))) {
            sendInfo(player, station, "Locked by the map maker: this station keeps its workspace and name.");
            return;
        }
        switch (action) {
            case "ensure" -> {
                station.ensureWorkspace();
                sendInfo(player, station, station.isBound() ? "" : (BridgeLink.available()
                    ? "creating workspace..." : "Omo bridge is not running on this computer"));
            }
            case "refresh" -> sendInfo(player, station, "");
            case "rename" -> {
                if (value.isEmpty() || value.length() > 40 || !station.isBound()) {
                    return;
                }
                Stations.onServer(player.level().getServer(), Stations.renameWorkspace(station.workspace(), value),
                    (JsonObject reply) -> {
                        if (!reply.has("error")) {
                            station.setWorkspaceName(reply.get("name").getAsString());
                        }
                        sendInfo(player, station, reply.has("error") ? reply.get("error").getAsString() : "");
                    });
            }
            case "switch" -> Stations.onServer(player.level().getServer(), Stations.getWorkspace(value),
                (JsonObject reply) -> {
                    if (reply.has("error")) {
                        sendInfo(player, station, reply.get("error").getAsString());
                        return;
                    }
                    station.bind(reply.get("id").getAsString(), reply.get("name").getAsString());
                    OmoLife.LOG.info("station {} switched to workspace {} '{}'", station.stationIdOrEmpty(),
                        station.workspace(), station.workspaceName());
                    sendInfo(player, station, "");
                });
            case "create" -> {
                int colon = value.indexOf(':');
                boolean hermes = value.startsWith("hermes:");
                String name = colon < 0 ? "" : value.substring(colon + 1).strip();
                Stations.createFor(player.level().getServer(), station, name.substring(0, Math.min(40, name.length())),
                    hermes, error -> sendInfo(player, station, error == null ? "" : error));
            }
            default -> { }
        }
    }

    public static void sendInfo(ServerPlayer player, Station station, String message) {
        ServerPlayNetworking.send(player, new Info(station.stationIdOrEmpty(), station.workspace(),
            station.workspaceName(), station.isHermes(), message == null ? "" : message));
    }

    /** A station's binding and a note for everyone who may use stations: an open screen on it toasts the note. */
    public static void tellAll(MinecraftServer server, Station station, String message) {
        for (ServerPlayer player : server.getPlayerList().getPlayers()) {
            if (Stations.allowed(player)) {
                sendInfo(player, station, message);
            }
        }
    }
}

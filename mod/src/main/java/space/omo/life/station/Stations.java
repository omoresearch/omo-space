package space.omo.life.station;

import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;
import java.util.function.Consumer;

import com.google.gson.JsonObject;

import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.OmoLife;
import space.omo.life.net.StationPackets;

/**
 * Server-side helpers shared by the station block entity, commands, packets
 * and the Lua API. Small on purpose: the bridge owns all workspace state.
 */
public final class Stations {

    private Stations() {
    }

    // -- who may touch host state ---------------------------------------------

    /**
     * The one access rule for everything that reaches the host: the
     * singleplayer world owner, or an operator. Opening a station, changing a
     * monitor, the station packets and {@code /omo} all ask this.
     */
    public static boolean allowed(ServerPlayer player) {
        MinecraftServer server = player.level().getServer();
        return server != null && (server.isSingleplayerOwner(player.nameAndId())
            || server.getPlayerList().isOp(player.nameAndId()));
    }

    /** Tell a player, on the action bar, why nothing happened. */
    public static void refuse(ServerPlayer player) {
        player.sendSystemMessage(Component.literal("Only the world owner can use Omo stations and monitors here.")
            .withStyle(ChatFormatting.GOLD), true);
    }

    // -- bridge calls ---------------------------------------------------------

    /** Create a workspace for a station: named, of a kind, optionally on a Hermes profile and with set tabs. */
    public static CompletableFuture<JsonObject> createWorkspace(StationPreset preset, String station, String world) {
        return BridgeLink.api("create", Map.of(
            "name", preset.name(),
            "kind", preset.hermes() ? "hermes" : "terminal",
            "profile", preset.profile(),
            "tabs", preset.tabsParam(),
            "station", station,
            "world", world));
    }

    /**
     * Create a workspace on the host and bind {@code station} to it. {@code done}
     * runs on the server thread with the bridge's error, or null once bound.
     * Callers guard against creating twice.
     */
    public static void createFor(MinecraftServer server, Station station, String name, boolean hermes,
                                 Consumer<String> done) {
        createFor(server, station, new StationPreset(name == null ? "" : name, hermes, "", List.of()), done);
    }

    /**
     * The same, as a preset asks. A Hermes profile this host does not have
     * falls back to the default one, and the players who may use stations
     * are told so.
     */
    public static void createFor(MinecraftServer server, Station station, StationPreset preset,
                                 Consumer<String> done) {
        onServer(server, createWorkspace(preset, station.stationIdOrEmpty(), worldName(server)),
            (JsonObject reply) -> {
                String error = reply.has("error") ? reply.get("error").getAsString() : null;
                if (error != null && !preset.profile().isEmpty() && error.startsWith("no Hermes profile")) {
                    String note = "No Hermes profile '" + preset.profile() + "' here; using default.";
                    createFor(server, station, preset.withoutProfile(), e -> {
                        if (e == null) {
                            StationPackets.tellAll(server, station, note);
                        }
                        done.accept(e);
                    });
                    return;
                }
                if (error != null) {
                    OmoLife.LOG.warn("station {}: workspace not created: {}", station.stationIdOrEmpty(), error);
                    done.accept(error);
                    return;
                }
                if (station.isPresent()) {
                    station.bind(reply.get("id").getAsString(), reply.get("name").getAsString());
                    OmoLife.LOG.info("station {} bound to workspace {} '{}'", station.stationIdOrEmpty(),
                        station.workspace(), station.workspaceName());
                }
                done.accept(null);
            });
    }

    public static CompletableFuture<JsonObject> getWorkspace(String ws) {
        return BridgeLink.api("get", Map.of("ws", ws));
    }

    public static CompletableFuture<JsonObject> renameWorkspace(String ws, String name) {
        return BridgeLink.api("rename", Map.of("ws", ws, "name", name));
    }

    public static CompletableFuture<JsonObject> listWorkspaces() {
        return BridgeLink.api("list", Map.of());
    }

    /** Hermes profile names on this host, {@code {"items": [...]}}, "default" first. */
    public static CompletableFuture<JsonObject> listProfiles() {
        return BridgeLink.api("profiles", Map.of());
    }

    /** Pin a workspace to a Hermes profile ("" or "default" = the default profile). */
    public static CompletableFuture<JsonObject> setProfile(String ws, String profile) {
        return BridgeLink.api("profile", Map.of("ws", ws, "profile", profile));
    }

    /** Run on the server thread once a bridge future completes. */
    public static <T> void onServer(MinecraftServer server, CompletableFuture<T> future, Consumer<T> then) {
        future.thenAccept(v -> server.execute(() -> then.accept(v)));
    }

    /**
     * A human-meaningful name for a world, used only for bookkeeping
     * ("last used from world X"). Never part of identity.
     */
    public static String worldName(MinecraftServer server) {
        try {
            return server.getWorldData().getLevelName();
        } catch (RuntimeException e) {
            return "world";
        }
    }
}

package space.omo.life.link;

import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;

import com.google.gson.JsonObject;

import net.fabricmc.fabric.api.networking.v1.ServerPlayNetworking;
import net.minecraft.core.BlockPos;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.OmoLife;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.net.LinkPackets;
import space.omo.life.net.LinkPackets.Setup;
import space.omo.life.net.StationPackets;
import space.omo.life.station.StationPreset;
import space.omo.life.station.Stations;

/**
 * The Station Setup, server side: what a station is (name, kind, Hermes
 * profile, the tabs a fresh workspace gets, the lock), edited in game
 * instead of with NBT commands.
 *
 * <p>It always edits the station's recipe ({@code OmoPreset}), which is
 * what a copy of the map on another PC builds its workspace from. A station
 * that already has a workspace is also renamed and re-profiled through the
 * bridge at once; its tabs stay as they are (the station panel adds tabs).
 *
 * <p>World owner or op only; a locked station only in creative mode.
 */
public final class StationSetup {

    private StationSetup() {
    }

    public static void open(ServerPlayer player, StationBlockEntity station) {
        if (!Stations.allowed(player)) {
            Stations.refuse(player);
            return;
        }
        if (station.lockedFor(player)) {
            Links.say(player, "This station is locked by the map maker (creative mode can change it).", false);
            return;
        }
        MinecraftServer server = player.level().getServer();
        CompletableFuture<JsonObject> ws = station.isBound() ? Stations.getWorkspace(station.workspace())
            : CompletableFuture.completedFuture(new JsonObject());
        Stations.onServer(server, Stations.listProfiles().thenCombine(ws, (profiles, desc) -> new JsonObject[]{
            profiles, desc}), replies -> {
                if (station.isRemoved()) {
                    return;
                }
                JsonObject profiles = replies[0], desc = replies[1];
                List<String> names = new ArrayList<>();
                if (profiles.has("items")) {
                    profiles.getAsJsonArray("items").forEach(e -> names.add(e.getAsString()));
                }
                StationPreset recipe = station.recipe();
                String profile = desc.has("profile") && !desc.get("profile").isJsonNull()
                    ? desc.get("profile").getAsString() : recipe.profile();
                StationPreset shown = new StationPreset(recipe.name(), station.isHermes(), profile, recipe.tabs());
                String note = profiles.has("error") ? "Bridge: " + profiles.get("error").getAsString() : "";
                ServerPlayNetworking.send(player, new LinkPackets.OpenSetup(station.getBlockPos(), station.station(),
                    station.workspace(), Setup.of(shown, station.isLocked()), names, note));
            });
    }

    /** Save what the screen sent: the recipe and lock, and for a bound station its name and profile now. */
    public static void save(ServerPlayer player, BlockPos pos, String stationId, Setup setup) {
        if (!LinkPackets.near(player, pos)
            || !(player.level().getBlockEntity(pos) instanceof StationBlockEntity station)
            || !station.stationIdOrEmpty().equals(stationId)) {
            return;
        }
        if (!Stations.allowed(player)) {
            Stations.refuse(player);
            return;
        }
        if (station.lockedFor(player)) {
            Links.say(player, "This station is locked by the map maker.", false);
            return;
        }
        StationPreset recipe = setup.preset();
        station.configure(recipe, setup.locked());
        OmoLife.LOG.info("{} set up station {}: {} {} profile='{}' tabs={} locked={}", player.getPlainTextName(),
            pos.toShortString(), recipe.name(), recipe.hermes() ? "hermes" : "terminal", recipe.profile(),
            recipe.tabs().size(), setup.locked());
        if (!station.isBound()) {
            Links.say(player, "Saved. Its workspace is made from this setup.", true);
            return;
        }
        MinecraftServer server = player.level().getServer();
        String ws = station.workspace();
        CompletableFuture<JsonObject> rename = recipe.name().isEmpty() || recipe.name().equals(station.workspaceName())
            ? CompletableFuture.completedFuture(new JsonObject()) : Stations.renameWorkspace(ws, recipe.name());
        Stations.onServer(server, rename.thenCombine(Stations.setProfile(ws, recipe.profile()),
            (renamed, profiled) -> new JsonObject[]{renamed, profiled}), replies -> {
                String error = replies[0].has("error") ? replies[0].get("error").getAsString()
                    : replies[1].has("error") ? replies[1].get("error").getAsString() : null;
                if (replies[0].has("name")) {
                    station.setWorkspaceName(replies[0].get("name").getAsString());
                }
                StationPackets.tellAll(server, station, "");
                Links.say(player, error == null ? "Saved: " + Links.label(station.displayName(), pos) + "."
                    : "Saved, but the bridge said: " + error, error == null);
            });
    }
}

package space.omo.life.station;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.mojang.brigadier.arguments.StringArgumentType;
import com.mojang.brigadier.suggestion.Suggestions;
import com.mojang.brigadier.suggestion.SuggestionsBuilder;

import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.ChatFormatting;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.commands.SharedSuggestionProvider;
import net.minecraft.network.chat.ClickEvent;
import net.minecraft.network.chat.Component;
import net.minecraft.network.chat.MutableComponent;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.HitResult;

import space.omo.life.block.StationBlockEntity;
import space.omo.life.pocket.PocketStation;

/**
 * {@code /omo}: keyboard-first controls that mirror the station panel.
 *
 * <pre>
 *   /omo status                  bridge + the station you look at (or the Omo Pocket in your hand)
 *   /omo workspaces              host workspaces (click one to rebind that station)
 *   /omo rebind &lt;id&gt;             point that station to an existing workspace
 *   /omo rename &lt;name&gt;           rename that station's workspace
 *   /omo profile [&lt;name&gt;]        show / set that workspace's Hermes profile
 *   /omo forget &lt;id&gt; [force]     close a workspace on the host (asks first if work is running)
 * </pre>
 * Only the world owner (or an operator) may use them: they touch host state.
 */
public final class StationCommands {

    private StationCommands() {
    }

    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, ctx, env) -> dispatcher.register(
            Commands.literal("omo")
                .requires(StationCommands::allowed)
                .then(Commands.literal("status").executes(c -> status(c.getSource())))
                .then(Commands.literal("workspaces").executes(c -> workspaces(c.getSource())))
                .then(Commands.literal("rebind").then(Commands.argument("id", StringArgumentType.word())
                    .executes(c -> rebind(c.getSource(), StringArgumentType.getString(c, "id")))))
                .then(Commands.literal("rename").then(Commands.argument("name", StringArgumentType.greedyString())
                    .executes(c -> rename(c.getSource(), StringArgumentType.getString(c, "name")))))
                .then(Commands.literal("profile")
                    .executes(c -> profiles(c.getSource()))
                    .then(Commands.argument("name", StringArgumentType.word())
                        .suggests((c, b) -> suggestProfiles(b))
                        .executes(c -> profile(c.getSource(), StringArgumentType.getString(c, "name")))))
                .then(Commands.literal("forget").then(Commands.argument("id", StringArgumentType.word())
                    .executes(c -> forget(c.getSource(), StringArgumentType.getString(c, "id"), false))
                    .then(Commands.literal("force")
                        .executes(c -> forget(c.getSource(), StringArgumentType.getString(c, "id"), true)))))
                .executes(c -> status(c.getSource()))));
    }

    private static boolean allowed(CommandSourceStack src) {
        if (!src.isPlayer()) {
            return true;                      // console / command blocks run as the server
        }
        ServerPlayer p = src.getPlayer();
        return p != null && Stations.allowed(p);
    }

    /** The station block the player looks at, else the running Omo Pocket in their hand. */
    private static Station lookedAt(CommandSourceStack src) {
        ServerPlayer p = src.getPlayer();
        if (p == null) {
            return null;
        }
        HitResult hit = p.pick(6.0, 0.0F, false);
        if (hit instanceof BlockHitResult bh && p.level().getBlockEntity(bh.getBlockPos()) instanceof StationBlockEntity be) {
            return be;
        }
        return PocketStation.held(p);
    }

    private static Station boundLookedAt(CommandSourceStack src) {
        Station be = lookedAt(src);
        if (be == null || !be.isBound()) {
            src.sendFailure(Component.literal(
                "Look at a Hermes Computer or Terminal (or hold an Omo Pocket) with a workspace first."));
            return null;
        }
        return be;
    }

    /** Report a bridge error; true when there was one. */
    private static boolean failed(CommandSourceStack src, JsonObject reply) {
        if (reply.has("error")) {
            src.sendFailure(Component.literal(reply.get("error").getAsString()));
            return true;
        }
        return false;
    }

    private static int status(CommandSourceStack src) {
        Station be = lookedAt(src);
        MutableComponent msg = Component.literal("Omo bridge: ").withStyle(ChatFormatting.GOLD);
        BridgeLink.health().thenAccept(ok -> src.getServer().execute(() -> {
            msg.append(Component.literal(ok ? "connected" : "not running")
                .withStyle(ok ? ChatFormatting.GREEN : ChatFormatting.RED));
            if (!ok) {
                msg.append(Component.literal("  " + BridgeLink.describeSetupHint()).withStyle(ChatFormatting.GRAY));
            }
            if (be != null) {
                msg.append("\n").append(Component.literal("Station: ").withStyle(ChatFormatting.GOLD))
                    .append(be.isBound() ? be.workspaceName() + " (" + be.workspace() + ")" : "no workspace yet");
            }
            src.sendSuccess(() -> msg, false);
        }));
        return 1;
    }

    private static int workspaces(CommandSourceStack src) {
        Stations.onServer(src.getServer(), Stations.listWorkspaces(), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            JsonArray items = reply.getAsJsonArray("items");
            MutableComponent out = Component.literal("Host workspaces (click to bind the station you look at):")
                .withStyle(ChatFormatting.GOLD);
            for (JsonElement el : items) {
                JsonObject w = el.getAsJsonObject();
                String id = w.get("id").getAsString();
                String state = w.has("state") ? w.get("state").getAsString()
                    : (w.get("alive").getAsBoolean() ? "live" : "interrupted");
                ChatFormatting colour = switch (state) {
                    case "live" -> ChatFormatting.GREEN;
                    case "ended" -> ChatFormatting.YELLOW;
                    default -> ChatFormatting.RED;
                };
                int tabs = w.getAsJsonArray("tabs").size();
                String profile = w.has("profile") ? w.get("profile").getAsString() : "";
                out.append("\n ").append(Component.literal(w.get("name").getAsString())
                    .withStyle(s -> s.withColor(colour).withUnderlined(true)
                        .withClickEvent(new ClickEvent.RunCommand("/omo rebind " + id))))
                    .append(Component.literal("  " + id + "  " + tabs + " tab" + (tabs == 1 ? "" : "s")
                        + (profile.isEmpty() ? "" : "  profile " + profile)
                        + ("live".equals(state) ? "" : "  " + state)).withStyle(ChatFormatting.GRAY));
            }
            if (items.isEmpty()) {
                out.append("\n (none yet - place a Hermes Computer or Terminal)");
            }
            src.sendSuccess(() -> out, false);
        });
        return 1;
    }

    /** A map maker's lock holds for commands too: use yes, rename/rebind/re-profile no (outside creative). */
    private static boolean locked(CommandSourceStack src, Station be) {
        if (be instanceof StationBlockEntity station && src.getPlayer() != null && station.lockedFor(src.getPlayer())) {
            src.sendFailure(Component.literal("This station is locked by the map's builder."));
            return true;
        }
        return false;
    }

    private static int rebind(CommandSourceStack src, String id) {
        Station be = lookedAt(src);
        if (be == null) {
            src.sendFailure(Component.literal("Look at a Hermes Computer or Terminal (or hold an Omo Pocket) first."));
            return 0;
        }
        if (locked(src, be)) {
            return 0;
        }
        Stations.onServer(src.getServer(), Stations.getWorkspace(id), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            be.bind(id, reply.get("name").getAsString());
            src.sendSuccess(() -> Component.literal("Station now shows workspace '" + be.workspaceName() + "'."),
                false);
        });
        return 1;
    }

    private static int rename(CommandSourceStack src, String name) {
        Station be = boundLookedAt(src);
        if (be == null || locked(src, be)) {
            return 0;
        }
        Stations.onServer(src.getServer(), Stations.renameWorkspace(be.workspace(), name), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            be.setWorkspaceName(reply.get("name").getAsString());
            src.sendSuccess(() -> Component.literal("Workspace renamed to '" + be.workspaceName() + "'."), false);
        });
        return 1;
    }

    private static List<String> names(JsonObject reply) {
        List<String> out = new ArrayList<>();
        if (reply.has("items")) {
            for (JsonElement el : reply.getAsJsonArray("items")) {
                out.add(el.getAsString());
            }
        }
        return out;
    }

    private static CompletableFuture<Suggestions> suggestProfiles(SuggestionsBuilder builder) {
        return Stations.listProfiles().thenCompose(reply -> SharedSuggestionProvider.suggest(names(reply), builder));
    }

    private static int profiles(CommandSourceStack src) {
        Station be = lookedAt(src);
        Stations.onServer(src.getServer(), Stations.listProfiles(), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            MutableComponent out = Component.literal("Hermes profiles (click to use one for the station you look at):")
                .withStyle(ChatFormatting.GOLD);
            if (be != null && be.isBound()) {
                out.append(Component.literal("\n workspace '" + be.workspaceName() + "'").withStyle(ChatFormatting.GRAY));
            }
            MutableComponent row = Component.literal("\n ");
            for (String name : names(reply)) {
                row.append(Component.literal(name).withStyle(s -> s.withColor(ChatFormatting.YELLOW).withUnderlined(true)
                    .withClickEvent(new ClickEvent.RunCommand("/omo profile " + name)))).append("  ");
            }
            out.append(row);
            src.sendSuccess(() -> out, false);
        });
        return 1;
    }

    private static int profile(CommandSourceStack src, String name) {
        Station be = boundLookedAt(src);
        if (be == null || locked(src, be)) {
            return 0;
        }
        Stations.onServer(src.getServer(), Stations.setProfile(be.workspace(), name), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            String p = reply.has("profile") ? reply.get("profile").getAsString() : name;
            src.sendSuccess(() -> Component.literal("Workspace '" + be.workspaceName() + "' now uses Hermes profile '"
                + (p.isEmpty() ? "default" : p) + "' for new Hermes tabs and resumes. Running tabs are untouched."), false);
        });
        return 1;
    }

    private static int forget(CommandSourceStack src, String id, boolean force) {
        Map<String, String> params = Map.of("ws", id, "force", force ? "1" : "0");
        Stations.onServer(src.getServer(), BridgeLink.api("delete", params), (JsonObject reply) -> {
            if (failed(src, reply)) {
                return;
            }
            String m = reply.get("message").getAsString();
            if (reply.get("ok").getAsBoolean()) {
                src.sendSuccess(() -> Component.literal(m), false);
            } else {
                src.sendSuccess(() -> Component.literal(m + "  ").append(Component.literal("[close anyway]")
                    .withStyle(s -> s.withColor(ChatFormatting.RED).withUnderlined(true)
                        .withClickEvent(new ClickEvent.RunCommand("/omo forget " + id + " force")))), false);
            }
        });
        return 1;
    }
}

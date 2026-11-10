package space.omo.life.world;

import com.google.gson.JsonObject;
import com.mojang.brigadier.arguments.IntegerArgumentType;

import net.fabricmc.fabric.api.command.v2.CommandRegistrationCallback;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.commands.Commands;
import net.minecraft.network.chat.Component;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.client.BridgeClient;
import space.omo.life.station.CommandGate;

/**
 * {@code /omo sense} — describe the world around you, deterministically,
 * and hand that description to the bridge so any outside brain can read it.
 *
 * <pre>
 *   /omo sense [radius]   radius in chunks, 0 (one chunk) or 1 (3x3). Default 0.
 *   /omo describe         same, but prints the short summary in chat too.
 * </pre>
 *
 * Same access rule as the rest of {@code /omo}: the world owner or an
 * operator (see {@link CommandGate}). A snapshot never leaves the PC
 * except to the loopback bridge; nothing here calls any model.
 */
public final class WorldSenseCommand {

    private WorldSenseCommand() {
    }

    public static void register() {
        CommandRegistrationCallback.EVENT.register((dispatcher, ctx, env) -> dispatcher.register(
            Commands.literal("omo")
                .requires(CommandGate::trusted)
                .then(Commands.literal("sense")
                    .executes(c -> sense(c.getSource(), 0, false))
                    .then(Commands.argument("radius", IntegerArgumentType.integer(0, WorldSense.MAX_RADIUS))
                        .executes(c -> sense(c.getSource(), IntegerArgumentType.getInteger(c, "radius"), false))))
                .then(Commands.literal("describe")
                    .executes(c -> sense(c.getSource(), 0, true))
                    .then(Commands.argument("radius", IntegerArgumentType.integer(0, WorldSense.MAX_RADIUS))
                        .executes(c -> sense(c.getSource(), IntegerArgumentType.getInteger(c, "radius"), true))))));
    }

    private static int sense(CommandSourceStack src, int radius, boolean print) {
        ServerPlayer p = src.getPlayer();
        ServerLevel level = src.getLevel();
        if (p == null) {
            src.sendFailure(Component.literal("Run /omo sense in game, standing where you want described."));
            return 0;
        }
        JsonObject snap = WorldSense.snapshot(level, p.blockPosition(), radius);
        int chunks = snap.getAsJsonArray("chunks").size();
        JsonObject frame = new JsonObject();
        frame.addProperty("t", "world_snapshot");
        frame.add("snapshot", snap);
        BridgeClient.get().ensureConnected();
        BridgeClient.get().send(frame);
        String msg = "World Sense: " + chunks + " chunk(s) around "
            + p.getBlockX() + "," + p.getBlockY() + "," + p.getBlockZ()
            + " sent to the bridge — any brain can now read /world/describe.";
        if (print) {
            src.sendSuccess(() -> Component.literal(msg
                + " Players nearby: " + snap.getAsJsonArray("players").size()
                + ", entities: " + snap.getAsJsonArray("entities").size() + "."), false);
        } else {
            src.sendSuccess(() -> Component.literal(msg), false);
        }
        return 1;
    }
}

package space.omo.life.world;

import java.util.Set;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.minecraft.core.BlockPos;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.network.chat.Component;
import net.minecraft.resources.ResourceLocation;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.level.block.Block;
import net.minecraft.world.level.block.Blocks;
import net.minecraft.world.level.block.entity.SignBlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.client.AgentLan;
import space.omo.life.client.BridgeClient;

/**
 * Executes validated World Sense write tools, in the game.
 *
 * <p>Trust model — read this before turning it on:
 * <ul>
 *   <li>Off by default. Writes run only with {@code -Domo.world.tools=true}.
 *   <li>Frames only arrive over the loopback bridge (the mod only ever
 *       connects to 127.0.0.1), and only from a UI-role connection — a
 *       station screen can never send one.
 *   <li>The bridge already validated the call (tool name, region size, text
 *       length — see {@code bridge/world_sense.py}); this side re-checks
 *       the block count before touching a block.
 *   <li>Execution is on the server thread, in the single-player integrated
 *       server. Dedicated servers have no client bridge connection, so
 *       writes there are out of scope for this PR.
 *   <li>Only the small safe subset is implemented here (set / walls / line /
 *       place_sign / say / move_to, by direct block placement). The fancy
 *       WorldEdit tools (cyl, sphere, generate, …) stay in vibe-build's
 *       executor, which drives them through a player; see docs/WORLD-SENSE.md.
 * </ul>
 */
public final class WorldTools {

    /** Writes are off unless the owner turns them on explicitly. */
    public static final boolean ENABLED = Boolean.getBoolean("omo.world.tools");

    /** Same caps as bridge/world_sense.py — enforced again here. */
    private static final int MAX_BLOCKS = 32768;
    private static final int MAX_LINE_BLOCKS = 8192;

    private WorldTools() {
    }

    /** Listen for {@code world_tool} frames from the bridge (client init). */
    public static void register() {
        BridgeClient.get().addListener(WorldTools::onBridge);
        OmoLife.LOG.info("World Sense tools {}", ENABLED ? "ENABLED (-Domo.world.tools=true)" : "off (start with -Domo.world.tools=true to let brains build)");
    }

    private static void onBridge(JsonObject msg) {
        if (!"world_tool".equals(msg.has("t") ? msg.get("t").getAsString() : "")) {
            return;
        }
        if (!ENABLED) {
            OmoLife.LOG.info("world_tool {} ignored: writes are off (-Domo.world.tools=true)", nameOf(msg));
            return;
        }
        MinecraftServer server = server();
        if (server == null) {
            return;
        }
        // Copy the frame: the websocket thread must not share it with the server thread.
        JsonObject call = msg.deepCopy();
        server.execute(() -> execute(server, call));
    }

    private static MinecraftServer server() {
        var mc = net.minecraft.client.Minecraft.getInstance();
        return mc == null ? null : mc.getSingleplayerServer();
    }

    private static String nameOf(JsonObject msg) {
        return msg.has("name") ? msg.get("name").getAsString() : "?";
    }

    private static void execute(MinecraftServer server, JsonObject call) {
        String name = nameOf(call);
        JsonObject args = call.has("args") && call.get("args").isJsonObject()
            ? call.getAsJsonObject("args") : new JsonObject();
        try {
            String result = switch (name) {
                case "world.set" -> set(server, args, false);
                case "world.walls" -> set(server, args, true);
                case "world.line" -> line(server, args);
                case "world.place_sign" -> placeSign(server, args);
                case "world.say" -> say(server, args);
                case "world.move_to" -> moveTo(server, args);
                default -> throw new IllegalArgumentException("unknown tool " + name);
            };
            OmoLife.LOG.info("world_tool {} -> {}", name, result);
            JsonObject ack = new JsonObject();
            ack.addProperty("t", "world_tool_result");
            ack.addProperty("name", name);
            ack.addProperty("ok", true);
            ack.addProperty("result", result);
            BridgeClient.get().send(ack);
        } catch (RuntimeException e) {
            OmoLife.LOG.warn("world_tool {} failed: {}", name, e.getMessage());
            JsonObject ack = new JsonObject();
            ack.addProperty("t", "world_tool_result");
            ack.addProperty("name", name);
            ack.addProperty("ok", false);
            ack.addProperty("error", e.getMessage());
            BridgeClient.get().send(ack);
        }
    }

    private static ServerLevel level(MinecraftServer server) {
        return server.overworld();
    }

    private static BlockPos pos(JsonArray a) {
        if (a == null || a.size() != 3) {
            throw new IllegalArgumentException("pos must be [x,y,z]");
        }
        return new BlockPos(a.get(0).getAsInt(), a.get(1).getAsInt(), a.get(2).getAsInt());
    }

    private static BlockState state(String pattern) {
        ResourceLocation id = ResourceLocation.tryParse(pattern);
        if (id == null) {
            throw new IllegalArgumentException("bad block id " + pattern);
        }
        Block block = BuiltInRegistries.BLOCK.get(id);
        if (block == null || (block == Blocks.AIR && !"minecraft:air".equals(pattern))) {
            throw new IllegalArgumentException("unknown block " + pattern);
        }
        return block.defaultBlockState();
    }

    private static String set(MinecraftServer server, JsonObject args, boolean wallsOnly) {
        BlockPos p1 = pos(args.getAsJsonArray("pos1"));
        BlockPos p2 = pos(args.getAsJsonArray("pos2"));
        BlockState s = state(args.get("pattern").getAsString());
        int x0 = Math.min(p1.getX(), p2.getX());
        int x1 = Math.max(p1.getX(), p2.getX());
        int y0 = Math.min(p1.getY(), p2.getY());
        int y1 = Math.max(p1.getY(), p2.getY());
        int z0 = Math.min(p1.getZ(), p2.getZ());
        int z1 = Math.max(p1.getZ(), p2.getZ());
        long n = (long) (x1 - x0 + 1) * (y1 - y0 + 1) * (z1 - z0 + 1);
        if (n > MAX_BLOCKS) {
            throw new IllegalArgumentException("region is " + n + " blocks, limit is " + MAX_BLOCKS);
        }
        ServerLevel level = level(server);
        int placed = 0;
        for (int x = x0; x <= x1; x++) {
            for (int y = y0; y <= y1; y++) {
                for (int z = z0; z <= z1; z++) {
                    if (wallsOnly && x != x0 && x != x1 && z != z0 && z != z1) {
                        continue;
                    }
                    if (level.setBlock(new BlockPos(x, y, z), s, Block.UPDATE_CLIENTS)) {
                        placed++;
                    }
                }
            }
        }
        return "placed " + placed + " x " + s.getBlock().getDescriptionId();
    }

    private static String line(MinecraftServer server, JsonObject args) {
        BlockPos p1 = pos(args.getAsJsonArray("pos1"));
        BlockPos p2 = pos(args.getAsJsonArray("pos2"));
        BlockState s = state(args.get("pattern").getAsString());
        int steps = Math.max(Math.abs(p2.getX() - p1.getX()),
            Math.max(Math.abs(p2.getY() - p1.getY()), Math.abs(p2.getZ() - p1.getZ())));
        if (steps + 1 > MAX_LINE_BLOCKS) {
            throw new IllegalArgumentException("line is too long");
        }
        ServerLevel level = level(server);
        int placed = 0;
        for (int i = 0; i <= steps; i++) {
            double t = steps == 0 ? 0 : (double) i / steps;
            BlockPos p = new BlockPos(
                (int) Math.round(p1.getX() + (p2.getX() - p1.getX()) * t),
                (int) Math.round(p1.getY() + (p2.getY() - p1.getY()) * t),
                (int) Math.round(p1.getZ() + (p2.getZ() - p1.getZ()) * t));
            if (level.setBlock(p, s, Block.UPDATE_CLIENTS)) {
                placed++;
            }
        }
        return "placed " + placed + " blocks along the line";
    }

    private static String placeSign(MinecraftServer server, JsonObject args) {
        BlockPos p = pos(args.getAsJsonArray("pos"));
        JsonArray lines = args.getAsJsonArray("lines");
        ServerLevel level = level(server);
        if (!level.setBlock(p, Blocks.OAK_SIGN.defaultBlockState(), Block.UPDATE_CLIENTS)) {
            throw new IllegalStateException("could not place a sign at " + p.toShortString());
        }
        // Sign text API: SignBlockEntity#setTextOnRow (1.21). If a future
        // version moves it, Fabric Loom CI will fail here first — that is
        // the intended tripwire, not a silent skip.
        if (level.getBlockEntity(p) instanceof SignBlockEntity sign && lines != null) {
            for (int i = 0; i < Math.min(4, lines.size()); i++) {
                sign.setTextOnRow(i, Component.literal(lines.get(i).getAsString()));
            }
            sign.setChanged();
            level.sendBlockUpdated(p, level.getBlockState(p), level.getBlockState(p), Block.UPDATE_CLIENTS);
        }
        return "sign placed at " + p.toShortString();
    }

    private static String say(MinecraftServer server, JsonObject args) {
        String text = args.has("text") ? args.get("text").getAsString() : "";
        if (text.length() > 256) {
            throw new IllegalArgumentException("text over 256 chars");
        }
        server.getPlayerList().broadcastSystemMessage(Component.literal("[agent] " + text), false);
        return "said: " + text;
    }

    private static String moveTo(MinecraftServer server, JsonObject args) {
        // Move the agent player (the Mineflayer body), never the owner.
        String agent = null;
        for (ServerPlayer p : server.getPlayerList().getPlayers()) {
            if (AgentLan.isAgent(p.getScoreboardName())) {
                agent = p.getScoreboardName();
                ServerLevel level = server.overworld();
                Vec3 from = p.position();
                double dx = args.get("x").getAsDouble() - from.x;
                double dy = args.get("y").getAsDouble() - from.y;
                double dz = args.get("z").getAsDouble() - from.z;
                if (Math.sqrt(dx * dx + dy * dy + dz * dz) > 256) {
                    throw new IllegalArgumentException("move_to is limited to 256 blocks");
                }
                p.teleportTo(level, args.get("x").getAsDouble(), args.get("y").getAsDouble(),
                    args.get("z").getAsDouble(), Set.of(), p.getYRot(), p.getXRot());
                return "moved agent " + agent + " to " + args.get("x") + "," + args.get("y") + "," + args.get("z");
            }
        }
        throw new IllegalStateException("no agent player is in the world (start one over the LAN port first)");
    }
}

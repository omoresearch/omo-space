package space.omo.life.world;

import java.util.ArrayList;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;

import com.google.gson.JsonArray;
import com.google.gson.JsonObject;

import net.minecraft.core.BlockPos;
import net.minecraft.core.Registry;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.core.registries.Registries;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.Entity;
import net.minecraft.world.level.ChunkPos;
import net.minecraft.world.level.chunk.LevelChunk;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.AABB;

/**
 * World Sense, game side: a deterministic summary of the chunks around a
 * point, computed by the game itself — never by asking an LLM to read raw
 * blocks.
 *
 * <p>The output is a <em>raw snapshot</em> (the shape
 * {@code bridge/world_sense.py} expects). The bridge turns it into the
 * budgeted LLM-facing summary; the raw snapshot is never stored on disk
 * and never sent to any model directly.
 *
 * <p>What is in it: per-chunk biome mix, height range, top-block and full
 * block histograms, structures, nearby entities by type, players, and
 * named places (stations). What is <em>not</em> in it: per-block data, the
 * world seed, player UUIDs, or anything about workspaces and terminals.
 *
 * <p>Cost: one chunk is 16x16 columns over the full build height. Radius 0
 * (one chunk) is the default; radius 1 (nine chunks) is the max and takes
 * on the order of a second on the server thread — it is a command, not a
 * tick, so that is acceptable, and the caller asked for it explicitly.
 */
public final class WorldSense {

    /** Max radius in chunks (1 = 3x3 chunks). */
    public static final int MAX_RADIUS = 1;

    private WorldSense() {
    }

    /** Snapshot of the chunks around {@code center} in {@code level}. */
    public static JsonObject snapshot(ServerLevel level, BlockPos center, int radius) {
        radius = Math.max(0, Math.min(MAX_RADIUS, radius));
        JsonObject snap = new JsonObject();
        snap.addProperty("world", level.getLevelData().getLevelName());
        snap.addProperty("dimension", level.dimension().location().toString());
        JsonObject c = new JsonObject();
        c.addProperty("x", center.getX());
        c.addProperty("y", center.getY());
        c.addProperty("z", center.getZ());
        snap.add("center", c);
        snap.addProperty("radius_chunks", radius);
        snap.addProperty("time", level.getDayTime());
        snap.addProperty("raining", level.isRaining());
        snap.addProperty("thundering", level.isThundering());

        ChunkPos cp = new ChunkPos(center);
        JsonArray chunks = new JsonArray();
        for (int dx = -radius; dx <= radius; dx++) {
            for (int dz = -radius; dz <= radius; dz++) {
                LevelChunk chunk = level.getChunkSource().getChunkNow(cp.x + dx, cp.z + dz);
                if (chunk != null) {
                    chunks.add(summariseChunk(level, chunk));
                }
            }
        }
        snap.add("chunks", chunks);

        // Entities in the described area, counted by type.
        int half = (radius * 2 + 1) * 8;
        AABB box = new AABB(center).inflate(half, 64, half);
        Map<String, List<Entity>> byType = new TreeMap<>();
        for (Entity e : level.getEntitiesOfClass(Entity.class, box)) {
            String type = BuiltInRegistries.ENTITY_TYPE.getKey(e.getType()).toString();
            byType.computeIfAbsent(type, k -> new ArrayList<>()).add(e);
        }
        JsonArray entities = new JsonArray();
        for (Map.Entry<String, List<Entity>> e : byType.entrySet()) {
            double nearest = e.getValue().stream()
                .mapToDouble(en -> en.position().distanceTo(center.getCenter())).min().orElse(-1);
            JsonObject o = new JsonObject();
            o.addProperty("type", e.getKey());
            o.addProperty("count", e.getValue().size());
            o.addProperty("nearest", Math.round(nearest * 10.0) / 10.0);
            entities.add(o);
        }
        snap.add("entities", entities);

        JsonArray players = new JsonArray();
        for (ServerPlayer p : level.getServer().getPlayerList().getPlayers()) {
            if (p.level() != level) {
                continue;
            }
            JsonObject o = new JsonObject();
            o.addProperty("name", p.getScoreboardName());
            o.addProperty("x", p.getBlockX());
            o.addProperty("y", p.getBlockY());
            o.addProperty("z", p.getBlockZ());
            o.addProperty("dist", Math.round(p.position().distanceTo(center.getCenter()) * 10.0) / 10.0);
            players.add(o);
        }
        snap.add("players", players);
        snap.add("named", new JsonArray());
        return snap;
    }

    private static JsonObject summariseChunk(ServerLevel level, LevelChunk chunk) {
        ChunkPos cp = chunk.getPos();
        JsonObject out = new JsonObject();
        out.addProperty("cx", cp.x);
        out.addProperty("cz", cp.z);

        Map<String, Integer> biomes = new TreeMap<>();
        Map<String, Integer> topBlocks = new TreeMap<>();
        Map<String, Integer> blocks = new TreeMap<>();
        int minY = level.getMinBuildHeight();
        int maxY = level.getMaxBuildHeight();
        int hMin = Integer.MAX_VALUE;
        int hMax = Integer.MIN_VALUE;
        long hSum = 0;
        int columns = 0;

        for (int x = 0; x < 16; x++) {
            for (int z = 0; z < 16; z++) {
                int wx = cp.getMinBlockX() + x;
                int wz = cp.getMinBlockZ() + z;
                int h = level.getHeight(Heightmap.Types.WORLD_SURFACE, wx, wz);
                hMin = Math.min(hMin, h);
                hMax = Math.max(hMax, h);
                hSum += h;
                columns++;
                BlockPos top = new BlockPos(wx, h - 1, wz);
                String topId = id(level.getBlockState(top).getBlock());
                topBlocks.merge(topId, 1, Integer::sum);
                String biome = level.getBiome(top).unwrapKey()
                    .map(k -> k.location().toString()).orElse("unknown");
                biomes.merge(biome, 1, Integer::sum);
                for (int y = minY; y < maxY; y++) {
                    String id = id(level.getBlockState(new BlockPos(wx, y, wz)).getBlock());
                    if (!"minecraft:air".equals(id)) {
                        blocks.merge(id, 1, Integer::sum);
                    }
                }
            }
        }
        JsonObject height = new JsonObject();
        height.addProperty("min", columns == 0 ? minY : hMin);
        height.addProperty("max", columns == 0 ? minY : hMax);
        height.addProperty("mean", columns == 0 ? 0 : Math.round(hSum * 10.0 / columns) / 10.0);
        out.add("height", height);
        out.add("biome", counts(biomes));
        out.add("top_blocks", counts(topBlocks));
        out.add("blocks", counts(blocks));

        JsonArray structures = new JsonArray();
        try {
            for (var e : chunk.getAllStarts().entrySet()) {
                Registry<?> reg = level.registryAccess().lookupOrThrow(Registries.STRUCTURE);
                var key = reg.getKey(e.getKey());
                if (key != null) {
                    structures.add(key.location().toString());
                }
            }
        } catch (RuntimeException ignored) {
            // Structures are a bonus, never a reason to fail a snapshot.
        }
        out.add("structures", structures);
        return out;
    }

    private static JsonObject counts(Map<String, Integer> m) {
        JsonObject o = new JsonObject();
        for (Map.Entry<String, Integer> e : m.entrySet()) {
            o.addProperty(e.getKey(), e.getValue());
        }
        return o;
    }

    private static String id(net.minecraft.world.level.block.Block block) {
        return BuiltInRegistries.BLOCK.getKey(block).toString();
    }
}

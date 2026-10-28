package space.omo.life.client;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.HashMap;
import java.util.Map;

import com.google.gson.JsonElement;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import net.fabricmc.loader.api.FabricLoader;
import net.minecraft.client.Minecraft;
import net.minecraft.core.BlockPos;

import space.omo.life.OmoLife;

/**
 * Per-wall web audio settings (volume, mute), kept on this computer only in
 * {@code config/omolife-web-audio.json}, keyed by world and the wall's origin
 * panel. Client-side by design: one player muting a wall mutes it for them.
 */
final class AudioPrefs {

    private static final Path FILE = FabricLoader.getInstance().getConfigDir().resolve("omolife-web-audio.json");
    private static Map<String, int[]> prefs;

    private AudioPrefs() {
    }

    /** {volume 0..100, muted 0/1} for this wall, or null when never changed. */
    static int[] get(BlockPos origin) {
        return load().get(key(origin));
    }

    static void put(BlockPos origin, int volume, boolean muted) {
        Map<String, int[]> all = load();
        if (volume == 100 && !muted) {
            all.remove(key(origin));            // the default needs no entry
        } else {
            all.put(key(origin), new int[]{volume, muted ? 1 : 0});
        }
        save(all);
    }

    private static String key(BlockPos origin) {
        Minecraft mc = Minecraft.getInstance();
        String world;
        if (mc.getSingleplayerServer() != null) {
            world = "sp:" + mc.getSingleplayerServer().getWorldData().getLevelName();
        } else if (mc.getCurrentServer() != null) {
            world = "mp:" + mc.getCurrentServer().ip;
        } else {
            world = "?";
        }
        String dim = mc.level == null ? "" : mc.level.dimension().identifier().toString();
        return world + "|" + dim + "|" + origin.getX() + "," + origin.getY() + "," + origin.getZ();
    }

    private static Map<String, int[]> load() {
        if (prefs != null) {
            return prefs;
        }
        prefs = new HashMap<>();
        try {
            if (Files.isRegularFile(FILE)) {
                JsonObject o = JsonParser.parseString(Files.readString(FILE)).getAsJsonObject();
                for (Map.Entry<String, JsonElement> e : o.entrySet()) {
                    var a = e.getValue().getAsJsonArray();
                    prefs.put(e.getKey(), new int[]{a.get(0).getAsInt(), a.get(1).getAsInt()});
                }
            }
        } catch (IOException | RuntimeException e) {
            OmoLife.LOG.warn("web audio settings unreadable, starting fresh: {}", e.toString());
        }
        return prefs;
    }

    private static void save(Map<String, int[]> all) {
        JsonObject o = new JsonObject();
        all.forEach((k, v) -> {
            var a = new com.google.gson.JsonArray();
            a.add(v[0]);
            a.add(v[1]);
            o.add(k, a);
        });
        try {
            Path tmp = FILE.resolveSibling(FILE.getFileName() + ".tmp");
            Files.writeString(tmp, o.toString(), StandardCharsets.UTF_8);
            Files.move(tmp, FILE, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException e) {
            OmoLife.LOG.warn("could not save web audio settings: {}", e.toString());
        }
    }
}

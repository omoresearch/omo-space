package space.omo.life;

import java.io.IOException;
import java.io.Reader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.Properties;

/**
 * {@code config/omolife.properties}: the few settings that must reach the mod
 * however the game was started (CurseForge, Modrinth App, Prism), where JVM
 * flags are awkward to set. A JVM flag always wins over the file.
 *
 * <pre>
 *   bridgePort=8780          -Domo.port      the bridge's loopback port
 *   bridgeData=/path         -Domo.data      the folder holding the bridge token
 *   showAllBlocks=false      -Domo.showAllBlocks  every Omo and CC: Tweaked block in creative + recipes
 * </pre>
 *
 * {@code omo_setup.py link --game-dir DIR} writes bridgePort (and bridgeData
 * when it is not the default) into a store instance. The token itself never
 * goes into a game folder.
 *
 * <p>No Minecraft or Fabric classes here, so it is testable on its own.
 */
public final class OmoConfig {

    public static final String FILE = "omolife.properties";

    private static volatile Properties loaded;

    private OmoConfig() {
    }

    /** The properties in {@code file}; empty when missing or unreadable. */
    public static Properties read(Path file) {
        Properties p = new Properties();
        if (file != null && Files.isRegularFile(file)) {
            try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
                p.load(r);
            } catch (IOException | IllegalArgumentException e) {
                return new Properties();
            }
        }
        return p;
    }

    /** Load {@code <configDir>/omolife.properties} once for {@link #get()}. */
    public static void init(Path configDir) {
        loaded = read(configDir == null ? null : configDir.resolve(FILE));
    }

    /** The loaded file, or an empty set before {@link #init}. */
    public static Properties get() {
        Properties p = loaded;
        return p == null ? new Properties() : p;
    }

    /** {@code prop} if set, else the file's {@code key}, else null. Blank counts as unset. */
    public static String value(String prop, Properties file, String key) {
        if (prop != null && !prop.isBlank()) {
            return prop.trim();
        }
        String v = file == null ? null : file.getProperty(key);
        return v == null || v.isBlank() ? null : v.trim();
    }

    /** A port 1..65535 from the flag or the file, else {@code fallback}. */
    public static int port(String prop, Properties file, int fallback) {
        String v = value(prop, file, "bridgePort");
        if (v == null) {
            return fallback;
        }
        try {
            int p = Integer.parseInt(v);
            return p > 0 && p < 65536 ? p : fallback;
        } catch (NumberFormatException e) {
            return fallback;
        }
    }

    /** True only for an explicit {@code true}. */
    public static boolean flag(String prop, Properties file, String key) {
        String v = value(prop, file, key);
        return v != null && "true".equalsIgnoreCase(v);
    }

    /** Every Omo and CC: Tweaked block in the creative menu, with recipes (advanced players). */
    public static boolean showAllBlocks() {
        return flag(System.getProperty("omo.showAllBlocks"), get(), "showAllBlocks");
    }
}

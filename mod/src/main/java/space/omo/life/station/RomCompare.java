package space.omo.life.station;

import java.util.ArrayList;
import java.util.Arrays;
import java.util.List;
import java.util.Map;
import java.util.TreeMap;
import java.util.function.Function;

/**
 * The pure half of {@link RomCheck}: is the CraftOS a station boots exactly
 * what the installed mods ship?
 *
 * <p>CC: Tweaked builds every computer's {@code bios.lua} and {@code /rom}
 * from the server's data packs, and a world's own {@code datapacks/} folder
 * outranks mod data. A shared map could therefore replace the station
 * program, the Omo autorun, {@code startup.lua} or the BIOS itself, or add an
 * autorun that runs first. Each of those would run world code on a station
 * computer, where the {@code omo} API mints tickets for a host shell.
 *
 * <p>The rule: every file under {@code computercraft:lua/bios.lua} and
 * {@code computercraft:lua/rom/**} must have exactly the bytes some installed
 * mod jar ships at that path (mods are code the player installed; world data
 * is not), and the files a station needs must exist (a world can also switch
 * a mod's data pack off). No Minecraft classes, so tests run it directly.
 */
public final class RomCompare {

    /** Files a station cannot boot safely without. */
    public static final List<String> REQUIRED = List.of(
        "lua/bios.lua",
        "lua/rom/startup.lua",
        "lua/rom/autorun/00-omo-station.lua",
        "lua/rom/programs/omo/station.lua");

    private RomCompare() {
    }

    /** Whether a {@code computercraft} resource path is part of what a computer boots. */
    public static boolean governed(String path) {
        return path.equals("lua/bios.lua") || path.startsWith("lua/rom/");
    }

    /**
     * The governed paths whose effective bytes no installed mod ships, plus
     * any required file that is missing. Empty means the ROM is the mods' own.
     *
     * @param effective what the server's resource manager serves, by path under {@code computercraft:}
     * @param shipped   the bytes each installed mod jar has at {@code data/computercraft/<path>}
     */
    public static List<String> problems(Map<String, byte[]> effective, Function<String, List<byte[]>> shipped) {
        List<String> out = new ArrayList<>();
        for (String req : REQUIRED) {
            if (!effective.containsKey(req)) {
                out.add(req + " (missing)");
            }
        }
        for (Map.Entry<String, byte[]> e : new TreeMap<>(effective).entrySet()) {
            if (!governed(e.getKey())) {
                continue;
            }
            List<byte[]> candidates = shipped.apply(e.getKey());
            boolean match = false;
            if (candidates != null) {
                for (byte[] c : candidates) {
                    if (c != null && Arrays.equals(c, e.getValue())) {
                        match = true;
                        break;
                    }
                }
            }
            if (!match) {
                out.add(e.getKey());
            }
        }
        return out;
    }
}

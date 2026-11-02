package space.omo.life.station;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardOpenOption;
import java.util.LinkedHashSet;
import java.util.Set;
import java.util.regex.Pattern;

/**
 * Which stations may get a ticket, and the host-side memory of stations the
 * owner has really used (no Minecraft classes, so tests run it directly).
 *
 * <p>A ticket lets a station look at and type into its workspace, which is a
 * host shell. World data (a shared map, a pocket in a chest) must never be
 * enough to get one, so {@link #refusal} only lets a ticket through when:
 * <ul>
 *   <li>the computer ROM is the installed mods' own ({@link RomCheck}), and</li>
 *   <li>the owner (or an operator) interacted with that station live, in this
 *       session, within {@link #WINDOW_MS}, or</li>
 *   <li>this host remembers that the owner used this exact station with this
 *       exact workspace before, and an owner/operator is online now.</li>
 * </ul>
 * The memory lives in the game's config folder, never in the world, so a
 * downloaded map cannot bring its own. Station ids are random UUIDs minted
 * here and workspace ids are random bridge ids; neither is shown by the
 * bridge's unauthenticated routes.
 */
public final class StationTrust {

    /** How long a live owner interaction lets a station mint tickets. */
    public static final long WINDOW_MS = 120_000;
    /** Never let the file grow without bound. */
    static final int MAX_ENTRIES = 4096;

    public static final String WAITING = "waiting for owner";

    private static final Pattern ID = Pattern.compile("[A-Za-z0-9-]{1,64}");

    private final Path file;
    private Set<String> pairs;

    public StationTrust(Path file) {
        this.file = file;
    }

    /**
     * Why a ticket is refused, or null when it may be minted.
     *
     * @param romProblem   null when the ROM is the mods' own, else why not
     * @param recentTouch  an owner/operator used this station within the window and is still online
     * @param remembered   this host remembers the station+workspace pair
     * @param ownerOnline  an owner/operator is in the game now
     */
    public static String refusal(String romProblem, boolean recentTouch, boolean remembered, boolean ownerOnline) {
        if (romProblem != null) {
            return romProblem;
        }
        if (recentTouch) {
            return null;
        }
        return remembered && ownerOnline ? null : WAITING;
    }

    /** True while {@code at} is within the window after {@code touched}. */
    public static boolean recent(long touched, long at) {
        return touched > 0 && at >= touched && at - touched < WINDOW_MS;
    }

    static boolean validId(String s) {
        return s != null && ID.matcher(s).matches();
    }

    private static String key(String station, String ws) {
        return station + " " + ws;
    }

    public synchronized boolean contains(String station, String ws) {
        return validId(station) && validId(ws) && load().contains(key(station, ws));
    }

    /** Remember a pair the owner used live. Ignores malformed ids. */
    public synchronized void add(String station, String ws) {
        if (!validId(station) || !validId(ws)) {
            return;
        }
        Set<String> set = load();
        if (!set.add(key(station, ws))) {
            return;
        }
        while (set.size() > MAX_ENTRIES) {
            set.remove(set.iterator().next());
        }
        try {
            Files.createDirectories(file.getParent());
            Files.write(file, String.join("\n", set).concat("\n").getBytes(StandardCharsets.UTF_8),
                StandardOpenOption.CREATE, StandardOpenOption.TRUNCATE_EXISTING, StandardOpenOption.WRITE);
        } catch (IOException e) {
            System.err.println("[omolife] could not save " + file + ": " + e);
        }
    }

    private Set<String> load() {
        if (pairs != null) {
            return pairs;
        }
        pairs = new LinkedHashSet<>();
        try {
            if (Files.isRegularFile(file)) {
                for (String line : Files.readAllLines(file, StandardCharsets.UTF_8)) {
                    String[] p = line.trim().split(" ");
                    if (p.length == 2 && validId(p[0]) && validId(p[1])) {
                        pairs.add(key(p[0], p[1]));
                    }
                }
            }
        } catch (IOException e) {
            System.err.println("[omolife] could not read " + file + ": " + e);
        }
        return pairs;
    }
}

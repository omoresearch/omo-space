package space.omo.life.client;

import java.util.ArrayDeque;
import java.util.HashMap;
import java.util.Map;
import java.util.function.LongSupplier;

/**
 * The rules that keep Chromium bounded, whatever asks for browsers.
 *
 * <p>Every web wall and tab is a Chromium browser, and every browser costs
 * jcef_helper processes and memory. On 27 Oct 2026 a page cap that evicted
 * pages still loading made 14 walls evict each other every frame: ~2450
 * browsers in 90 s, ~2000 leaked helpers, 50 GB, and the kernel's OOM killer
 * took the desktop session down mid-stream. These rules make that
 * impossible, each on its own:
 * <ol>
 *   <li><b>Eviction</b> ({@link #evictable}): only a page no wall has asked for
 *       in {@link #EVICT_UNSEEN_MS}, and that is at least that old, may be
 *       closed to make room. A page still loading is in use.</li>
 *   <li><b>Creation budget</b>: {@link #BURST} at once, then one per
 *       {@link #REFILL_MS}, never more than {@link #PER_MINUTE} a minute.</li>
 *   <li><b>Churn breaker</b>: a wall that gets a fresh browser
 *       {@link #CHURN_OPENS} times within {@link #CHURN_WINDOW_MS} is parked
 *       for {@link #CHURN_COOLDOWN_MS}.</li>
 *   <li><b>Live ceiling</b>: at most {@link #MAX_BROWSERS} browsers alive or
 *       still closing.</li>
 *   <li><b>Memory guard</b> ({@link #assess}): measured from outside by
 *       {@link HelperWatchdog}; SOFT pauses new browsers, HARD closes them all.</li>
 * </ol>
 * Pure Java with an injected clock (no Minecraft, no MCEF), so every rule is
 * unit-tested on its own by tests/test_browser_guard.py. Client thread only.
 */
final class BrowserGuard {

    static final long EVICT_UNSEEN_MS = 5000;

    static final int BURST = 4;
    static final long REFILL_MS = 750;
    static final int PER_MINUTE = 30;
    private static final long MINUTE_MS = 60_000;

    static final int CHURN_OPENS = 4;
    static final long CHURN_WINDOW_MS = 60_000;
    static final long CHURN_COOLDOWN_MS = 60_000;

    static final int MAX_BROWSERS = 32;

    /** jcef_helper processes: a dozen walls use ~2-3 each plus a few shared ones. */
    static final int HELPERS_SOFT = 80;
    static final int HELPERS_HARD = 150;
    /** Private (anonymous) memory of all helpers together. */
    static final long ANON_SOFT = 8L << 30;
    static final long ANON_HARD = 12L << 30;
    /** The machine's MemAvailable: below this the browsers are what gives way. */
    static final long AVAIL_SOFT = 6L << 30;
    static final long AVAIL_HARD = 3L << 30;
    /** How long a SOFT / HARD verdict keeps new browsers paused after it was last seen. */
    static final long SOFT_PAUSE_MS = 15_000;
    static final long HARD_PAUSE_MS = 120_000;
    /** With no browser alive or closing this long, leftover renderers are leaks. */
    static final long ORPHAN_REAP_MS = 15_000;
    /** New browsers wait this long while a reap runs, so it cannot hit a fresh renderer. */
    static final long REAP_HOLD_MS = 3000;

    /**
     * Short on machine memory (not Chromium over its own limits): the walls
     * nearest the player still keep, or get, a page, so the screens you are
     * using stay up while the rest of the room goes dark.
     */
    static final int NEAR_KEEP = 2;
    static final double NEAR_BLOCKS = 16;
    /** Below this MemAvailable not even the nearest walls keep a page. */
    static final long AVAIL_FLOOR = 3L << 29;
    /** Every memory pause's reason starts with this; other pauses never do. */
    static final String MEMORY_PAUSE = "Web screens paused";

    enum Level { OK, SOFT, HARD }

    /** One look at Chromium's processes and the machine; availBytes -1 = unknown. */
    record Sample(int helpers, int renderers, long anonBytes, long availBytes) {
        String describe() {
            return helpers + " helpers, " + (anonBytes >> 20) + " MB, "
                + (availBytes < 0 ? "?" : String.valueOf(availBytes >> 20)) + " MB free";
        }
    }

    private final LongSupplier clock;
    private double tokens = BURST;
    private long refilledAt;
    private final ArrayDeque<Long> lastMinute = new ArrayDeque<>();
    private final Map<Object, ArrayDeque<Long>> opensByWall = new HashMap<>();
    private final Map<Object, Long> parkedUntil = new HashMap<>();
    private long pausedUntil;
    private String pauseReason = "";

    BrowserGuard(LongSupplier clock) {
        this.clock = clock;
        this.refilledAt = clock.getAsLong();
    }

    /** May this page be closed to make room for another wall? */
    static boolean evictable(long now, long lastWanted, long createdAt, boolean interactive) {
        return !interactive && now - lastWanted >= EVICT_UNSEEN_MS && now - createdAt >= EVICT_UNSEEN_MS;
    }

    /**
     * Why a new browser may not be created now, or "" when it may.
     * {@code wall} is the wall it is for (null for a tab: no churn check);
     * {@code live} counts browsers alive plus those still closing. Spends
     * nothing: call {@link #created} once the browser exists.
     */
    String deny(Object wall, int live) {
        return deny(wall, live, false);
    }

    /** As {@link #deny(Object, int)}; {@code nearExempt} lets a near wall past a memory pause (never other pauses). */
    String deny(Object wall, int live, boolean nearExempt) {
        long now = clock.getAsLong();
        if (now < pausedUntil && !(nearExempt && pauseReason.startsWith(MEMORY_PAUSE))) {
            return pauseReason;
        }
        Long parked = wall == null ? null : parkedUntil.get(wall);
        if (parked != null && now < parked) {
            return "This screen kept reopening, so it is paused for a minute";
        }
        if (live >= MAX_BROWSERS) {
            return "Browser limit reached (" + MAX_BROWSERS + " pages and tabs)";
        }
        refill(now);
        dropOlderThan(lastMinute, now - MINUTE_MS + 1);
        return tokens < 1 || lastMinute.size() >= PER_MINUTE ? "Opening pages..." : "";
    }

    /**
     * A browser for {@code wall} (null: a tab) was just created. Returns true
     * when this one tripped the churn breaker and the wall is now parked.
     */
    boolean created(Object wall) {
        long now = clock.getAsLong();
        refill(now);
        tokens = Math.max(0, tokens - 1);
        lastMinute.addLast(now);
        if (wall == null) {
            return false;
        }
        ArrayDeque<Long> opens = opensByWall.computeIfAbsent(wall, k -> new ArrayDeque<>());
        opens.addLast(now);
        dropOlderThan(opens, now - CHURN_WINDOW_MS);
        if (opens.size() < CHURN_OPENS) {
            return false;
        }
        opens.clear();
        parkedUntil.put(wall, now + CHURN_COOLDOWN_MS);
        return true;
    }

    /**
     * No new browsers for {@code ms}, with {@code reason} shown on dark walls.
     * Extends, never shortens; the reason is that of the pause ending last.
     */
    void pause(long ms, String reason) {
        long until = clock.getAsLong() + ms;
        if (until >= pausedUntil) {
            pausedUntil = until;
            pauseReason = reason;
        }
    }

    boolean paused() {
        return clock.getAsLong() < pausedUntil;
    }

    /** What dark walls say while {@link #paused}. */
    String pauseReason() {
        return pauseReason;
    }

    /** Forget walls with nothing left in any window (call now and then). */
    void prune() {
        long now = clock.getAsLong();
        opensByWall.values().removeIf(q -> {
            dropOlderThan(q, now - CHURN_WINDOW_MS);
            return q.isEmpty();
        });
        parkedUntil.values().removeIf(until -> until <= now);
    }

    /** Chromium itself is over its limits, as opposed to the machine being short on memory. */
    static boolean chromiumOver(Sample s) {
        return s != null && (s.helpers() >= HELPERS_HARD || s.anonBytes() >= ANON_HARD);
    }

    /**
     * May a wall {@code blocks} away keep or get a page while memory is short?
     * {@code rank} is how many nearer walls already keep one.
     */
    static boolean nearExempt(Sample s, double blocks, int rank) {
        return !chromiumOver(s) && (s == null || s.availBytes() < 0 || s.availBytes() >= AVAIL_FLOOR)
            && blocks <= NEAR_BLOCKS && rank < NEAR_KEEP;
    }

    static Level assess(Sample s) {
        if (s == null) {
            return Level.OK;
        }
        boolean knownAvail = s.availBytes() >= 0;
        if (s.helpers() >= HELPERS_HARD || s.anonBytes() >= ANON_HARD || knownAvail && s.availBytes() < AVAIL_HARD) {
            return Level.HARD;
        }
        if (s.helpers() >= HELPERS_SOFT || s.anonBytes() >= ANON_SOFT || knownAvail && s.availBytes() < AVAIL_SOFT) {
            return Level.SOFT;
        }
        return Level.OK;
    }

    private void refill(long now) {
        if (now > refilledAt) {
            tokens = Math.min(BURST, tokens + (double) (now - refilledAt) / REFILL_MS);
            refilledAt = now;
        }
    }

    /** Drop timestamps before {@code cutoff}. */
    private static void dropOlderThan(ArrayDeque<Long> q, long cutoff) {
        while (!q.isEmpty() && q.peekFirst() < cutoff) {
            q.removeFirst();
        }
    }
}

package space.omo.life.client;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.List;
import java.util.concurrent.Executors;
import java.util.concurrent.ScheduledExecutorService;
import java.util.concurrent.TimeUnit;
import java.util.function.Consumer;
import java.util.function.IntConsumer;

/**
 * Watches Chromium's helper processes (jcef_helper, children of this JVM)
 * from outside the browser, so a leak is caught by what it costs, not by
 * trusting the code that leaked. Linux only (reads /proc); elsewhere
 * {@link #sample} returns null and nothing runs.
 *
 * <p>All /proc work (the scan every {@link #PERIOD_MS}, and reaping) runs on
 * one low-priority daemon thread, never the render thread: when this matters
 * there may be thousands of processes. Callbacks run on that thread and must
 * hop to the client thread themselves. Pure Java: no Minecraft types.
 */
final class HelperWatchdog {

    static final long PERIOD_MS = 3000;
    private static final String HELPER = "jcef_helper";
    private static final Path PROC = Path.of("/proc");

    private static ScheduledExecutorService timer;

    private HelperWatchdog() {
    }

    static synchronized void start(Consumer<BrowserGuard.Sample> onSample) {
        if (timer != null || !Files.isDirectory(PROC.resolve("self"))) {
            return;
        }
        timer = Executors.newSingleThreadScheduledExecutor(r -> {
            Thread t = new Thread(r, "omo-browser-watchdog");
            t.setDaemon(true);
            t.setPriority(Thread.MIN_PRIORITY);
            return t;
        });
        timer.scheduleWithFixedDelay(() -> {
            try {
                BrowserGuard.Sample s = sample();
                if (s != null) {
                    onSample.accept(s);
                }
            } catch (RuntimeException ignored) {
                // a process vanished mid-read; next round
            }
        }, PERIOD_MS, PERIOD_MS, TimeUnit.MILLISECONDS);
    }

    /**
     * On the watchdog thread: kill orphaned renderers (see
     * {@link #killOrphanRenderers}) and report how many. No-op before {@link #start}.
     */
    static synchronized void reapOrphans(IntConsumer killed) {
        if (timer != null) {
            timer.execute(() -> killed.accept(killOrphanRenderers()));
        }
    }

    /** Count and size this JVM's helper processes, plus the machine's free memory. */
    static BrowserGuard.Sample sample() {
        if (!Files.isDirectory(PROC.resolve("self"))) {
            return null;
        }
        int helpers = 0;
        int renderers = 0;
        long anon = 0;
        for (ProcessHandle p : helpers()) {
            helpers++;
            if (isRenderer(p.pid())) {
                renderers++;
            }
            anon += statusKb(p.pid(), "RssAnon:") * 1024;
        }
        return new BrowserGuard.Sample(helpers, renderers, anon, memAvailable());
    }

    /**
     * Kill helper renderers. Only for when no browser is alive or closing:
     * then every renderer left is an orphan nothing will ever reap, and
     * Chromium recreates what it needs for the next page. GPU and utility
     * helpers are left alone. Returns how many were killed.
     */
    static int killOrphanRenderers() {
        int n = 0;
        for (ProcessHandle p : helpers()) {
            if (isRenderer(p.pid()) && p.destroyForcibly()) {
                n++;
            }
        }
        return n;
    }

    private static List<ProcessHandle> helpers() {
        return ProcessHandle.current().descendants()
            .filter(p -> HELPER.equals(comm(p.pid())))
            .toList();
    }

    private static String comm(long pid) {
        try {
            return Files.readString(PROC.resolve(pid + "/comm"), StandardCharsets.UTF_8).trim();
        } catch (IOException | RuntimeException e) {
            return "";
        }
    }

    private static boolean isRenderer(long pid) {
        try {
            String cmd = new String(Files.readAllBytes(PROC.resolve(pid + "/cmdline")), StandardCharsets.UTF_8);
            return cmd.contains("--type=renderer");
        } catch (IOException | RuntimeException e) {
            return false;
        }
    }

    private static long statusKb(long pid, String key) {
        try {
            for (String line : Files.readAllLines(PROC.resolve(pid + "/status"), StandardCharsets.UTF_8)) {
                if (line.startsWith(key)) {
                    return Long.parseLong(line.substring(key.length()).trim().split("\\s+")[0]);
                }
            }
        } catch (IOException | RuntimeException ignored) {
            // gone
        }
        return 0;
    }

    private static long memAvailable() {
        try {
            for (String line : Files.readAllLines(PROC.resolve("meminfo"), StandardCharsets.UTF_8)) {
                if (line.startsWith("MemAvailable:")) {
                    return Long.parseLong(line.substring(13).trim().split("\\s+")[0]) * 1024;
                }
            }
        } catch (IOException | RuntimeException ignored) {
            // unknown
        }
        return -1;
    }
}

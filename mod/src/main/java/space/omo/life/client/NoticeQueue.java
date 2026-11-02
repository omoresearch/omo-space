package space.omo.life.client;

import java.io.IOException;
import java.io.Reader;
import java.io.Writer;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.ArrayList;
import java.util.List;
import java.util.Properties;

/**
 * The HUD's Hermes notices: at most {@link #MAX} at once, each shown for
 * {@link #SHOW_MS} with a short fade in and out. A new notice when the stack
 * is full pushes the oldest out.
 *
 * <p>No Minecraft types, so tests/test_notice_queue.py compiles and runs it on
 * its own. Time is passed in, never read here. Not thread-safe: the client
 * thread adds and draws.
 */
public final class NoticeQueue {

    public static final int MAX = 3;
    public static final long SHOW_MS = 5000L;
    public static final long FADE_IN_MS = 180L;
    public static final long FADE_OUT_MS = 450L;
    public static final int TEXT_MAX = 160;
    public static final int PROFILE_MAX = 32;

    /** The client config key that turns the HUD notices on and off. */
    public static final String CONFIG_KEY = "hermesNotices";

    public enum Level {
        INFO, SUCCESS, WARN;

        /** The bridge's level id; anything unknown is info. */
        public static Level of(String id) {
            if (id != null) {
                for (Level l : values()) {
                    if (l.name().equalsIgnoreCase(id.trim())) {
                        return l;
                    }
                }
            }
            return INFO;
        }
    }

    public record Notice(String profile, String text, Level level, long born) {
    }

    private final List<Notice> notices = new ArrayList<>();

    /**
     * Queue a notice. Text and profile are cleaned for drawing (formatting
     * codes and control characters out, whitespace collapsed, clamped); an
     * empty text is dropped. Returns whether it was queued.
     */
    public boolean add(String profile, String text, String level, long now) {
        String t = clamp(clean(text), TEXT_MAX);
        if (t.isEmpty()) {
            return false;
        }
        String p = clamp(clean(profile), PROFILE_MAX);
        notices.add(new Notice(p.isEmpty() ? "hermes" : p, t, Level.of(level), now));
        while (notices.size() > MAX) {
            notices.remove(0);
        }
        return true;
    }

    /** The notices still showing at {@code now}, oldest first; expired ones are forgotten. */
    public List<Notice> visible(long now) {
        notices.removeIf(n -> now - n.born() >= SHOW_MS || now < n.born());
        return List.copyOf(notices);
    }

    public boolean isEmpty() {
        return notices.isEmpty();
    }

    public void clear() {
        notices.clear();
    }

    /** Opacity 0..1 of a notice at {@code now}: eased in, held, eased out. */
    public static float opacity(Notice n, long now) {
        long age = now - n.born();
        if (age < 0 || age >= SHOW_MS) {
            return 0f;
        }
        if (age < FADE_IN_MS) {
            return ease(age / (float) FADE_IN_MS);
        }
        long left = SHOW_MS - age;
        if (left < FADE_OUT_MS) {
            return ease(left / (float) FADE_OUT_MS);
        }
        return 1f;
    }

    /** Fraction of the showing time left, 1 at birth, 0 at expiry (the card's timer line). */
    public static float remaining(Notice n, long now) {
        long age = now - n.born();
        return Math.max(0f, Math.min(1f, 1f - age / (float) SHOW_MS));
    }

    /**
     * Text safe to draw: Minecraft's section-sign formatting codes (the sign
     * and the code after it), control and bidi-override characters removed,
     * whitespace runs collapsed to one space.
     */
    public static String clean(String s) {
        if (s == null) {
            return "";
        }
        StringBuilder sb = new StringBuilder(s.length());
        boolean space = false;
        for (int i = 0; i < s.length(); i++) {
            char c = s.charAt(i);
            if (c == '\u00a7') {
                i++;                                   // the code letter goes with the sign
                continue;
            }
            if (Character.isWhitespace(c) || Character.isSpaceChar(c)) {
                space = sb.length() > 0;
                continue;
            }
            if (Character.isISOControl(c) || isBidiControl(c)) {
                continue;
            }
            if (space) {
                sb.append(' ');
                space = false;
            }
            sb.append(c);
        }
        return sb.toString();
    }

    private static boolean isBidiControl(char c) {
        return (c >= '\u202a' && c <= '\u202e') || (c >= '\u2066' && c <= '\u2069') || c == '\u200e' || c == '\u200f';
    }

    private static String clamp(String s, int max) {
        if (s.length() <= max) {
            return s;
        }
        int end = max - 1;
        if (Character.isHighSurrogate(s.charAt(end - 1))) {
            end--;                                     // never split a surrogate pair
        }
        return s.substring(0, end) + "\u2026";
    }

    private static float ease(float p) {
        p = Math.max(0f, Math.min(1f, p));
        return p * p * (3f - 2f * p);
    }

    // -- client config: config/omolife-client.properties -------------------------------

    /** Whether HUD notices are on; true when the file or key is missing or unreadable. */
    public static boolean readEnabled(Path file) {
        Properties p = new Properties();
        try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            p.load(r);
        } catch (IOException | RuntimeException e) {
            return true;
        }
        return !"false".equalsIgnoreCase(p.getProperty(CONFIG_KEY, "true").trim());
    }

    /** Set the key, keeping any other keys in the file. Written atomically; failures are ignored. */
    public static void writeEnabled(Path file, boolean on) {
        Properties p = new Properties();
        try (Reader r = Files.newBufferedReader(file, StandardCharsets.UTF_8)) {
            p.load(r);
        } catch (IOException | RuntimeException ignored) {
        }
        p.setProperty(CONFIG_KEY, Boolean.toString(on));
        Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
        try {
            try (Writer w = Files.newBufferedWriter(tmp, StandardCharsets.UTF_8)) {
                p.store(w, "Omo Space client settings");
            }
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException | RuntimeException e) {
            try {
                Files.deleteIfExists(tmp);
            } catch (IOException | RuntimeException ignored) {
            }
        }
    }
}

package space.omo.life.block;

import java.util.Locale;
import java.util.regex.Pattern;

/**
 * What a monitor wall may load: http(s) pages and {@code about:blank}, nothing
 * else. No Minecraft classes, so tests run it directly.
 *
 * <p>A wall's URL can come from a packet (owner-gated), an item, block NBT
 * written by a map or {@code /data}, or a third-party server's block update.
 * All of those go through {@link #safe} on the server and {@link #loadable}
 * again on the client right before the browser sees it, so a
 * {@code file:}, {@code chrome:}, {@code javascript:}, {@code data:},
 * {@code view-source:} or {@code about:} address never reaches the user's
 * real Chromium profile.
 */
public final class WebUrls {

    public static final int MAX_LENGTH = 2000;

    private WebUrls() {
    }

    /**
     * Normalise what a player typed: http(s) kept, bare hosts get a scheme
     * (loopback: http, anything else: https). Null = not allowed.
     */
    public static String safe(String raw) {
        if (raw == null) {
            return null;
        }
        String v = raw.trim();
        if (v.isEmpty() || v.length() > MAX_LENGTH) {
            return null;
        }
        String lower = v.toLowerCase(Locale.ROOT);
        if (!lower.startsWith("http://") && !lower.startsWith("https://")) {
            if (lower.matches("^[a-z][a-z0-9+.-]*:(?!\\d).*")) {
                return null;                    // file:, javascript:, data:, chrome:, about:, ...
            }
            v = (v.matches("^(localhost|127\\.0\\.0\\.1|\\[::1\\])(:\\d+)?(/.*)?$") ? "http://" : "https://") + v;
        }
        v = v.replace(" ", "%20");              // a typed search with spaces still works
        return loadable(v) ? v : null;
    }

    /**
     * Whether a stored URL may be handed to the browser as it is: an absolute
     * http(s) URL with a host, no credentials, no whitespace or control
     * characters; or exactly {@code about:blank}.
     */
    public static boolean loadable(String url) {
        if (url == null || url.isEmpty() || url.length() > MAX_LENGTH) {
            return false;
        }
        if (url.equals("about:blank")) {
            return true;
        }
        for (int i = 0; i < url.length(); i++) {
            char c = url.charAt(i);
            if (c <= ' ' || c == 0x7F || c == '\\' || Character.isWhitespace(c) || Character.isISOControl(c)
                || Character.isSpaceChar(c)) {
                return false;
            }
        }
        return AUTHORITY.matcher(url).matches();
    }

    /** http(s)://host[:port] then a path, query or fragment; no user@ in the authority. */
    private static final Pattern AUTHORITY =
        Pattern.compile("^(?i:https?)://(?:[A-Za-z0-9._~%-]+|\\[[0-9A-Fa-f:.]+\\])(?::\\d{1,5})?(?:[/?#].*)?$");
}

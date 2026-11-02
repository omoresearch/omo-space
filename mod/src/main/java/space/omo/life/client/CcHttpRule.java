package space.omo.life.client;

import java.util.ArrayList;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Map;
import java.util.Set;

/**
 * Does CC: Tweaked let station computers reach the bridge? CC:T denies
 * private addresses ({@code $private}) unless {@code config/computercraft-server.toml}
 * has an allow rule for 127.0.0.1 ahead of that deny. Only the Omo Space
 * launcher and {@code omo_setup.py link} write that rule, so a plain store
 * install is missing it and terminals sit on "connecting".
 *
 * <p>The same decision as {@code launcher/omo_setup.py cct_rule_state}: the
 * first rule that could match 127.0.0.1:port decides. Only the subset of TOML
 * CC:T writes is read ({@code [[http.rules]]} tables of {@code key = value}).
 * No Minecraft classes, so it is testable on its own.
 */
public final class CcHttpRule {

    public enum State { OK, OPEN, MISSING }

    private static final Set<String> LOOPBACK = Set.of("127.0.0.1", "127.0.0.0/8", "localhost", "$private", "*");

    private CcHttpRule() {
    }

    /** {@link State#OK} or {@link State#OPEN} mean stations can connect. */
    public static boolean allows(State s) {
        return s == State.OK || s == State.OPEN;
    }

    public static State state(String toml, int port) {
        if (toml == null) {
            return State.MISSING;
        }
        for (Map<String, String> rule : rules(toml)) {
            String host = rule.get("host");
            if (host == null || !LOOPBACK.contains(host) || !rule.containsKey("action")) {
                continue;
            }
            if (rule.containsKey("port")) {
                try {
                    if (Integer.parseInt(rule.get("port")) != port) {
                        continue;
                    }
                } catch (NumberFormatException e) {
                    continue;
                }
            }
            if ("127.0.0.1".equals(host) && "allow".equals(rule.get("action"))) {
                return rule.containsKey("port") ? State.OK : State.OPEN;
            }
            return State.MISSING;
        }
        return State.MISSING;
    }

    /** The {@code [[http.rules]]} tables, in order, as raw key/value strings. */
    static List<Map<String, String>> rules(String toml) {
        List<Map<String, String>> out = new ArrayList<>();
        Map<String, String> current = null;
        for (String raw : toml.split("\r?\n")) {
            String line = stripComment(raw).trim();
            if (line.isEmpty()) {
                continue;
            }
            if (line.startsWith("[")) {
                current = null;
                if (line.replace(" ", "").equals("[[http.rules]]")) {
                    current = new LinkedHashMap<>();
                    out.add(current);
                }
                continue;
            }
            int eq = line.indexOf('=');
            if (current == null || eq < 0) {
                continue;
            }
            String key = line.substring(0, eq).trim();
            String value = line.substring(eq + 1).trim();
            if (value.length() >= 2 && (value.startsWith("\"") && value.endsWith("\"")
                    || value.startsWith("'") && value.endsWith("'"))) {
                value = value.substring(1, value.length() - 1);
            }
            current.put(key, value);
        }
        return out;
    }

    private static String stripComment(String line) {
        boolean dq = false;
        boolean sq = false;
        for (int i = 0; i < line.length(); i++) {
            char c = line.charAt(i);
            if (c == '"' && !sq) {
                dq = !dq;
            } else if (c == '\'' && !dq) {
                sq = !sq;
            } else if (c == '#' && !dq && !sq) {
                return line.substring(0, i);
            }
        }
        return line;
    }
}

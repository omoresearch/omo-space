package space.omo.life.client;

import java.util.Collections;
import java.util.LinkedHashMap;
import java.util.Locale;
import java.util.Map;
import java.util.regex.Pattern;

/**
 * Optional display labels for local agent bodies. With a label, the agent
 * named {@code builder_bot} can show as "Builder" over its head, in the tab
 * list and in chat, instead of the default "ChatGPT - builder_bot".
 *
 * JVM flag (Prism: Edit instance → Settings → Java arguments):
 *   -Domo.agents.labels=omo=Omo;builder_bot=Builder
 *
 * Entries are separated by ';', name and label by the first '='. Names are
 * matched case-insensitively and must be valid Minecraft usernames. A label
 * only applies to a player that is already an agent (AgentLan.isAgent, i.e.
 * listed in -Domo.agents.names); it never makes a name an agent. With no flag
 * (the default) nothing changes.
 *
 * Bad input never fails: malformed entries are skipped, labels are plain
 * text (no §x formatting codes or control characters) and at most
 * MAX_LABEL characters, and at most MAX_ENTRIES names are read.
 *
 * Plain Java (no Minecraft types) so the parsing can be tested on its own
 * (tests/test_agent_labels.py).
 */
public final class AgentLabels {
    public static final String PROPERTY = "omo.agents.labels";
    public static final int MAX_LABEL = 32;
    public static final int MAX_ENTRIES = 64;
    private static final Pattern USERNAME = Pattern.compile("[a-z0-9_]{1,16}");

    private static final Map<String, String> LABELS = load();

    private AgentLabels() {}

    private static Map<String, String> load() {
        try {
            return parse(System.getProperty(PROPERTY));
        } catch (RuntimeException e) {
            System.err.println("[omolife] ignoring -D" + PROPERTY + ": " + e);
            return Map.of();
        }
    }

    /** Parses "name=Label;name2=Label 2". Blank or malformed entries are skipped. */
    public static Map<String, String> parse(String spec) {
        Map<String, String> out = new LinkedHashMap<>();
        if (spec == null || spec.isBlank()) return Collections.unmodifiableMap(out);
        for (String entry : spec.split(";", MAX_ENTRIES * 4)) {
            if (out.size() >= MAX_ENTRIES) break;
            int eq = entry.indexOf('=');
            if (eq <= 0) continue;
            String name = entry.substring(0, eq).trim().toLowerCase(Locale.ROOT);
            String label = clean(entry.substring(eq + 1));
            if (!USERNAME.matcher(name).matches() || label.isEmpty()) continue;
            out.put(name, label);
        }
        return Collections.unmodifiableMap(out);
    }

    /** Plain text only: drops §x formatting codes and control characters, collapses spaces, caps the length. */
    static String clean(String raw) {
        StringBuilder sb = new StringBuilder();
        int count = 0;
        boolean space = false;
        for (int i = 0; i < raw.length() && count < MAX_LABEL; ) {
            int cp = raw.codePointAt(i);
            i += Character.charCount(cp);
            if (cp == '§') {                     // a formatting code: drop it and its code character
                if (i < raw.length()) i += Character.charCount(raw.codePointAt(i));
                continue;
            }
            if (Character.isISOControl(cp) || !Character.isDefined(cp)
                || Character.getType(cp) == Character.FORMAT || Character.getType(cp) == Character.PRIVATE_USE) {
                continue;
            }
            if (Character.isWhitespace(cp) || Character.isSpaceChar(cp)) {
                space = sb.length() > 0;
                continue;
            }
            if (space) {
                if (count + 1 >= MAX_LABEL) break;
                sb.append(' ');
                count++;
                space = false;
            }
            sb.appendCodePoint(cp);
            count++;
        }
        return sb.toString();
    }

    /** The label for this username, or null when it has none. */
    public static String label(String name) {
        return name == null || LABELS.isEmpty() ? null : LABELS.get(name.toLowerCase(Locale.ROOT));
    }

    public static Map<String, String> all() {
        return LABELS;
    }
}

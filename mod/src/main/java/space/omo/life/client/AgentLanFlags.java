package space.omo.life.client;

import java.net.InetAddress;
import java.util.Locale;
import java.util.Set;

/**
 * The switches of {@link AgentLan}, kept free of Minecraft classes so the
 * common mixins can ask them on a dedicated server and tests can run them
 * on their own.
 *
 * <p>Agent LAN is <b>off unless asked for</b>: {@code -Domo.agents.lan=true}.
 * The Omo Space launcher passes that flag for its own installs (it runs the
 * local agent bodies); a store install never opens a world to LAN by itself.
 */
public final class AgentLanFlags {

    /** Shown before an agent body's name over its head, in the tab list and in chat. */
    public static final String TAG_PREFIX = "Agent - ";

    private AgentLanFlags() {
    }

    /** Only an explicit {@code true} turns it on; unset, empty or anything else is off. */
    public static boolean enabled(String property) {
        return property != null && "true".equalsIgnoreCase(property.trim());
    }

    public static boolean enabled() {
        return enabled(System.getProperty("omo.agents.lan"));
    }

    /**
     * The address the agent LAN listener binds: loopback, so nothing off this
     * computer can reach it, unless remote joins were allowed on purpose
     * ({@code -Domo.agents.allowRemote=true}), which keeps vanilla's
     * all-interfaces bind ({@code requested}, normally null).
     */
    public static InetAddress bindAddress(boolean allowRemote, InetAddress requested) {
        return allowRemote ? requested : InetAddress.getLoopbackAddress();
    }

    /** Lower-case agent body names from {@code -Domo.agents.names} (default omo,teacher). */
    public static Set<String> names(String property) {
        String raw = property == null || property.isBlank() ? "omo,teacher" : property;
        return Set.of(java.util.Arrays.stream(raw.toLowerCase(Locale.ROOT).split(","))
            .map(String::trim).filter(s -> !s.isEmpty()).distinct().toArray(String[]::new));
    }

    private static final Set<String> NAMES = names(System.getProperty("omo.agents.names"));

    /** An agent body: only while agent LAN is on, so a player called "omo" elsewhere is just a player. */
    public static boolean isAgent(boolean enabled, Set<String> names, String name) {
        return enabled && name != null && names.contains(name.toLowerCase(Locale.ROOT));
    }

    public static boolean isAgent(String name) {
        return isAgent(enabled(), NAMES, name);
    }
}

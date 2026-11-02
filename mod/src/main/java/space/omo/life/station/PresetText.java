package space.omo.life.station;

/**
 * Cleans the text of a station preset (no Minecraft classes, so tests run it
 * directly). A preset can come from a shared map's block NBT, an item, the
 * Station Setup screen or a command, and its names end up as tmux window
 * names on the host. tmux expands {@code #(...)} and {@code #{...}} in those
 * (a {@code #(cmd)} name runs {@code cmd}), so {@code #} never survives here,
 * whatever the bridge does; nor do control characters. Lengths are capped
 * as the Station Setup screen caps them.
 */
public final class PresetText {

    public static final int NAME_MAX = 40;
    public static final int TAB_MAX = 24;
    public static final int TABS_MAX = 8;
    public static final int PROFILE_MAX = 64;

    private PresetText() {
    }

    /** A workspace or tab name: no '#', no control characters, trimmed, at most {@code max} chars. */
    public static String name(String raw, int max) {
        if (raw == null) {
            return "";
        }
        StringBuilder out = new StringBuilder(Math.min(raw.length(), max));
        raw.codePoints().forEach(cp -> {
            if (cp != '#' && !Character.isISOControl(cp) && Character.getType(cp) != Character.FORMAT
                && cp != 0x2028 && cp != 0x2029) {
                out.appendCodePoint(cp);
            }
        });
        String s = out.toString().strip();
        return s.length() <= max ? s : s.substring(0, max).strip();
    }

    /** A Hermes profile name: letters, digits, '.', '_' and '-' only, else "" (the default profile). */
    public static String profile(String raw) {
        if (raw == null) {
            return "";
        }
        String s = raw.strip();
        return s.length() <= PROFILE_MAX && s.matches("[A-Za-z0-9._-]*") ? s : "";
    }
}

package space.omo.life.client;

import java.io.IOException;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.nio.file.StandardCopyOption;
import java.util.Collection;
import java.util.Locale;

/**
 * What a new tab runs: the rows of the station header's "+" menu, in order.
 * A shell and Hermes as always; Claude Code and Codex start their CLI in the
 * tab's shell on the host, like a Hermes tab. {@link #id} is the bridge's
 * {@code tab_new} kind.
 *
 * <p>No Minecraft types (tests/test_tab_kinds.py compiles it alone): the
 * glyph for each kind lives in {@link OmoStyle#glyph(TabKind)}.
 */
public enum TabKind {
    SHELL("shell", "Shell", "Ctrl+Shift+T", 0xFFC8703F),
    HERMES("hermes", "Hermes", "Ctrl+Shift+H", 0xFFDDA744),
    CLAUDE("claude", "Claude Code", "", 0xFFD97757),
    CODEX("codex", "Codex", "", 0xFF8FB8DE);

    /** The bridge's tab kind. */
    public final String id;
    /** Menu label and the default tab name. */
    public final String label;
    /** Its keyboard shortcut, shown in the menu ("" = none). */
    public final String keys;
    /** Glyph colour in the menu and on the tab (ARGB). */
    public final int colour;

    TabKind(String id, String label, String keys, int colour) {
        this.id = id;
        this.label = label;
        this.keys = keys;
        this.colour = colour;
    }

    /** The kind for a bridge id; anything unknown is a shell, as on the bridge. */
    public static TabKind of(String id) {
        if (id != null) {
            for (TabKind k : values()) {
                if (k.id.equals(id.trim().toLowerCase(Locale.ROOT))) {
                    return k;
                }
            }
        }
        return SHELL;
    }

    /** An agent CLI (Claude Code, Codex) rather than a shell or Hermes. */
    public boolean agent() {
        return this == CLAUDE || this == CODEX;
    }

    /**
     * Can this bridge open it? {@code bridgeKinds} is the welcome's
     * {@code tab_kinds}; null means a bridge from before agent tabs, which
     * would quietly open a plain shell for anything but shell and Hermes.
     */
    public boolean supportedBy(Collection<String> bridgeKinds) {
        if (bridgeKinds == null) {
            return this == SHELL || this == HERMES;
        }
        return bridgeKinds.contains(id);
    }

    /**
     * The menu's right-hand note: why a row may not do what it says, else its
     * shortcut. {@code found} is the bridge's answer for the CLI (null = not
     * known yet). A missing CLI still opens: the tab explains how to install it.
     */
    public String menuDetail(boolean supported, Boolean found) {
        if (!supported) {
            return "update bridge";
        }
        if (Boolean.FALSE.equals(found)) {
            return "not installed";
        }
        return keys;
    }

    /** The last kind picked from "+", or Shell when there is none or it cannot be read. */
    public static TabKind readLast(Path file) {
        try {
            String s = Files.readString(file, StandardCharsets.UTF_8).trim();
            for (TabKind k : values()) {
                if (k.id.equals(s)) {
                    return k;
                }
            }
        } catch (IOException | RuntimeException ignored) {
            // first use, or a hand-edited file: the default is fine
        }
        return SHELL;
    }

    /** Remember the pick (atomically: a crash never leaves half a file). Failure is ignored. */
    public static void writeLast(Path file, TabKind kind) {
        try {
            Path tmp = file.resolveSibling(file.getFileName() + ".tmp");
            Files.writeString(tmp, kind.id + "\n", StandardCharsets.UTF_8);
            Files.move(tmp, file, StandardCopyOption.REPLACE_EXISTING, StandardCopyOption.ATOMIC_MOVE);
        } catch (IOException | RuntimeException ignored) {
            // a read-only config dir only costs the remembered choice
        }
    }
}

package space.omo.life;

import java.util.Set;

/**
 * What players see by default: two blocks, the <b>Omo Computer</b>
 * ({@code omolife:hermes_computer}) and the <b>Omo Monitor</b>
 * ({@code omolife:monitor}). CC: Tweaked's own blocks and items, and Omo's
 * extra ones (Terminal, Slim Screen, Name Plate, Status Lamp, Linker,
 * pockets), leave the creative menu and their recipes are switched off by
 * the built-in {@code omolife:simple_blocks} data pack (generated from the
 * CC:T jar by {@code mod/tools/SimpleBlocksPack.java}).
 *
 * <p>Nothing is unregistered: blocks already placed in old worlds keep
 * working, CC:T's computers and monitors that Omo creates under the hood
 * are untouched, and {@code /give} still works. {@code -Domo.showAllBlocks=true}
 * or {@code showAllBlocks=true} in {@code config/omolife.properties} brings
 * everything back. No Minecraft classes, so it is testable on its own.
 */
public final class HiddenBlocks {

    public static final Set<String> OMO_VISIBLE = Set.of("hermes_computer", "monitor");
    public static final String PACK = "simple_blocks";

    private HiddenBlocks() {
    }

    /** Whether an item with this registry id leaves the creative menu (while hiding is on). */
    public static boolean hidden(String namespace, String path) {
        if ("computercraft".equals(namespace)) {
            return true;
        }
        return "omolife".equals(namespace) && !OMO_VISIBLE.contains(path);
    }
}

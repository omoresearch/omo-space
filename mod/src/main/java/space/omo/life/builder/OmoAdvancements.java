package space.omo.life.builder;

import net.minecraft.advancements.AdvancementHolder;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

import space.omo.life.OmoLife;

/**
 * The "Omo Space" advancement tab ({@code data/omolife/advancement/}).
 *
 * <p>Most are plain vanilla triggers in the JSON (placing a station, getting
 * a pocket). The ones vanilla cannot see have a single
 * {@code minecraft:impossible} criterion named {@value #CRITERION}, granted
 * here when the thing happens. Granting twice is a no-op, so there is no
 * spam however often it happens.
 */
public final class OmoAdvancements {

    public static final String OPEN_HERMES = "open_hermes";
    public static final String PIN_PAGE = "pin_page";
    public static final String BIG_SCREEN = "big_screen";
    public static final String CONTROL_AFAR = "control_afar";
    public static final String LINK_SCREEN = "link_screen";

    static final String CRITERION = "done";

    private OmoAdvancements() {
    }

    public static void award(ServerPlayer player, String name) {
        MinecraftServer server = player.level().getServer();
        AdvancementHolder adv = server == null ? null : server.getAdvancements().get(OmoLife.id(name));
        if (adv != null) {
            player.getAdvancements().award(adv, CRITERION);
        }
    }
}

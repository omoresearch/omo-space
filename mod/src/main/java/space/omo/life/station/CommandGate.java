package space.omo.life.station;

import java.util.Collection;

import dan200.computercraft.shared.computer.core.ServerComputer;

import net.minecraft.commands.CommandSource;
import net.minecraft.commands.CommandSourceStack;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.server.permissions.Permissions;
import net.minecraft.server.rcon.RconConsoleSource;

import space.omo.life.mixin.CommandSourceAccessor;

/**
 * Who may run commands that reach stations: {@code /omo} and CC:T's
 * {@code /computercraft view|shutdown|turn-on|tp} on a station computer.
 *
 * <p>Only a real player that {@link Stations#allowed} lets in, typing the
 * command themselves, or the server console (and RCON, which needs its
 * password). Not command blocks, not data pack functions (including
 * {@code execute as <owner> run ...} from either), not command computers: a
 * shared map ships all of those, so none of them may forget, rename or
 * re-profile workspaces or open a station's shell.
 */
public final class CommandGate {

    private CommandGate() {
    }

    public static boolean trusted(CommandSourceStack src) {
        CommandSource origin = ((CommandSourceAccessor) src).omolife$source();
        if (origin instanceof ServerPlayer player) {
            return src.getEntity() == player && Stations.allowed(player);
        }
        if (origin instanceof RconConsoleSource) {
            return true;
        }
        // The console runs with owner permissions; functions run with the
        // function permission level (gamemaster by default) and the same
        // server source, so the level is what tells them apart.
        return origin instanceof MinecraftServer && src.getEntity() == null
            && src.permissions().hasPermission(Permissions.COMMANDS_OWNER);
    }

    /**
     * CC:T's command is about to act on {@code computers}: true (and a
     * failure message) when one of them is a station and {@code src} is not
     * {@link #trusted}.
     */
    public static boolean refuseStations(CommandSourceStack src, Collection<ServerComputer> computers) {
        if (computers == null || trusted(src)) {
            return false;
        }
        for (ServerComputer c : computers) {
            if (StationComputers.of(c) != null) {
                src.sendFailure(Component.literal("That is an Omo station: only the world owner (or an operator) "
                    + "can view or control it."));
                return true;
            }
        }
        return false;
    }

    /** CC:T's computer GUI and its input packets: a station only for players {@link Stations#allowed} lets in. */
    public static boolean mayUseComputer(ServerPlayer player, ServerComputer computer) {
        Station station = StationComputers.of(computer);
        if (station == null) {
            return true;
        }
        if (!Stations.allowed(player)) {
            return false;
        }
        OwnerPresence.touch(station, player);
        return true;
    }
}

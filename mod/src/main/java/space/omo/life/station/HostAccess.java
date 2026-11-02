package space.omo.life.station;

import java.util.UUID;

/**
 * Who may reach host state (shells, workspaces, monitors), as a pure rule so
 * it can be tested without a server. {@link Stations#allowed} feeds it.
 *
 * <ul>
 *   <li>The single-player owner, matched by <b>UUID</b>. Vanilla's
 *       {@code isSingleplayerOwner} compares names only, so with offline
 *       logins (agent LAN) anyone could claim the owner's name.</li>
 *   <li>A player on the server's <b>operator list</b> ({@code /op}), but
 *       only when that entry can be trusted: the server authenticates
 *       players with Microsoft, or the player connects from this computer.
 *       In offline mode (agent LAN) anyone can log in under an op's name.</li>
 * </ul>
 *
 * <p>Deliberately <i>not</i> {@code PlayerList.isOp}: in a world opened to LAN
 * with cheats on it answers true for every player
 * ({@code allowCommandsForAllPlayers}), and "cheats for everyone" must never
 * mean "shells for everyone".
 */
public final class HostAccess {

    private HostAccess() {
    }

    /**
     * @param player   the player's UUID
     * @param owner    the single-player owner's UUID, or null (dedicated server)
     * @param onOpList whether the player is on the operator list itself
     * @param opTrusted whether an operator entry counts for this connection:
     *                 the server authenticates players, or the player is local
     */
    public static boolean allowed(UUID player, UUID owner, boolean onOpList, boolean opTrusted) {
        if (player == null) {
            return false;
        }
        return player.equals(owner) || (onOpList && opTrusted);
    }
}

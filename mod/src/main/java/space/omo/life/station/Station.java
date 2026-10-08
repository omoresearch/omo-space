package space.omo.life.station;

/**
 * A computer that is an Omo station, server side: a Hermes Computer or
 * Terminal block, or an Omo Pocket. The {@code omo} Lua API, the station
 * packets and {@code /omo} all work through this, so none of them care which
 * kind of station they talk to.
 *
 * <p>Identity: {@link #workspace()} is the durable binding and travels with
 * the item; {@link #stationIdOrEmpty()} names this one screen, for tickets
 * and steering. Neither is ever a secret.
 */
public interface Station {

    String workspace();

    String workspaceName();

    /** The station id, or "" before one is minted. Safe from any thread. */
    String stationIdOrEmpty();

    /** Whether this station's own workspace starts with a Hermes tab (and its accent is gold). */
    boolean isHermes();

    default boolean isBound() {
        return !workspace().isEmpty();
    }

    /** False once the block is broken or the pocket's computer has shut down for good. */
    boolean isPresent();

    /**
     * Make sure a workspace exists: created on the host when this station has
     * none, never a second one. Server thread.
     */
    void ensureWorkspace();

    /** Point this station at a workspace. Its screen reconnects at once. Server thread. */
    void bind(String workspace, String name);

    void setWorkspaceName(String name);
}

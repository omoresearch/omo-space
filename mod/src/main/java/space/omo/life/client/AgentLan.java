package space.omo.life.client;

import java.io.IOException;
import java.net.InetSocketAddress;
import java.net.SocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;

import net.fabricmc.fabric.api.client.networking.v1.ClientPlayConnectionEvents;
import net.fabricmc.fabric.api.event.lifecycle.v1.ServerLifecycleEvents;
import net.fabricmc.fabric.api.networking.v1.ServerPlayConnectionEvents;
import net.minecraft.client.server.IntegratedServer;
import net.minecraft.ChatFormatting;
import net.minecraft.network.chat.Component;
import net.minecraft.world.scores.PlayerTeam;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerPlayer;

/**
 * Lets local AI agents (the Omo Teacher's Mineflayer body) join your
 * single-player world as real players. <b>Off unless {@code -Domo.agents.lan=true}</b>
 * (the Omo Space launcher sets it for its own installs; a mod installed from a
 * store never opens a world on its own).
 *
 * When on and a world opens, it is published on a fixed port (25566 by
 * default) and offline logins are accepted, because a bot has no Microsoft
 * account. The listener binds to 127.0.0.1 only, so nothing off this computer
 * can connect, the LAN broadcast is skipped, and the join guard below still
 * disconnects any unauthenticated player that is not on loopback. Joiners are
 * never let at host shells: {@link space.omo.life.station.HostAccess} ignores
 * "cheats for everyone".
 *
 * The chosen port is written to ~/.config/omo/lan.json so the agent runtime
 * can find the world.
 *
 * JVM flags (Prism: Edit instance → Settings → Java arguments):
 *   -Domo.agents.lan=true           turn the feature on
 *   -Domo.agents.port=25566         preferred LAN port
 *   -Domo.agents.allowRemote=true   bind all interfaces and accept unauthenticated LAN players
 */
public final class AgentLan {
    private static final boolean ENABLED = AgentLanFlags.enabled();
    private static final int PORT = Integer.getInteger("omo.agents.port", 25566);
    private static final boolean ALLOW_REMOTE = Boolean.getBoolean("omo.agents.allowRemote");
    private static final Path STATE = Path.of(System.getProperty("user.home"), ".config", "omo", "lan.json");

    /** True only while {@link #publish} calls vanilla's publishServer, so the bind mixin leaves the Open to LAN screen alone. */
    private static volatile boolean publishing;

    private AgentLan() {}

    /** From the bind mixin: the address vanilla's LAN listener should use. */
    public static java.net.InetAddress bindAddress(java.net.InetAddress requested) {
        return publishing ? AgentLanFlags.bindAddress(ALLOW_REMOTE, requested) : requested;
    }

    /** From the bind mixin: skip the LAN broadcast for a loopback-only agent listener. */
    public static boolean announceOnLan() {
        return !publishing || ALLOW_REMOTE;
    }

    public static void register() {
        if (!ENABLED) return;
        // Publishing needs the host's own client connection (it signs with the
        // player's key pair), so wait until the host has actually joined, then
        // publish from the client thread exactly like the Open to LAN screen.
        ClientPlayConnectionEvents.JOIN.register((handler, sender, client) -> client.execute(() -> {
            IntegratedServer integrated = client.getSingleplayerServer();
            if (integrated != null) publish(integrated);
        }));
        ServerLifecycleEvents.SERVER_STOPPING.register(server -> clearState());
        ServerPlayConnectionEvents.JOIN.register((handler, sender, server) -> guard(handler.player, server));
    }

    private static void publish(IntegratedServer server) {
        int port = -1;
        if (server.isPublished()) {
            port = server.getPort();
        } else {
            boolean commands = server.getWorldData().isAllowCommands();
            for (int candidate = PORT; candidate < PORT + 5 && port < 0; candidate++) {
                publishing = true;
                try {
                    if (server.publishServer(MinecraftServer.MultiplayerScope.LAN, server.getDefaultGameType(), commands, candidate)) {
                        port = candidate;
                    }
                } catch (RuntimeException e) {
                    System.err.println("[omolife] opening to LAN on " + candidate + " failed: " + e);
                } finally {
                    publishing = false;
                }
            }
        }
        if (port < 0) {
            System.err.println("[omolife] could not open the world to LAN for agents on ports " + PORT + "-" + (PORT + 4));
            return;
        }
        // A bot has no Microsoft account; the join guard below keeps this local.
        server.setUsesAuthentication(false);
        writeState(port, server.getWorldData().getLevelName());
        System.out.println("[omolife] agents can join this world at 127.0.0.1:" + port
            + (ALLOW_REMOTE ? " (remote joins allowed)" : " (this computer only)"));
    }

    public static boolean isAgent(String name) {
        return AgentLanFlags.isAgent(name);
    }

    // Shows the agent as "Agent - omo" over its head, in the tab list and in chat.
    private static void tag(ServerPlayer player, MinecraftServer server) {
        var board = server.getScoreboard();
        PlayerTeam team = board.getPlayerTeam("omo_agents");
        if (team == null) team = board.addPlayerTeam("omo_agents");
        team.setPlayerPrefix(Component.literal(AgentLanFlags.TAG_PREFIX).withStyle(ChatFormatting.AQUA));
        board.addPlayerToTeam(player.getScoreboardName(), team);
    }

    private static void guard(ServerPlayer player, MinecraftServer server) {
        if (!server.usesAuthentication() && isAgent(player.getScoreboardName())) tag(player, server);
        if (ALLOW_REMOTE || server.usesAuthentication()) return;
        // The owner by UUID: vanilla's isSingleplayerOwner matches names, which offline logins can fake.
        var owner = server.getSingleplayerProfile();
        if (owner != null && player.getUUID().equals(owner.id())) return;
        SocketAddress address = player.connection.getRemoteAddress();
        boolean local = !(address instanceof InetSocketAddress inet) || inet.getAddress() == null
            || inet.getAddress().isLoopbackAddress();
        if (!local) {
            player.connection.disconnect(Component.literal(
                "This world only accepts local Omo agents."));
        }
    }

    private static void writeState(int port, String world) {
        String json = "{\"host\":\"127.0.0.1\",\"port\":" + port + ",\"world\":\"" + world.replace("\\", "\\\\").replace("\"", "\\\"")
            + "\",\"version\":\"26.2\"}\n";
        try {
            Files.createDirectories(STATE.getParent());
            Files.writeString(STATE, json, StandardCharsets.UTF_8);
        } catch (IOException e) {
            System.err.println("[omolife] could not write " + STATE + ": " + e.getMessage());
        }
    }

    private static void clearState() {
        try {
            Files.deleteIfExists(STATE);
        } catch (IOException ignored) {
            // stale file only; the runtime re-checks the port before connecting
        }
    }
}

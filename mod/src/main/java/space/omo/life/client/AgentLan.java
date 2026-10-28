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
 * single-player world as real players.
 *
 * When a world opens, it is published to LAN on a fixed port (25566 by
 * default) and offline logins are accepted, because a bot has no Microsoft
 * account. To keep that from opening the world to anyone on the network,
 * unauthenticated players may join only from this computer (loopback), unless
 * you opt in to remote joins.
 *
 * The chosen port is written to ~/.config/omo/lan.json so the agent runtime
 * can find the world.
 *
 * JVM flags (Prism: Edit instance → Settings → Java arguments):
 *   -Domo.agents.lan=false          turn the whole feature off
 *   -Domo.agents.port=25566         preferred LAN port
 *   -Domo.agents.allowRemote=true   also accept unauthenticated LAN players
 */
public final class AgentLan {
    private static final boolean ENABLED = !"false".equalsIgnoreCase(System.getProperty("omo.agents.lan", "true"));
    private static final int PORT = Integer.getInteger("omo.agents.port", 25566);
    private static final boolean ALLOW_REMOTE = Boolean.getBoolean("omo.agents.allowRemote");
    private static final Path STATE = Path.of(System.getProperty("user.home"), ".config", "omo", "lan.json");

    private AgentLan() {}

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
                try {
                    if (server.publishServer(MinecraftServer.MultiplayerScope.LAN, server.getDefaultGameType(), commands, candidate)) {
                        port = candidate;
                    }
                } catch (RuntimeException e) {
                    System.err.println("[omolife] opening to LAN on " + candidate + " failed: " + e);
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
        System.out.println("[omolife] agents can join this world at 127.0.0.1:" + port);
    }

    /** Lower-case usernames of the local agent bodies. */
    public static final java.util.Set<String> AGENT_NAMES = java.util.Set.of(
        System.getProperty("omo.agents.names", "omo,teacher").toLowerCase(java.util.Locale.ROOT).split(","));

    public static boolean isAgent(String name) {
        return name != null && AGENT_NAMES.contains(name.toLowerCase(java.util.Locale.ROOT));
    }

    // Shows the agent as "ChatGPT - OMO" over his head, in the tab list and in chat.
    private static void tag(ServerPlayer player, MinecraftServer server) {
        var board = server.getScoreboard();
        PlayerTeam team = board.getPlayerTeam("omo_agents");
        if (team == null) team = board.addPlayerTeam("omo_agents");
        team.setPlayerPrefix(Component.literal("ChatGPT - ").withStyle(ChatFormatting.AQUA));
        board.addPlayerToTeam(player.getScoreboardName(), team);
    }

    private static void guard(ServerPlayer player, MinecraftServer server) {
        if (!server.usesAuthentication() && isAgent(player.getScoreboardName())) tag(player, server);
        if (ALLOW_REMOTE || server.usesAuthentication()) return;
        if (server.isSingleplayerOwner(player.nameAndId())) return;
        SocketAddress address = player.connection.getRemoteAddress();
        boolean local = !(address instanceof InetSocketAddress inet) || inet.getAddress() == null
            || inet.getAddress().isLoopbackAddress();
        if (!local) {
            player.connection.disconnect(Component.literal(
                "This world only accepts local Omo agents. The host can allow LAN players with -Domo.agents.allowRemote=true."));
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

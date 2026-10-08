package space.omo.life.station;

import java.io.IOException;
import java.net.URI;
import java.net.URLEncoder;
import java.net.http.HttpClient;
import java.net.http.HttpRequest;
import java.net.http.HttpResponse;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.Duration;
import java.util.Map;
import java.util.concurrent.CompletableFuture;

import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import space.omo.life.OmoLife;

/**
 * The game's side of the host bridge: plain HTTP on loopback, token from the
 * user's own data directory.
 *
 * <p>Where the bridge lives is fixed to {@code 127.0.0.1}; only the port and
 * the data directory can move, through JVM system properties so a test
 * instance can run against its own bridge:
 * <ul>
 *   <li>{@code -Domo.port=8780} (default 8780)</li>
 *   <li>{@code -Domo.data=/path} the directory holding {@code token}
 *       (default {@code ~/.local/share/omo}; macOS
 *       {@code ~/Library/Application Support/OmoResearch}; Windows
 *       {@code %LOCALAPPDATA%\OmoResearch})</li>
 * </ul>
 *
 * <p>The token never enters world data, item components, Lua or packets to
 * clients. A station's Lua client receives a single-use ticket minted by the
 * bridge and bound to that station and its workspace.
 */
public final class BridgeLink {

    public static final String HOST = "127.0.0.1";
    public static final int DEFAULT_PORT = 8780;

    private static final HttpClient HTTP = HttpClient.newBuilder()
        .version(HttpClient.Version.HTTP_1_1)
        .connectTimeout(Duration.ofSeconds(2)).build();

    private static volatile String cachedToken;
    private static volatile long tokenReadAt;

    private BridgeLink() {
    }

    public static int port() {
        int p = Integer.getInteger("omo.port", DEFAULT_PORT);
        return p > 0 && p < 65536 ? p : DEFAULT_PORT;
    }

    public static String baseUrl() {
        return "http://" + HOST + ":" + port();
    }

    public static String wsUrl() {
        return "ws://" + HOST + ":" + port() + "/ws";
    }

    public static Path dataDir() {
        String prop = System.getProperty("omo.data");
        if (prop != null && !prop.isBlank()) {
            return Path.of(prop);
        }
        // Must match launcher/omo_setup.py default_data_dir().
        String os = System.getProperty("os.name", "").toLowerCase(java.util.Locale.ROOT);
        String home = System.getProperty("user.home");
        if (os.startsWith("mac")) {
            return Path.of(home, "Library", "Application Support", "OmoResearch");
        }
        if (os.startsWith("windows")) {
            String local = System.getenv("LOCALAPPDATA");
            return (local != null && !local.isBlank() ? Path.of(local) : Path.of(home, "AppData", "Local"))
                .resolve("OmoResearch");
        }
        return Path.of(home, ".local", "share", "omo");
    }

    /** The bridge token, or null when the bridge has never run on this host. */
    public static String token() {
        long now = System.currentTimeMillis();
        if (cachedToken != null && now - tokenReadAt < 30_000L) {
            return cachedToken;
        }
        try {
            String tok = Files.readString(dataDir().resolve("token")).trim();
            cachedToken = tok.isEmpty() ? null : tok;
        } catch (IOException e) {
            cachedToken = null;
        }
        tokenReadAt = now;
        return cachedToken;
    }

    public static boolean available() {
        return token() != null;
    }

    private static String encode(String s) {
        return URLEncoder.encode(s == null ? "" : s, StandardCharsets.UTF_8);
    }

    private static String query(String tok, Map<String, String> params) {
        StringBuilder q = new StringBuilder("?token=").append(encode(tok));
        params.forEach((k, v) -> q.append('&').append(encode(k)).append('=').append(encode(v)));
        return q.toString();
    }

    /**
     * Call one JSON endpoint. Never throws: a failure comes back as an object
     * with an {@code error} string so callers can show it in-game.
     */
    public static CompletableFuture<JsonObject> api(String op, Map<String, String> params) {
        String tok = token();
        if (tok == null) {
            return CompletableFuture.completedFuture(error(notSetUp()));
        }
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl() + "/api/" + op + query(tok, params)))
            .timeout(Duration.ofSeconds(8)).GET().build();
        return HTTP.sendAsync(req, HttpResponse.BodyHandlers.ofString()).handle((resp, err) -> {
            if (err != null) {
                return error("Omo bridge unreachable: " + rootMessage(err));
            }
            try {
                JsonObject obj = JsonParser.parseString(resp.body()).getAsJsonObject();
                if (resp.statusCode() >= 400 && !obj.has("error")) {
                    obj.addProperty("error", "bridge HTTP " + resp.statusCode());
                }
                return obj;
            } catch (RuntimeException e) {
                return error(resp.statusCode() == 403 ? "the bridge refused this game's token"
                    : "bridge sent an unreadable reply (HTTP " + resp.statusCode() + ")");
            }
        });
    }

    /** A minted ticket, or why there is none. Exactly one of the two is non-null. */
    public record Ticket(String ticket, String error) {
    }

    /**
     * A single-use connection ticket for one station looking at one
     * workspace. Completes within a few seconds whatever the bridge does.
     */
    public static CompletableFuture<Ticket> ticket(String station, String workspace) {
        String tok = token();
        if (tok == null) {
            return CompletableFuture.completedFuture(new Ticket(null, "bridge not set up on this computer"));
        }
        URI uri = URI.create(baseUrl() + "/ticket" + query(tok, Map.of("station", station, "ws", workspace)));
        HttpRequest req = HttpRequest.newBuilder(uri).timeout(Duration.ofSeconds(5)).GET().build();
        return HTTP.sendAsync(req, HttpResponse.BodyHandlers.ofString()).handle((resp, err) -> {
            if (err != null) {
                return new Ticket(null, "bridge unreachable");
            }
            if (resp.statusCode() == 403) {
                return new Ticket(null, "bridge refused this station");
            }
            String body = resp.body() == null ? "" : resp.body().trim();
            if (resp.statusCode() != 200 || body.isEmpty()) {
                return new Ticket(null, "bridge error (HTTP " + resp.statusCode() + ")");
            }
            if (body.startsWith("{")) {
                try {
                    JsonObject o = JsonParser.parseString(body).getAsJsonObject();
                    if (o.has("ticket")) {
                        return new Ticket(o.get("ticket").getAsString(), null);
                    }
                    return new Ticket(null, o.has("error") ? o.get("error").getAsString() : "bridge error");
                } catch (RuntimeException e) {
                    return new Ticket(null, "bridge sent an unreadable ticket");
                }
            }
            return new Ticket(body, null);
        });
    }

    public static CompletableFuture<Boolean> health() {
        HttpRequest req = HttpRequest.newBuilder(URI.create(baseUrl() + "/health"))
            .timeout(Duration.ofSeconds(3)).GET().build();
        return HTTP.sendAsync(req, HttpResponse.BodyHandlers.ofString())
            .handle((resp, err) -> err == null && resp.statusCode() == 200);
    }

    public static String notSetUp() {
        return "Omo bridge is not set up on this computer (no token in " + dataDir() + ")";
    }

    public static String describeSetupHint() {
        return "Start it with: systemctl --user start omo-tmux omo-bridge";
    }

    private static JsonObject error(String m) {
        JsonObject o = new JsonObject();
        o.addProperty("error", m);
        return o;
    }

    private static String rootMessage(Throwable t) {
        while (t.getCause() != null) {
            t = t.getCause();
        }
        String m = t.getMessage();
        return m == null || m.isBlank() ? t.getClass().getSimpleName() : m;
    }

    static {
        OmoLife.LOG.info("Omo bridge endpoint {} (data {})", baseUrl(), dataDir());
    }
}

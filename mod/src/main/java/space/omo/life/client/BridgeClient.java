package space.omo.life.client;

import java.net.URI;
import java.net.http.HttpClient;
import java.net.http.WebSocket;
import java.time.Duration;
import java.util.List;
import java.util.concurrent.CompletionStage;
import java.util.concurrent.CopyOnWriteArrayList;
import java.util.function.Consumer;

import com.google.gson.Gson;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;

import net.minecraft.client.Minecraft;

import space.omo.life.OmoLife;
import space.omo.life.station.BridgeLink;

/**
 * The control-plane websocket to the bridge, used by the station panel.
 *
 * <p>This is the "ui" role: it creates, renames and closes tabs, sets a
 * workspace's Hermes profile and steers a station's screen to a tab. It never
 * carries terminal frames; those flow on the computer's own websocket (the
 * Lua client), so painting stays in CC:T's terminal.
 *
 * <p>Same machine, same user, so it authenticates with the real token from
 * the bridge's data directory. On a machine without the bridge it stays
 * disconnected and the panel says why.
 *
 * <p>Every connection attempt gets a generation number. Callbacks from an
 * older attempt (a slow handshake, a socket closing after its replacement
 * opened) are ignored and their sockets aborted, so there is never more than
 * one live control socket and a stale close never marks the new one dead.
 */
public final class BridgeClient {

    private static final BridgeClient INSTANCE = new BridgeClient();
    private static final Gson GSON = new Gson();
    private static final long RETRY_MS = 1000L;

    private final HttpClient http = HttpClient.newBuilder().version(HttpClient.Version.HTTP_1_1)
        .connectTimeout(Duration.ofSeconds(2)).build();
    private final List<Consumer<JsonObject>> listeners = new CopyOnWriteArrayList<>();
    private final Object lock = new Object();

    private volatile WebSocket socket;
    private volatile boolean welcomed;
    private volatile String lastError = "";
    private volatile int generation; // written under lock
    private boolean connecting;      // guarded by lock
    private long lastAttempt;        // guarded by lock

    private BridgeClient() {
    }

    public static BridgeClient get() {
        return INSTANCE;
    }

    public boolean connected() {
        WebSocket ws = socket;
        return ws != null && welcomed && !ws.isInputClosed();
    }

    public String lastError() {
        return lastError;
    }

    public void addListener(Consumer<JsonObject> l) {
        listeners.add(l);
    }

    public void removeListener(Consumer<JsonObject> l) {
        listeners.remove(l);
    }

    /** Connect if needed. Cheap to call every tick; at most one attempt a second. */
    public void ensureConnected() {
        synchronized (lock) {
            if (connected() || connecting) {
                return;
            }
            long now = System.currentTimeMillis();
            if (now - lastAttempt < RETRY_MS) {
                return;
            }
            lastAttempt = now;
            String tok = BridgeLink.token();
            if (tok == null) {
                lastError = "bridge not set up";
                return;
            }
            int gen = ++generation;
            connecting = true;
            welcomed = false;
            WebSocket old = socket;
            socket = null;
            if (old != null) {
                old.abort();
            }
            try {
                http.newWebSocketBuilder().connectTimeout(Duration.ofSeconds(3))
                    .buildAsync(URI.create(BridgeLink.wsUrl()), new Listener(gen))
                    .whenComplete((ws, err) -> opened(gen, ws, err, tok));
            } catch (RuntimeException e) {
                connecting = false;
                lastError = "bridge unreachable";
            }
        }
    }

    private void opened(int gen, WebSocket ws, Throwable err, String tok) {
        synchronized (lock) {
            if (gen != generation) {
                if (ws != null) {
                    ws.abort();
                }
                return;
            }
            connecting = false;
            if (err != null || ws == null) {
                lastError = "bridge unreachable";
                return;
            }
            socket = ws;
        }
        JsonObject hello = new JsonObject();
        hello.addProperty("t", "hello");
        hello.addProperty("token", tok);
        hello.addProperty("role", "ui");
        hello.addProperty("station", "minecraft-ui");
        ws.sendText(GSON.toJson(hello), true);
    }

    public void send(JsonObject msg) {
        WebSocket ws = socket;
        if (ws == null || ws.isOutputClosed()) {
            ensureConnected();
            return;
        }
        ws.sendText(GSON.toJson(msg), true);
    }

    /** Send {@code {"t": type, k1: v1, ...}}; null values are left out. */
    public void send(String type, String... kv) {
        JsonObject o = new JsonObject();
        o.addProperty("t", type);
        for (int i = 0; i + 1 < kv.length; i += 2) {
            if (kv[i + 1] != null) {
                o.addProperty(kv[i], kv[i + 1]);
            }
        }
        send(o);
    }

    public void close() {
        WebSocket ws;
        synchronized (lock) {
            generation++;
            connecting = false;
            welcomed = false;
            ws = socket;
            socket = null;
        }
        if (ws != null) {
            try {
                ws.sendClose(WebSocket.NORMAL_CLOSURE, "bye").whenComplete((w, e) -> ws.abort());
            } catch (RuntimeException e) {
                ws.abort();
            }
        }
    }

    private void dropped(int gen, WebSocket ws, String why) {
        synchronized (lock) {
            if (gen != generation || socket != ws) {
                return;
            }
            socket = null;
            welcomed = false;
            lastError = why;
        }
    }

    /** Frames of one connection attempt. */
    private final class Listener implements WebSocket.Listener {
        private final int gen;
        private final StringBuilder partial = new StringBuilder();

        Listener(int gen) {
            this.gen = gen;
        }

        @Override
        public void onOpen(WebSocket ws) {
            ws.request(1);
        }

        @Override
        public CompletionStage<?> onText(WebSocket ws, CharSequence data, boolean last) {
            partial.append(data);
            if (last) {
                String text = partial.toString();
                partial.setLength(0);
                if (gen == generation) {
                    deliver(text);
                }
            }
            ws.request(1);
            return null;
        }

        @Override
        public CompletionStage<?> onClose(WebSocket ws, int statusCode, String reason) {
            dropped(gen, ws, "bridge closed the connection");
            return null;
        }

        @Override
        public void onError(WebSocket ws, Throwable error) {
            dropped(gen, ws, "bridge connection lost");
        }
    }

    private void deliver(String text) {
        JsonObject msg;
        try {
            msg = JsonParser.parseString(text).getAsJsonObject();
        } catch (RuntimeException e) {
            OmoLife.LOG.debug("bad bridge frame: {}", text);
            return;
        }
        String t = msg.has("t") ? msg.get("t").getAsString() : "";
        if ("welcome".equals(t)) {
            welcomed = true;
            lastError = "";
        } else if ("error".equals(t) && !welcomed) {
            lastError = msg.has("m") ? msg.get("m").getAsString() : "refused";
        }
        Minecraft.getInstance().execute(() -> {
            for (Consumer<JsonObject> l : listeners) {
                try {
                    l.accept(msg);
                } catch (RuntimeException e) {
                    OmoLife.LOG.warn("bridge listener failed", e);
                }
            }
        });
    }
}

package space.omo.life.client;

import java.io.IOException;
import java.io.OutputStream;
import java.lang.reflect.Method;
import java.net.InetSocketAddress;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.util.ArrayList;
import java.util.List;
import java.util.concurrent.CompletableFuture;
import java.util.concurrent.TimeUnit;

import com.google.gson.Gson;
import com.google.gson.JsonArray;
import com.google.gson.JsonObject;
import com.google.gson.JsonParser;
import com.sun.net.httpserver.HttpExchange;
import com.sun.net.httpserver.HttpServer;

import dan200.computercraft.client.gui.AbstractComputerScreen;
import dan200.computercraft.core.terminal.Terminal;

import net.minecraft.client.KeyboardHandler;
import net.minecraft.client.Minecraft;
import net.minecraft.client.MouseHandler;
import net.minecraft.client.Screenshot;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.client.input.MouseButtonInfo;
import net.minecraft.commands.CommandSource;
import net.minecraft.core.BlockPos;
import net.minecraft.core.Direction;
import net.minecraft.core.component.DataComponents;
import net.minecraft.core.registries.BuiltInRegistries;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.level.ServerLevel;
import net.minecraft.server.level.ServerPlayer;
import net.minecraft.tags.BlockTags;
import net.minecraft.tags.FluidTags;
import net.minecraft.world.InteractionHand;
import net.minecraft.world.item.Item;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.TooltipFlag;
import net.minecraft.world.item.component.CustomData;
import net.minecraft.world.level.block.entity.BlockEntity;
import net.minecraft.world.level.block.entity.ComparatorBlockEntity;
import net.minecraft.world.level.block.state.BlockState;
import net.minecraft.world.level.levelgen.Heightmap;
import net.minecraft.world.phys.BlockHitResult;
import net.minecraft.world.phys.Vec3;

import space.omo.life.OmoLife;
import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.block.StatusBlockEntity;
import space.omo.life.client.remote.RemoteWall;
import space.omo.life.client.remote.WallPointer;
import space.omo.life.station.StationPreset;

/**
 * Development-only acceptance-test driver.
 *
 * <p>Compiled only into test builds ({@code OMO_TEST=1 mod/build.sh}) and
 * started only when the JVM runs with {@code -Domo.test.port=N}.
 * It listens on 127.0.0.1:N and executes simple JSON commands on the client
 * thread: send a chat command, right-click a block, type into the open
 * screen, read the open computer's terminal buffer, take a screenshot,
 * sample frame time. This is how the PRD's in-game acceptance tests are run
 * headlessly and reproducibly. It is never active in a normal launch.
 */
public final class TestDriver {

    private static final Gson GSON = new Gson();

    private TestDriver() {
    }

    /** Called reflectively by {@code OmoLifeClient}. */
    public static void start() {
        String port = System.getProperty("omo.test.port");
        if (port == null || port.isBlank()) {
            return;
        }
        try {
            HttpServer server = HttpServer.create(new InetSocketAddress("127.0.0.1", Integer.parseInt(port)), 4);
            server.createContext("/", TestDriver::handle);
            // Daemon threads only: the driver must never keep the JVM alive
            // after the game asks to quit (it did, and tripped the shutdown
            // watchdog).
            server.setExecutor(java.util.concurrent.Executors.newSingleThreadExecutor(r -> {
                Thread t = new Thread(r, "omo-test-driver");
                t.setDaemon(true);
                return t;
            }));
            server.start();
            OmoLife.LOG.warn("TEST DRIVER active on 127.0.0.1:{} (development only)", port);
        } catch (Exception e) {
            OmoLife.LOG.error("test driver failed to start", e);
        }
    }

    private static void handle(HttpExchange ex) throws IOException {
        JsonObject reply;
        try {
            String body = new String(ex.getRequestBody().readAllBytes(), StandardCharsets.UTF_8);
            JsonObject req = body.isBlank() ? new JsonObject() : JsonParser.parseString(body).getAsJsonObject();
            String op = ex.getRequestURI().getPath().replaceFirst("^/", "");
            CompletableFuture<JsonObject> fut = new CompletableFuture<>();
            Minecraft mc = Minecraft.getInstance();
            if ("perf".equals(op)) {
                reply = perf(mc, req.has("seconds") ? req.get("seconds").getAsInt() : 5);
            } else if ("ground".equals(op)) {
                reply = ground(mc, req.getAsJsonArray("points"));
            } else if ("server".equals(op)) {
                reply = serverCommand(mc, req.get("command").getAsString());
            } else {
                mc.execute(() -> {
                    try {
                        fut.complete(run(mc, op, req));
                    } catch (Throwable t) {
                        JsonObject o = new JsonObject();
                        o.addProperty("error", t.toString());
                        fut.complete(o);
                    }
                });
                reply = fut.get(30, TimeUnit.SECONDS);
            }
        } catch (Exception e) {
            reply = new JsonObject();
            reply.addProperty("error", e.toString());
        }
        byte[] out = GSON.toJson(reply).getBytes(StandardCharsets.UTF_8);
        ex.getResponseHeaders().add("Content-Type", "application/json");
        ex.sendResponseHeaders(200, out.length);
        try (OutputStream os = ex.getResponseBody()) {
            os.write(out);
        }
    }

    /**
     * The ground under each [x, z] in the singleplayer overworld, read on the
     * server thread: y of the top block that is not air, leaves, a log or a
     * plant, and whether it is water. World builders lay paths with it.
     */
    private static JsonObject ground(Minecraft mc, JsonArray points) throws Exception {
        ServerLevel level = mc.getSingleplayerServer().overworld();
        JsonArray ys = new JsonArray();
        JsonArray water = new JsonArray();
        mc.getSingleplayerServer().submit(() -> {
            for (var p : points) {
                int x = p.getAsJsonArray().get(0).getAsInt();
                int z = p.getAsJsonArray().get(1).getAsInt();
                BlockPos.MutableBlockPos pos = new BlockPos.MutableBlockPos(x,
                    level.getHeight(Heightmap.Types.MOTION_BLOCKING_NO_LEAVES, x, z) - 1, z);
                BlockState st = level.getBlockState(pos);
                while (pos.getY() > level.getMinY() && (st.isAir() || st.is(BlockTags.LEAVES)
                        || st.is(BlockTags.LOGS) || (st.canBeReplaced() && st.getFluidState().isEmpty()))) {
                    pos.move(Direction.DOWN);
                    st = level.getBlockState(pos);
                }
                ys.add(pos.getY());
                water.add(st.getFluidState().is(FluidTags.WATER));
            }
        }).get(20, TimeUnit.SECONDS);
        JsonObject o = new JsonObject();
        o.add("y", ys);
        o.add("water", water);
        return o;
    }

    /**
     * Run a command as the player on the singleplayer server and return what
     * it answered (successes and failures), so a builder can tell a typo or an
     * unloaded chunk from a placed block.
     */
    private static JsonObject serverCommand(Minecraft mc, String command) throws Exception {
        MinecraftServer server = mc.getSingleplayerServer();
        JsonArray said = new JsonArray();
        CommandSource sink = new CommandSource() {
            @Override
            public void sendSystemMessage(Component message) {
                said.add(message.getString());
            }

            @Override
            public boolean acceptsSuccess() {
                return true;
            }

            @Override
            public boolean acceptsFailure() {
                return true;
            }

            @Override
            public boolean shouldInformAdmins() {
                return false;
            }
        };
        server.submit(() -> {
            ServerPlayer player = server.getPlayerList().getPlayers().get(0);
            server.getCommands().performPrefixedCommand(player.createCommandSourceStack().withSource(sink),
                command.replaceFirst("^/", ""));
        }).get(20, TimeUnit.SECONDS);
        JsonObject o = new JsonObject();
        o.add("output", said);
        return o;
    }

    private static JsonObject perf(Minecraft mc, int seconds) throws InterruptedException {
        List<Long> samples = new ArrayList<>();
        long end = System.currentTimeMillis() + seconds * 1000L;
        while (System.currentTimeMillis() < end) {
            samples.add(mc.getFrameTimeNs());
            Thread.sleep(50);
        }
        samples.sort(Long::compare);
        JsonObject o = new JsonObject();
        o.addProperty("samples", samples.size());
        o.addProperty("fps", mc.getFps());
        o.addProperty("frame_ms_p50", samples.get(samples.size() / 2) / 1e6);
        o.addProperty("frame_ms_p95", samples.get((int) (samples.size() * 0.95)) / 1e6);
        o.addProperty("frame_ms_max", samples.get(samples.size() - 1) / 1e6);
        Runtime rt = Runtime.getRuntime();
        o.addProperty("java_heap_mb", (rt.totalMemory() - rt.freeMemory()) / (1024 * 1024));
        return o;
    }

    private static JsonObject run(Minecraft mc, String op, JsonObject req) throws Exception {
        JsonObject o = new JsonObject();
        switch (op) {
            case "status" -> {
                Screen cur = mc.gui.screen();
                o.addProperty("screen", cur == null ? "" : cur.getClass().getName());
                o.addProperty("in_world", mc.level != null);
                if (mc.player != null) {
                    o.addProperty("x", mc.player.getX());
                    o.addProperty("y", mc.player.getY());
                    o.addProperty("z", mc.player.getZ());
                }
                o.addProperty("fps", mc.getFps());
                o.addProperty("pages", WebPages.count());
                o.addProperty("bridge_ui", BridgeClient.get().connected());
            }
            case "cmd" -> {
                mc.player.connection.sendCommand(req.get("command").getAsString());
                o.addProperty("ok", true);
            }
            case "chat" -> {
                mc.player.connection.sendChat(req.get("text").getAsString());
                o.addProperty("ok", true);
            }
            case "look" -> {
                mc.player.setYRot(req.get("yaw").getAsFloat());
                mc.player.setXRot(req.get("pitch").getAsFloat());
                o.addProperty("ok", true);
            }
            case "use" -> {
                BlockPos pos = pos(req);
                Direction face = Direction.valueOf(req.has("face") ? req.get("face").getAsString().toUpperCase() : "NORTH");
                Vec3 hit = Vec3.atCenterOf(pos).add(face.getStepX() * 0.5, face.getStepY() * 0.5, face.getStepZ() * 0.5);
                var res = mc.gameMode.useItemOn(mc.player, InteractionHand.MAIN_HAND,
                    new BlockHitResult(hit, face, pos, false));
                o.addProperty("result", res.toString());
            }
            case "useitem" -> {                     // right-click the held item, in the air
                InteractionHand hand = req.has("hand") && "off".equals(req.get("hand").getAsString())
                    ? InteractionHand.OFF_HAND : InteractionHand.MAIN_HAND;
                o.addProperty("result", mc.gameMode.useItem(mc.player, hand).toString());
            }
            case "stack" -> {                       // what an inventory slot holds, as the client sees it
                ItemStack st = mc.player.getInventory().getItem(req.get("slot").getAsInt());
                o.addProperty("item", BuiltInRegistries.ITEM.getKey(st.getItem()).toString());
                CustomData data = st.get(DataComponents.CUSTOM_DATA);
                o.addProperty("custom", data == null ? "" : data.copyTag().toString());
                o.addProperty("components", st.getComponentsPatch().toString());
                JsonArray tip = new JsonArray();
                st.getTooltipLines(Item.TooltipContext.of(mc.level), mc.player, TooltipFlag.NORMAL)
                    .forEach(line -> tip.add(line.getString()));
                o.add("tooltip", tip);
            }
            case "window" -> {                      // resize the game window (screenshots at big GUI scales)
                mc.getWindow().setWindowed(req.get("w").getAsInt(), req.get("h").getAsInt());
                o.addProperty("ok", true);
            }
            case "slot" -> {
                mc.player.getInventory().setSelectedSlot(req.get("slot").getAsInt());
                o.addProperty("ok", true);
            }
            case "key" -> {
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                int key = req.get("key").getAsInt();
                int mods = req.has("mods") ? req.get("mods").getAsInt() : 0;
                boolean handled = s.keyPressed(new KeyEvent(key, 0, mods));
                s.keyReleased(new KeyEvent(key, 0, mods));
                o.addProperty("handled", handled);
            }
            case "down", "up" -> {                 // hold / release one key (modifier chords)
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                KeyEvent ev = new KeyEvent(req.get("key").getAsInt(), 0, req.has("mods") ? req.get("mods").getAsInt() : 0);
                o.addProperty("handled", "down".equals(op) ? s.keyPressed(ev) : s.keyReleased(ev));
            }
            case "type" -> {
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                // Like GLFW: key press, then the character, then release. A
                // screen that lets a printable key through (the inventory
                // key "e" closing the GUI) fails here as it would in play.
                String text = req.get("text").getAsString();
                for (int i = 0; i < text.length(); ) {
                    int cp = text.codePointAt(i);
                    Screen cur = mc.gui.screen();
                    if (cur == null) {
                        o.addProperty("closed_at", i);
                        break;
                    }
                    int key = cp == '\n' ? 257 : glfwKey(cp);
                    int mods = Character.isUpperCase(cp) ? 1 : 0;
                    if (key != 0) {
                        cur.keyPressed(new KeyEvent(key, 0, mods));
                    }
                    if (cp != '\n' && mc.gui.screen() == cur) {
                        cur.charTyped(new CharacterEvent(cp));
                    }
                    if (key != 0) {
                        cur.keyReleased(new KeyEvent(key, 0, mods));
                    }
                    i += Character.charCount(cp);
                }
                o.addProperty("ok", mc.gui.screen() != null);
            }
            case "click" -> {
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                double x = req.get("x").getAsDouble(), y = req.get("y").getAsDouble();
                int button = req.has("button") ? req.get("button").getAsInt() : 0;
                s.mouseMoved(x, y);
                boolean h = s.mouseClicked(new MouseButtonEvent(x, y, new MouseButtonInfo(button, 0)), false);
                s.mouseReleased(new MouseButtonEvent(x, y, new MouseButtonInfo(button, 0)));
                o.addProperty("handled", h);
            }
            case "drag" -> {                       // press, drag in steps, release (GUI pixels)
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                double x1 = req.get("x1").getAsDouble(), y1 = req.get("y1").getAsDouble();
                double x2 = req.get("x2").getAsDouble(), y2 = req.get("y2").getAsDouble();
                MouseButtonInfo left = new MouseButtonInfo(0, 0);
                s.mouseMoved(x1, y1);
                s.mouseClicked(new MouseButtonEvent(x1, y1, left), false);
                for (int i = 1; i <= 8; i++) {
                    double x = x1 + (x2 - x1) * i / 8, y = y1 + (y2 - y1) * i / 8;
                    s.mouseDragged(new MouseButtonEvent(x, y, left), (x2 - x1) / 8, (y2 - y1) / 8);
                }
                o.addProperty("handled", s.mouseReleased(new MouseButtonEvent(x2, y2, left)));
            }
            case "term" -> {
                Screen s = mc.gui.screen();
                if (!(s instanceof AbstractComputerScreen<?>)) {
                    o.addProperty("error", "no computer screen open");
                    break;
                }
                Terminal t = ((AbstractComputerScreen<?>) s).getMenu().getTerminal();
                JsonArray lines = new JsonArray();
                for (int y = 0; y < t.getHeight(); y++) {
                    lines.add(t.getLine(y).toString().replaceAll("\\s+$", ""));
                }
                o.add("lines", lines);
                o.addProperty("cols", t.getWidth());
                o.addProperty("rows", t.getHeight());
            }
            case "queue" -> {                      // a CC event on the computer at x,y,z (overworld)
                var server = mc.getSingleplayerServer();
                BlockPos at = pos(req);
                String event = req.get("event").getAsString();
                JsonArray args = req.has("args") ? req.getAsJsonArray("args") : new JsonArray();
                Object[] values = new Object[args.size()];
                for (int i = 0; i < values.length; i++) {
                    var v = args.get(i).getAsJsonPrimitive();
                    double d = v.isNumber() ? v.getAsDouble() : 0;
                    values[i] = v.isBoolean() ? v.getAsBoolean() : !v.isNumber() ? v.getAsString()
                        : d == Math.rint(d) ? (Object) (int) d : d;
                }
                server.execute(() -> {
                    if (server.overworld().getBlockEntity(at) instanceof dan200.computercraft.shared.computer.blocks
                        .ComputerBlockEntity c && c.getServerComputer() != null) {
                        c.getServerComputer().queueEvent(event, values);
                    }
                });
                o.addProperty("ok", true);
            }
            case "panel" -> {
                StationPanel p = mc.gui.screen() == null ? null : StationScreens.forScreen(mc.gui.screen());
                if (p == null) {
                    o.addProperty("error", "no station panel");
                    break;
                }
                o.add("debug", p.debugState());
            }
            case "widgets" -> {
                Screen s = mc.gui.screen();
                JsonArray arr = new JsonArray();
                if (s != null) {
                    for (var child : s.children()) {
                        if (child instanceof net.minecraft.client.gui.components.AbstractWidget w) {
                            JsonObject wj = new JsonObject();
                            wj.addProperty("text", w.getMessage().getString());
                            wj.addProperty("x", w.getX());
                            wj.addProperty("y", w.getY());
                            wj.addProperty("w", w.getWidth());
                            wj.addProperty("h", w.getHeight());
                            wj.addProperty("active", w.active);
                            arr.add(wj);
                        }
                    }
                }
                o.add("widgets", arr);
                o.addProperty("focused", s == null || s.getFocused() == null ? "" : s.getFocused().getClass().getName());
            }
            case "guiscale" -> {
                mc.options.guiScale().set(req.get("scale").getAsInt());
                mc.resizeGui();
                o.addProperty("ok", true);
            }
            case "move" -> {
                Screen s = mc.gui.screen();
                if (s != null) {
                    s.mouseMoved(req.get("x").getAsDouble(), req.get("y").getAsDouble());
                }
                o.addProperty("ok", s != null);
            }
            case "scroll" -> {
                Screen s = mc.gui.screen();
                if (s == null) {
                    o.addProperty("error", "no screen");
                    break;
                }
                o.addProperty("handled", s.mouseScrolled(req.get("x").getAsDouble(), req.get("y").getAsDouble(), 0,
                    req.get("dy").getAsDouble()));
            }
            case "close" -> {
                if (mc.gui.screen() != null) {
                    mc.gui.screen().onClose();
                }
                o.addProperty("ok", true);
            }
            case "be" -> {
                BlockPos pos = pos(req);
                BlockEntity be = mc.level.getBlockEntity(pos);
                o.addProperty("type", be == null ? null : be.getClass().getSimpleName());
                if (be != null) {
                    o.addProperty("block_state", be.getBlockState().toString());
                }
                if (be instanceof StatusBlockEntity status) {    // a Name Plate or Status Lamp, as the client shows it
                    o.addProperty("name", status.name());
                    o.addProperty("glow", status.glow().getSerializedName());
                    o.addProperty("kind", status.kind());
                }
                if (be instanceof StationBlockEntity st) {
                    o.addProperty("workspace", st.workspace());
                    o.addProperty("name", st.workspaceName());
                    o.addProperty("station", st.stationIdOrEmpty());
                    o.addProperty("hermes", st.isHermes());
                    var server = mc.getSingleplayerServer();
                    if (server != null) {                // what the world saves: is a preset still waiting?
                        var saved = server.submit(() -> server.overworld().getBlockEntity(pos) instanceof BlockEntity b
                            ? b.saveWithoutMetadata(server.registryAccess()) : new CompoundTag()).get(5, TimeUnit.SECONDS);
                        o.addProperty("preset", saved.contains(StationPreset.NBT));
                        o.addProperty("preset_tag", saved.getCompoundOrEmpty(StationPreset.NBT).toString());
                    }
                    o.addProperty("locked", st.isLocked());
                    o.addProperty("display_name", st.displayName());
                } else if (be instanceof OmoMonitorBlockEntity m) {
                    o.addProperty("source", m.source());
                    o.addProperty("url", m.url());
                    o.addProperty("zoom", m.zoom());
                    o.addProperty("width", m.getWidth());
                    o.addProperty("height", m.getHeight());
                    o.addProperty("xIndex", m.getXIndex());
                    o.addProperty("yIndex", m.getYIndex());
                    o.addProperty("linked", m.linkedStation() == null ? "" : m.linkedStation().toShortString());
                    var server = mc.getSingleplayerServer();
                    if (server != null) {                // the wall's own terminal as the server holds it
                        o.addProperty("server_term", server.submit(() -> {
                            var sm = server.overworld().getBlockEntity(pos) instanceof OmoMonitorBlockEntity sw
                                ? sw.getCachedServerMonitor() : null;
                            Terminal t = sm == null ? null : sm.getTerminal();
                            return t == null ? "" : t.getWidth() + "x" + t.getHeight() + " " + t.getLine(0);
                        }).get(5, TimeUnit.SECONDS));
                    }
                    var monitor = m.getOriginClientMonitor();
                    Terminal wallTerm = monitor == null ? null : monitor.getTerminal();
                    o.addProperty("client_monitor", monitor != null);
                    if (wallTerm != null) {             // what the wall's terminal shows, as text
                        JsonArray lines = new JsonArray();
                        for (int y = 0; y < wallTerm.getHeight(); y++) {
                            lines.add(wallTerm.getLine(y).toString().replaceAll("\\s+$", ""));
                        }
                        o.add("term_lines", lines);
                        o.addProperty("term_size", wallTerm.getWidth() + "x" + wallTerm.getHeight());
                    }
                    WebPages.Page pg = WebPages.get(pos);
                    o.addProperty("page", pg != null);
                    if (pg != null) {
                        o.addProperty("page_ready", pg.ready());
                        o.addProperty("page_url", pg.currentUrl());
                        o.addProperty("page_w", pg.width());
                        o.addProperty("page_h", pg.height());
                        o.addProperty("page_throttled", pg.throttled());
                        o.addProperty("page_last_visible_ms_ago", System.currentTimeMillis() - pg.lastVisibleFrame());
                    }
                }
            }
            case "redstone" -> {                   // server side: the block, a comparator's output, the power reaching it
                BlockPos pos = pos(req);
                var server = mc.getSingleplayerServer();
                JsonObject r = server.submit(() -> {
                    ServerLevel lvl = server.overworld();
                    JsonObject j = new JsonObject();
                    j.addProperty("block", lvl.getBlockState(pos).toString());
                    j.addProperty("power", lvl.getBestNeighborSignal(pos));
                    if (lvl.getBlockEntity(pos) instanceof ComparatorBlockEntity cmp) {
                        j.addProperty("comparator", cmp.getOutputSignal());
                    }
                    return j;
                }).get(5, TimeUnit.SECONDS);
                r.entrySet().forEach(e -> o.add(e.getKey(), e.getValue()));
            }
            case "recipes" -> {                  // the Omo recipes loaded, and which this player's recipe book has
                MinecraftServer server = mc.getSingleplayerServer();
                JsonArray all = new JsonArray();
                JsonArray known = new JsonArray();
                server.submit(() -> {
                    ServerPlayer p = server.getPlayerList().getPlayer(mc.player.getUUID());
                    for (var r : server.getRecipeManager().getRecipes()) {
                        if (r.id().identifier().getNamespace().equals(OmoLife.MOD_ID)) {
                            all.add(r.id().identifier().getPath());
                            if (p != null && p.getRecipeBook().contains(r.id())) {
                                known.add(r.id().identifier().getPath());
                            }
                        }
                    }
                }).get(5, TimeUnit.SECONDS);
                o.add("all", all);
                o.add("known", known);
            }
            case "pages" -> {
                o.addProperty("count", WebPages.count());
                o.addProperty("runtime", WebPages.runtimeStatus());
            }
            case "screenshot" -> {
                Path out = Path.of(req.get("path").getAsString());
                Files.createDirectories(out.getParent());
                Screenshot.takeScreenshot(mc.gameRenderer.mainRenderTarget(), img -> {
                    try (img) {
                        img.writeToFile(out);
                    } catch (IOException e) {
                        OmoLife.LOG.warn("screenshot failed", e);
                    }
                });
                o.addProperty("path", out.toString());
            }
            case "openworld" -> {
                mc.createWorldOpenFlows().openWorld(req.get("name").getAsString(), () -> { });
                o.addProperty("ok", true);
            }
            case "quit" -> {
                mc.stop();
                o.addProperty("ok", true);
            }
            // -- raw input: through GLFW's own callbacks, so the capture mixins see it as in play
            case "rawkey" -> {                 // action 1 press, 2 repeat, 0 release
                rawKey(mc, req.get("key").getAsInt(), req.has("action") ? req.get("action").getAsInt() : 1,
                    req.has("mods") ? req.get("mods").getAsInt() : 0);
                o.addProperty("ok", true);
            }
            case "rawtype" -> {                // like a keyboard: press, character, release per key
                String text = req.get("text").getAsString();
                Method charTyped = KeyboardHandler.class.getDeclaredMethod("charTyped", long.class, CharacterEvent.class);
                charTyped.setAccessible(true);
                for (int i = 0; i < text.length(); ) {
                    int cp = text.codePointAt(i);
                    int key = cp == '\n' ? 257 : glfwKey(cp);
                    int mods = Character.isUpperCase(cp) ? 1 : 0;
                    if (key != 0) {
                        rawKey(mc, key, 1, mods);
                    }
                    if (cp != '\n') {
                        charTyped.invoke(mc.keyboardHandler, mc.getWindow().handle(), new CharacterEvent(cp));
                    }
                    if (key != 0) {
                        rawKey(mc, key, 0, mods);
                    }
                    i += Character.charCount(cp);
                }
                o.addProperty("ok", true);
            }
            case "rawbutton" -> {              // button 0 left, 1 right; action 1 press, 0 release
                Method onButton = MouseHandler.class.getDeclaredMethod("onButton", long.class, MouseButtonInfo.class,
                    int.class);
                onButton.setAccessible(true);
                onButton.invoke(mc.mouseHandler, mc.getWindow().handle(),
                    new MouseButtonInfo(req.get("button").getAsInt(), 0), req.get("action").getAsInt());
                o.addProperty("ok", true);
            }
            case "rawscroll" -> {
                Method onScroll = MouseHandler.class.getDeclaredMethod("onScroll", long.class, double.class, double.class);
                onScroll.setAccessible(true);
                onScroll.invoke(mc.mouseHandler, mc.getWindow().handle(), 0.0, req.get("dy").getAsDouble());
                o.addProperty("ok", true);
            }
            case "aim" -> {                    // look at a world point from the eye
                Vec3 d = new Vec3(req.get("x").getAsDouble(), req.get("y").getAsDouble(), req.get("z").getAsDouble())
                    .subtract(mc.player.getEyePosition());
                mc.player.setYRot((float) Math.toDegrees(Math.atan2(-d.x, d.z)));
                mc.player.setXRot((float) Math.toDegrees(-Math.atan2(d.y, Math.sqrt(d.x * d.x + d.z * d.z))));
                o.addProperty("ok", true);
            }
            case "sneak" -> {
                mc.options.keyShift.setDown(req.get("on").getAsBoolean());
                o.addProperty("ok", true);
            }
            case "remote" -> {                 // the remote-wall state, and whether the game saw any walking
                RemoteWall.Control c = RemoteWall.active();
                o.addProperty("active", c != null);
                if (c != null) {
                    o.addProperty("origin", c.origin().toShortString());
                    o.addProperty("web", c.web());
                    o.addProperty("label", c.label());
                }
                WallPointer.Hit h = RemoteWall.pointer();
                if (h != null) {
                    o.addProperty("wall", h.wall().getBlockPos().toShortString());
                    o.addProperty("u", h.u());
                    o.addProperty("v", h.v());
                }
                o.addProperty("forward_down", mc.options.keyUp.isDown());
                o.addProperty("screen", mc.gui.screen() == null ? "" : mc.gui.screen().getClass().getName());
            }
            default -> o.addProperty("error", "unknown op " + op);
        }
        return o;
    }

    private static void rawKey(Minecraft mc, int key, int action, int mods) throws ReflectiveOperationException {
        Method keyPress = KeyboardHandler.class.getDeclaredMethod("keyPress", long.class, int.class, KeyEvent.class);
        keyPress.setAccessible(true);
        keyPress.invoke(mc.keyboardHandler, mc.getWindow().handle(), action, new KeyEvent(key, 0, mods));
    }

    /** GLFW key code for a printable character (US layout), 0 if none. */
    private static int glfwKey(int cp) {
        int c = Character.toUpperCase(cp);
        if ((c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == ' ') {
            return c;
        }
        return switch (cp) {
            case '\'', '"' -> 39;
            case ',', '<' -> 44;
            case '-', '_' -> 45;
            case '.', '>' -> 46;
            case '/', '?' -> 47;
            case ';', ':' -> 59;
            case '=', '+' -> 61;
            case '[', '{' -> 91;
            case '\\', '|' -> 92;
            case ']', '}' -> 93;
            case '`', '~' -> 96;
            default -> 0;
        };
    }

    private static BlockPos pos(JsonObject req) {
        return new BlockPos(req.get("x").getAsInt(), req.get("y").getAsInt(), req.get("z").getAsInt());
    }
}

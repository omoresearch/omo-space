package space.omo.life.client;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;

import com.google.gson.JsonArray;
import com.google.gson.JsonElement;
import com.google.gson.JsonObject;

import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.Minecraft;
import net.minecraft.client.gui.ComponentPath;
import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.narration.NarrationElementOutput;
import net.minecraft.client.gui.navigation.FocusNavigationEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.core.BlockPos;
import net.minecraft.core.component.DataComponents;
import net.minecraft.nbt.CompoundTag;
import net.minecraft.network.chat.Component;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.item.component.CustomData;

import space.omo.life.block.StationBlockEntity;
import space.omo.life.client.OmoStyle.Glyph;
import space.omo.life.net.StationPackets;
import space.omo.life.station.BridgeLink;
import space.omo.life.station.StationTarget;

/**
 * The workspace header of a station screen, and the bezel it shares with the
 * terminal below.
 *
 * <p>Attached by {@code StationScreenMixin} to CC:T's own computer screen, so
 * the terminal itself (typing, mouse, resize, upload) stays 100% CC:T. Two
 * tiers, workspace over tabs:
 * <pre>
 *   ✦ Hermes 11 ▾  ◆ default        [Revive shell]  toast        ● Live  ⋯
 *   ● Hermes  ● Shell 2 ✕  +
 *  ┌──────────────────────── terminal ────────────────────────┐
 * </pre>
 * The name opens the workspace switcher (every workspace on the host; search
 * as you type; switch this station, or make a new workspace; switching never
 * stops anything). Tabs are real tabs: the active one opens into the bezel
 * around the terminal it shows. The pill is the screen's connection (click to
 * reconnect); the dots open the rest (rename, profile, Start Hermes, ...).
 *
 * <p>Tab controls go over {@link BridgeClient}'s "ui" connection and the
 * header repaints from the bridge's broadcasts, so two screens on one
 * workspace never disagree; what touches the world (which workspace this
 * station shows, its name) goes to the server as {@link StationPackets}.
 * The panel names its station only through a {@link StationTarget}: it never
 * knows whether that is a block or a pocket.
 *
 * <p>Keyboard (Ctrl+Shift, so readline's Ctrl+W / Ctrl+T reach the shell):
 * O workspaces, T new shell, H new Hermes tab, W close tab (asks: a second
 * press within 5 s closes), R reconnect, N rename workspace, E rename tab,
 * P Hermes profile. Ctrl+Tab / Ctrl+Shift+Tab cycle tabs; Alt+1..8 jump to a
 * tab and Alt+9 to the last, as in a browser.
 */
public final class StationPanel {

    /** Header height above the terminal's bezel, in GUI pixels. */
    public static final int HEIGHT = 38;
    /** CC:T's frame around the terminal; the panel paints over it. */
    private static final int BEZEL = 12;
    private static final int PAD = 6;
    private static final int ROW_H = 14;
    private static final int TAB_H = 15;
    private static final int TAB_NAME_MAX = 84;
    private static final long CONFIRM_MS = 5000L;

    private static final int KEY_TAB = 258;
    private static final int KEY_1 = 49;
    private static final int KEY_9 = 57;

    /** What the screen's display stack says about the station; null when the computer is not a station. */
    public record Binding(StationTarget target, String workspace, String name, boolean hermes, boolean locked) {
        public static Binding from(ItemStack stack) {
            CustomData data = stack.get(DataComponents.CUSTOM_DATA);
            if (data == null) {
                return null;
            }
            CompoundTag tag = data.copyTag();
            String station = tag.getStringOr(StationBlockEntity.NBT_STATION, "");
            if (station.isEmpty()) {
                return null;
            }
            // A block's screen stack carries its position; a pocket's stack is the pocket itself.
            StationTarget target = tag.getInt("X").isPresent()
                ? StationTarget.block(new BlockPos(tag.getIntOr("X", 0), tag.getIntOr("Y", 0), tag.getIntOr("Z", 0)),
                    station)
                : StationTarget.pocket(station);
            return new Binding(target, tag.getStringOr(StationBlockEntity.NBT_WORKSPACE, ""),
                tag.getStringOr(StationBlockEntity.NBT_WS_NAME, ""),
                tag.getBooleanOr(StationBlockEntity.NBT_HERMES, false),
                tag.getBooleanOr(StationBlockEntity.NBT_LOCKED, false));
        }
    }

    private enum Popup { NONE, WORKSPACES, NEW_TAB, MORE, TABS, PROFILE, RENAME_WORKSPACE, RENAME_TAB }

    private final StationTarget target;
    private final Font font;
    private final Consumer<AbstractWidget> addWidget;
    private final Runnable clearWidgets;
    private final Consumer<GuiEventListener> focus;
    private final List<AbstractWidget> mine = new ArrayList<>();

    private String workspace;
    private String workspaceName;
    private boolean hermes;
    private final boolean locked;                   // a map maker's lock: no switching, renaming or re-profiling
    private JsonObject ws;                          // last bridge description of the workspace
    private JsonArray workspaces;                   // every workspace on the host, while the switcher is open
    private String currentTab = "";
    private String screenState = "connecting";      // connecting | connected | offline

    private String toastText = "";
    private int toastColour = OmoStyle.CREAM;
    private long toastUntil;
    private String confirmTab;
    private long confirmUntil;

    private Popup popup = Popup.NONE;
    private Picker picker;
    private String renaming = "";                   // the tab a rename prompt is for

    // geometry: the terminal's rectangle, and the card (header + bezel) around it
    private int termX0;
    private int termY0;
    private int termX1;
    private int termY1;
    private int left;
    private int top;
    private int cardW;
    private int screenW;
    private int screenH;
    private int toastX0;
    private int toastX1;
    private int anchorX;
    private int anchorY;
    private int chipX;

    private int ticks;
    private boolean chimed;
    private String lastWsState = "";
    private final Consumer<JsonObject> listener = this::onBridge;

    public StationPanel(Binding binding, Font font, Consumer<AbstractWidget> addWidget, Runnable clearWidgets,
                        Consumer<GuiEventListener> focus) {
        this.target = binding.target();
        this.font = font;
        this.workspace = binding.workspace();
        this.workspaceName = binding.name();
        this.hermes = binding.hermes();
        this.locked = binding.locked() && !Minecraft.getInstance().player.isCreative();
        this.addWidget = addWidget;
        this.clearWidgets = clearWidgets;
        this.focus = focus;
    }

    public String station() {
        return target.station();
    }

    public void open() {
        BridgeClient.get().addListener(listener);
        BridgeClient.get().ensureConnected();
        if (workspace.isEmpty()) {
            act("ensure", "");                       // the server creates it; its Info reply updates us
        } else {
            requestWorkspace();
        }
    }

    public void close() {
        BridgeClient.get().removeListener(listener);
    }

    /** Lay the panel out around the terminal widget's rectangle, on a screen of the given size. */
    public void layout(int x0, int y0, int x1, int y1, int screenWidth, int screenHeight) {
        termX0 = x0;
        termY0 = y0;
        termX1 = x1;
        termY1 = y1;
        left = x0 - BEZEL;
        cardW = x1 - x0 + 2 * BEZEL;
        top = Math.max(2, y0 - BEZEL - HEIGHT);
        screenW = screenWidth;
        screenH = screenHeight;
        rebuild();
    }

    private int accent() {
        return OmoStyle.accent(hermes);
    }

    private void act(String action, String value) {
        ClientPlayNetworking.send(new StationPackets.Action(target, action, value));
    }

    // -- bridge traffic -------------------------------------------------------------

    private void requestWorkspace() {
        if (!workspace.isEmpty()) {
            BridgeClient.get().send("ws_get", "ws", workspace);
            BridgeClient.get().send("station_status", "station", station());
        }
    }

    private void onBridge(JsonObject msg) {
        switch (msg.get("t").getAsString()) {
            case "welcome" -> requestWorkspace();
            case "workspace" -> {
                JsonObject w = msg.getAsJsonObject("ws");
                if (w != null && w.get("id").getAsString().equals(workspace)) {
                    onWorkspace(w);
                }
            }
            case "workspaces" -> {
                workspaces = msg.getAsJsonArray("items");
                if (popup == Popup.WORKSPACES && picker != null) {
                    picker.rows(workspaceRows());
                }
            }
            case "tab_created" -> {
                steer(msg.get("tab").getAsString());
                rebuild();
            }
            case "station" -> {
                if (msg.get("station").getAsString().equals(station())) {
                    onScreens(msg.getAsJsonArray("screens"));
                }
            }
            case "profiles" -> {
                if (popup == Popup.PROFILE && picker != null) {
                    picker.rows(profileRows(msg.getAsJsonArray("items")));
                }
            }
            case "confirm" -> toast(msg.get("m").getAsString(), 6000, OmoStyle.AMBER);
            case "toast" -> toast(msg.get("m").getAsString(), 4000, OmoStyle.CREAM);
            case "error" -> toast(msg.get("m").getAsString(), 6000, OmoStyle.RED);
            default -> { }
        }
    }

    private void onWorkspace(JsonObject w) {
        boolean same = w.equals(ws);
        ws = w;
        workspaceName = w.get("name").getAsString();
        String state = wsState();
        if ("interrupted".equals(state) && !state.equals(lastWsState)) {
            OmoStyle.lowNote();
        }
        lastWsState = state;
        if (currentTabObj() == null) {
            currentTab = "";
            for (JsonElement el : tabs()) {
                JsonObject t = el.getAsJsonObject();
                if (currentTab.isEmpty() || isLive(t) && !isLive(currentTabObj())) {
                    currentTab = t.get("id").getAsString();
                }
            }
        }
        if (!same) {
            rebuild();
        }
    }

    private void onScreens(JsonArray screens) {
        String before = screenState + "/" + currentTab;
        if (screens == null || screens.isEmpty()) {
            screenState = "connecting";
        } else {
            JsonObject s = screens.get(0).getAsJsonObject();
            screenState = s.get("attached").getAsBoolean() ? "connected" : "offline";
            if (s.has("tab") && !s.get("tab").isJsonNull() && workspace.equals(str(s, "ws"))) {
                currentTab = s.get("tab").getAsString();
            }
        }
        if ("connected".equals(screenState) && !chimed) {
            chimed = true;
            OmoStyle.chime();
        }
        if (!before.equals(screenState + "/" + currentTab)) {
            rebuild();
        }
    }

    /** The server's word on this station's binding (after ensure, switch, create, rename). */
    public void onStationInfo(StationPackets.Info info) {
        boolean changed = !info.workspace().equals(workspace);
        boolean switched = changed && !workspace.isEmpty();
        workspace = info.workspace();
        workspaceName = info.name();
        hermes = info.hermes();
        if (changed) {
            ws = null;
            currentTab = "";
            confirmTab = null;
            screenState = "connecting";
            if (switched && info.message().isEmpty()) {
                toast("Now showing '" + workspaceName + "'. Nothing was stopped.", 4000, OmoStyle.CREAM);
            }
        }
        if (!info.message().isEmpty()) {
            toast(info.message(), 5000, OmoStyle.CREAM);
        }
        if (changed || ws == null) {
            requestWorkspace();
        }
        rebuild();
    }

    private void toast(String m, long ms, int colour) {
        toastText = m;
        toastColour = colour;
        toastUntil = System.currentTimeMillis() + ms;
    }

    private void steer(String tab) {
        BridgeClient.get().send("steer", "station", station(), "ws", workspace, "tab", tab);
        currentTab = tab;
    }

    // -- model helpers ------------------------------------------------------------------

    private static String str(JsonObject o, String key) {
        return o != null && o.has(key) && !o.get(key).isJsonNull() ? o.get(key).getAsString() : "";
    }

    private JsonArray tabs() {
        return ws == null ? new JsonArray() : ws.getAsJsonArray("tabs");
    }

    private JsonObject tab(String id) {
        for (JsonElement el : tabs()) {
            JsonObject t = el.getAsJsonObject();
            if (t.get("id").getAsString().equals(id)) {
                return t;
            }
        }
        return null;
    }

    private JsonObject currentTabObj() {
        return tab(currentTab);
    }

    private static boolean isLive(JsonObject tab) {
        return tab != null && "live".equals(tab.get("state").getAsString());
    }

    /** A program runs in this tab (vim, Hermes, less...): the footer tells how to send it Escape. */
    private boolean programRunning() {
        JsonObject cur = currentTabObj();
        return isLive(cur) && isBusy(cur);
    }

    private static boolean isBusy(JsonObject tab) {
        return tab != null && tab.has("busy") && tab.get("busy").getAsBoolean();
    }

    private static String command(JsonObject tab) {
        return str(tab, "command");
    }

    private static boolean isHermesKind(JsonObject o) {
        return "hermes".equals(str(o, "kind"));
    }

    /** live | ended | interrupted ("" before the first description). */
    private static String stateOf(JsonObject w) {
        if (w == null) {
            return "";
        }
        return w.has("state") ? w.get("state").getAsString() : w.get("alive").getAsBoolean() ? "live" : "interrupted";
    }

    private String wsState() {
        return stateOf(ws);
    }

    private static String profileOf(JsonObject w) {
        String p = str(w, "profile");
        return p.isEmpty() ? "default" : p;
    }

    private String profile() {
        return profileOf(ws);
    }

    /** Hermes ended itself after sitting idle and unwatched; opening the tab resumes it. */
    private static boolean isSleeping(JsonObject tab) {
        return tab != null && tab.has("sleeping") && tab.get("sleeping").getAsBoolean();
    }

    private static int tabDot(JsonObject tab) {
        return switch (tab.get("state").getAsString()) {
            case "live" -> isSleeping(tab) ? OmoStyle.GREY : isBusy(tab) ? OmoStyle.AMBER : OmoStyle.GREEN;
            case "exited" -> OmoStyle.GREY;
            default -> OmoStyle.RED;
        };
    }

    private static int wsDot(JsonObject w) {
        return switch (stateOf(w)) {
            case "live" -> OmoStyle.GREEN;
            case "ended" -> OmoStyle.GREY;
            default -> OmoStyle.RED;
        };
    }

    private static String tabHint(JsonObject tab, int index) {
        String cmd = command(tab);
        String state = switch (tab.get("state").getAsString()) {
            case "live" -> isSleeping(tab) ? "Hermes is asleep to save memory: select it to wake the same conversation."
                : isBusy(tab) ? "Running " + cmd + "."
                : "Idle shell" + (cmd.isEmpty() ? "." : " (" + cmd + ").");
            case "exited" -> "Its process has ended: select it, then Revive.";
            default -> "Interrupted: the session server or host restarted. Nothing is running.";
        };
        String jump = index < 8 ? " Alt+" + (index + 1) + " jumps here." : "";
        return tab.get("name").getAsString() + ": " + state + jump
            + " Double-click renames; the cross or a middle click closes.";
    }

    // -- layout -------------------------------------------------------------------------

    private void add(AbstractWidget w) {
        mine.add(w);
        addWidget.accept(w);
    }

    /** True for the panel's own widgets (header buttons, tabs, popups). */
    public boolean owns(GuiEventListener l) {
        return l instanceof AbstractWidget w && mine.contains(w);
    }

    private void rebuild() {
        clearWidgets.run();
        mine.clear();
        if (cardW <= 0) {
            return;
        }
        add(new Backdrop());
        int accent = accent();
        int x0 = left + PAD;
        int x1 = left + cardW - PAD;
        int y = top + 5;
        boolean tips = popup == Popup.NONE;       // no tooltips from under an open popup

        // Tier 1, right to left: more, connection pill; then left to right: workspace, profile.
        OmoButton more = OmoButton.icon(font, OmoButton.Style.GHOST, accent, Glyph.DOTS, "More", 0, y, ROW_H,
            () -> openPopup(Popup.MORE)).selected(popup == Popup.MORE);
        more.setX(x1 - more.getWidth());
        Status st = status();
        OmoButton pill = OmoButton.of(font, OmoButton.Style.PILL, accent, st.label(), 0, y, ROW_H, this::reconnect)
            .dot(st.colour());
        pill.setX(more.getX() - 5 - pill.getWidth());

        String title = workspaceName.isEmpty() ? (workspace.isEmpty() ? "New workspace" : workspace) : workspaceName;
        OmoButton switcher = OmoButton.of(font, OmoButton.Style.GHOST, accent, OmoStyle.fit(font, title, 150),
                x0 - 2, y, ROW_H, () -> openPopup(Popup.WORKSPACES))
            .glyph(OmoStyle.kind(ws == null ? hermes : isHermesKind(ws)), accent).caret()
            .selected(popup == Popup.WORKSPACES);
        OmoButton chip = OmoButton.of(font, OmoButton.Style.CHIP, accent, "◆ " + profile(), switcher.getRight() + 5,
            y + 1, ROW_H - 2, () -> openPopup(Popup.PROFILE));
        if (tips) {
            more.tip("More: rename, Hermes profile, Start Hermes here, close tab, reconnect");
            pill.tip(st.tip() + " Click to reconnect this screen (Ctrl+Shift+R).");
            switcher.tip("Workspace '" + title + "'. Click (or Ctrl+Shift+O) to see every workspace on this PC, "
                + "switch this station to another one, or start a new one. Switching never stops anything.");
            chip.tip("Hermes profile: new Hermes tabs and resumes in this workspace use it (Ctrl+Shift+P)");
        }
        add(switcher);
        add(chip);
        chipX = chip.getX();

        toastX0 = chip.getRight() + 10;
        toastX1 = pill.getX() - 10;
        JsonObject cur = currentTabObj();
        if (cur != null && !isLive(cur)) {
            // A dead tab's way back is the one thing worth a button up here.
            OmoButton revive = OmoButton.of(font, OmoButton.Style.ACTION, OmoStyle.COPPER, "Revive shell", toastX0, y,
                ROW_H, () -> revive("shell"));
            OmoButton resume = OmoButton.of(font, OmoButton.Style.ACTION, OmoStyle.GOLD, "Resume Hermes",
                revive.getRight() + 4, y, ROW_H, () -> revive("hermes"));
            if (tips) {
                revive.tip("Start a fresh shell in this tab. Nothing from before re-runs.");
                resume.tip("Reopen this tab's saved Hermes conversation");
            }
            if (resume.getRight() < toastX1) {
                add(revive);
                add(resume);
                toastX0 = resume.getRight() + 8;
            }
        }
        add(pill);
        add(more);

        buildTabs(x0, x1, top + HEIGHT - TAB_H, accent, tips);

        if (picker != null) {
            picker.place(anchorX, anchorY, screenW, screenH);
            picker.widgets().forEach(this::add);
            focus.accept(picker.focusTarget());
        }
    }

    /** Tier 2: the tabs, standing on the bezel, then "+". Tabs that do not fit fold into a "+N" menu. */
    private void buildTabs(int x0, int x1, int ty, int accent, boolean tips) {
        JsonArray tabs = tabs();
        if (tabs.isEmpty()) {
            if (ws != null) {
                OmoButton none = OmoButton.of(font, OmoButton.Style.LINK, accent, "No tabs: open one with +", x0,
                    ty + 2, ROW_H - 2, () -> newTab("shell"));
                add(none);
                addPlus(none.getRight() + 6, ty, accent, tips);
            }
            return;
        }
        List<String> names = new ArrayList<>();
        int[] widths = new int[tabs.size()];
        int total = 0;
        for (int i = 0; i < tabs.size(); i++) {
            names.add(OmoStyle.fit(font, tabs.get(i).getAsJsonObject().get("name").getAsString(), TAB_NAME_MAX));
            widths[i] = StationTab.widthFor(font, names.get(i));
            total += widths[i] + 2;
        }
        int room = x1 - x0 - 20;                  // "+" stays
        List<Integer> visible = new ArrayList<>();
        if (total <= room) {
            for (int i = 0; i < tabs.size(); i++) {
                visible.add(i);
            }
        } else {
            room -= font.width("+" + tabs.size()) + 20;
            int used = 0;
            for (int i = 0; i < tabs.size() && used + widths[i] + 2 <= room; i++) {
                visible.add(i);
                used += widths[i] + 2;
            }
            int cur = indexOf(currentTab);
            if (cur >= 0 && !visible.contains(cur)) {
                while (!visible.isEmpty() && used + widths[cur] + 2 > room) {
                    used -= widths[visible.remove(visible.size() - 1)] + 2;
                }
                visible.add(cur);
            }
        }
        int bx = x0;
        for (int i : visible) {
            JsonObject t = tabs.get(i).getAsJsonObject();
            String id = t.get("id").getAsString();
            StationTab tab = new StationTab(font, accent, names.get(i), tabDot(t), id.equals(currentTab),
                id.equals(confirmTab), bx, ty, TAB_H, () -> selectTab(id), () -> closeTab(id),
                () -> beginRenameTab(id));
            if (tips) {
                tab.tip(tabHint(t, i));
            }
            add(tab);
            bx = tab.getRight() + 2;
        }
        int hidden = tabs.size() - visible.size();
        if (hidden > 0) {
            OmoButton rest = OmoButton.of(font, OmoButton.Style.GHOST, accent, "+" + hidden, bx + 2, ty + 1, ROW_H - 1,
                () -> openPopup(Popup.TABS)).caret().selected(popup == Popup.TABS);
            if (tips) {
                rest.tip(hidden + " more tab" + (hidden == 1 ? "" : "s") + ": pick one here, or Ctrl+Tab / Alt+1..9");
            }
            add(rest);
            bx = rest.getRight() + 2;
        }
        addPlus(bx + 2, ty, accent, tips);
    }

    private void addPlus(int x, int ty, int accent, boolean tips) {
        OmoButton plus = OmoButton.icon(font, OmoButton.Style.GHOST, accent, Glyph.PLUS, "New tab", x, ty + 1,
            ROW_H - 1, () -> openPopup(Popup.NEW_TAB)).selected(popup == Popup.NEW_TAB);
        if (tips) {
            plus.tip("New tab: a shell (Ctrl+Shift+T) or Hermes (Ctrl+Shift+H)");
        }
        add(plus);
    }

    private int indexOf(String tabId) {
        JsonArray tabs = tabs();
        for (int i = 0; i < tabs.size(); i++) {
            if (tabs.get(i).getAsJsonObject().get("id").getAsString().equals(tabId)) {
                return i;
            }
        }
        return -1;
    }

    // -- popups ----------------------------------------------------------------------------

    private void openPopup(Popup kind) {
        if (popup == kind) {                      // a second click on the same control closes it
            closePopup();
            return;
        }
        if (workspace.isEmpty() && kind != Popup.MORE) {
            toast("No workspace yet: the bridge has not created one.", 3000, OmoStyle.AMBER);
            return;
        }
        if (locked && (kind == Popup.WORKSPACES || kind == Popup.PROFILE || kind == Popup.RENAME_WORKSPACE)) {
            toast("Locked by the map maker: this station keeps its workspace, name and profile.", 4000,
                OmoStyle.AMBER);
            return;
        }
        int accent = accent();
        int below = top + 5 + ROW_H + 3;
        Runnable close = this::closePopup;
        picker = switch (kind) {
            case WORKSPACES -> {
                BridgeClient.get().send("ws_list");
                anchor(left + PAD - 2, below);
                yield new Picker(font, accent, "Workspaces", 260, "search, or name a new one", close)
                    .pinned(this::createRows)
                    .note("Switching never stops anything; all keep running.");
            }
            case PROFILE -> {
                if (!BridgeClient.get().connected()) {
                    toast("The Omo bridge is not connected; profiles are unavailable.", 4000, OmoStyle.RED);
                    yield null;
                }
                BridgeClient.get().send("profiles");
                anchor(chipX, below);
                yield new Picker(font, accent, "Hermes profile", 170, "filter profiles", close)
                    .empty("no profile matches")
                    .note("For new Hermes tabs; running ones keep theirs.");
            }
            case NEW_TAB -> {
                anchor(plusX(), top + HEIGHT + 1);
                yield new Picker(font, accent, null, 150, null, close).rows(List.of(
                    Picker.Row.kind(false, "Shell", "Ctrl+Shift+T", () -> newTab("shell")),
                    Picker.Row.kind(true, "Hermes", "Ctrl+Shift+H", () -> newTab("hermes"))));
            }
            case TABS -> {
                anchor(plusX() - 60, top + HEIGHT + 1);
                yield new Picker(font, accent, "Tabs", 190, null, close).rows(tabRows());
            }
            case MORE -> {
                anchor(left + cardW - PAD - 190, below);
                yield new Picker(font, accent, null, 190, null, close).rows(moreRows());
            }
            case RENAME_WORKSPACE -> {
                anchor(left + PAD - 2, below);
                yield new Picker(font, accent, "Rename workspace", 220, "workspace name", close)
                    .onSubmit(this::renameWorkspace).rows(List.of()).value(workspaceName)
                    .note("Enter saves · Esc keeps the old name");
            }
            case RENAME_TAB -> {
                JsonObject t = tab(renaming);
                anchor(left + PAD, top + HEIGHT + 1);
                yield t == null ? null : new Picker(font, accent, "Rename tab", 220, "tab name", close)
                    .onSubmit(this::renameTab).rows(List.of()).value(t.get("name").getAsString())
                    .note("Enter saves · Esc keeps the old name");
            }
            case NONE -> null;
        };
        popup = picker == null ? Popup.NONE : kind;
        if (kind == Popup.WORKSPACES && picker != null && workspaces != null) {
            picker.rows(workspaceRows());
        }
        rebuild();
    }

    private void anchor(int x, int y) {
        anchorX = x;
        anchorY = y;
    }

    private int plusX() {
        for (AbstractWidget w : mine) {
            if (w instanceof OmoButton b && "New tab".equals(b.getMessage().getString())) {
                return b.getX();
            }
        }
        return left + PAD;
    }

    private void closePopup() {
        picker = null;
        popup = Popup.NONE;
        rebuild();
        focus.accept(null);
    }

    private List<Picker.Row> workspaceRows() {
        List<JsonObject> items = new ArrayList<>();
        workspaces.forEach(e -> items.add(e.getAsJsonObject()));
        items.sort(Comparator.comparing((JsonObject w) -> !"live".equals(stateOf(w)))
            .thenComparing(w -> str(w, "name").toLowerCase(Locale.ROOT)));
        List<Picker.Row> rows = new ArrayList<>();
        for (JsonObject w : items) {
            String id = str(w, "id");
            String name = str(w, "name");
            int n = w.getAsJsonArray("tabs").size();
            boolean here = id.equals(workspace);
            boolean h = isHermesKind(w);
            String detail = (here ? "here · " : "") + n + " tab" + (n == 1 ? "" : "s") + " · " + profileOf(w);
            rows.add(new Picker.Row(name, detail, OmoStyle.kind(h), OmoStyle.accent(h), wsDot(w), here,
                () -> switchTo(id, name)));
        }
        return rows;
    }

    private List<Picker.Row> createRows(String query) {
        String named = query.isEmpty() ? "" : " '" + query + "'";
        return List.of(
            Picker.Row.kind(true, "New Hermes workspace" + named, "", () -> create("hermes", query)),
            Picker.Row.kind(false, "New terminal workspace" + named, "", () -> create("terminal", query)));
    }

    private List<Picker.Row> profileRows(JsonArray items) {
        String current = profile();
        List<Picker.Row> rows = new ArrayList<>();
        items.forEach(e -> {
            String name = e.getAsString();
            rows.add(new Picker.Row(name, name.equals(current) ? "in use" : "", null, 0, 0, name.equals(current),
                () -> pickProfile(name)));
        });
        return rows;
    }

    private List<Picker.Row> tabRows() {
        List<Picker.Row> rows = new ArrayList<>();
        JsonArray tabs = tabs();
        for (int i = 0; i < tabs.size(); i++) {
            JsonObject t = tabs.get(i).getAsJsonObject();
            String id = t.get("id").getAsString();
            String key = i < 8 ? "Alt+" + (i + 1) : i == tabs.size() - 1 ? "Alt+9" : "";
            rows.add(new Picker.Row(t.get("name").getAsString(), key, null, 0, tabDot(t), id.equals(currentTab),
                () -> selectTab(id)));
        }
        return rows;
    }

    private List<Picker.Row> moreRows() {
        List<Picker.Row> rows = new ArrayList<>();
        rows.add(Picker.Row.of("Workspaces...", "Ctrl+Shift+O", () -> openPopup(Popup.WORKSPACES)));
        rows.add(Picker.Row.of("Rename workspace...", "Ctrl+Shift+N", () -> openPopup(Popup.RENAME_WORKSPACE)));
        JsonObject cur = currentTabObj();
        if (cur != null) {
            rows.add(Picker.Row.of("Rename tab...", "Ctrl+Shift+E", () -> beginRenameTab(currentTab)));
        }
        rows.add(Picker.Row.of("Hermes profile...", "Ctrl+Shift+P", () -> openPopup(Popup.PROFILE)));
        if (cur != null && isLive(cur) && !isBusy(cur) && !"hermes".equals(command(cur))) {
            rows.add(Picker.Row.kind(true, "Start Hermes here", "", () ->
                BridgeClient.get().send("hermes", "ws", workspace, "tab", currentTab)));
        }
        if (cur != null && !isLive(cur)) {
            rows.add(Picker.Row.kind(false, "Revive shell", "", () -> revive("shell")));
            rows.add(Picker.Row.kind(true, "Resume Hermes", "", () -> revive("hermes")));
        }
        if (cur != null) {
            rows.add(Picker.Row.of("Close tab", "Ctrl+Shift+W", () -> closeTab(currentTab)));
        }
        rows.add(Picker.Row.of("Reconnect screen", "Ctrl+Shift+R", this::reconnect));
        return rows;
    }

    // -- actions -------------------------------------------------------------------------

    /**
     * Point the screen at a tab. An exited or interrupted tab is shown too:
     * the screen says so honestly and the header offers Revive / Resume.
     */
    private void selectTab(String id) {
        if (!id.equals(currentTab)) {
            steer(id);
            rebuild();
        }
        focus.accept(null);
    }

    private void newTab(String kind) {
        if (workspace.isEmpty()) {
            toast("No workspace yet: the bridge has not created one.", 3000, OmoStyle.AMBER);
            return;
        }
        BridgeClient.get().send("tab_new", "ws", workspace, "kind", kind);
    }

    private void revive(String mode) {
        BridgeClient.get().send("tab_revive", "ws", workspace, "tab", currentTab, "mode", mode);
    }

    /** Always asks: the first press arms, a second within 5 s closes. */
    private void closeTab(String id) {
        JsonObject t = tab(id);
        if (t == null) {
            return;
        }
        long now = System.currentTimeMillis();
        if (id.equals(confirmTab) && now < confirmUntil) {
            JsonObject o = new JsonObject();
            o.addProperty("t", "tab_close");
            o.addProperty("ws", workspace);
            o.addProperty("tab", id);
            o.addProperty("force", true);
            BridgeClient.get().send(o);
            confirmTab = null;
            if (id.equals(currentTab)) {
                currentTab = "";
            }
            toast("Closing tab...", 2000, OmoStyle.CREAM);
        } else {
            confirmTab = id;
            confirmUntil = now + CONFIRM_MS;
            String what = isLive(t) && isBusy(t) ? " It runs " + command(t) + "." : "";
            toast("Close '" + t.get("name").getAsString() + "'?" + what + " Click its cross again.", CONFIRM_MS,
                OmoStyle.AMBER);
        }
        rebuild();
    }

    private void reconnect() {
        BridgeClient.get().ensureConnected();
        if (workspace.isEmpty()) {
            act("ensure", "");
        } else {
            steer(currentTab);
            requestWorkspace();
        }
        toast("Reconnecting screen...", 2000, OmoStyle.CREAM);
    }

    private void switchTo(String id, String name) {
        if (id.equals(workspace)) {
            toast("This station already shows '" + name + "'.", 2500, OmoStyle.CREAM);
            return;
        }
        act("switch", id);
        toast("Switching to '" + name + "'... nothing is stopped.", 4000, OmoStyle.CREAM);
    }

    private void create(String kind, String name) {
        act("create", kind + ":" + name);
        toast("Starting a new " + ("hermes".equals(kind) ? "Hermes" : "terminal") + " workspace...", 4000,
            OmoStyle.CREAM);
    }

    private void beginRenameTab(String id) {
        renaming = id;
        popup = Popup.NONE;
        openPopup(Popup.RENAME_TAB);
    }

    private void renameWorkspace(String name) {
        if (!name.isEmpty()) {
            act("rename", name);
        }
    }

    private void renameTab(String name) {
        if (!name.isEmpty()) {
            BridgeClient.get().send("tab_rename", "ws", workspace, "tab", renaming, "name", name);
        }
    }

    private void pickProfile(String name) {
        BridgeClient.get().send("ws_profile", "ws", workspace, "profile", "default".equals(name) ? "" : name);
        toast("Hermes profile '" + name + "': new Hermes tabs and resumes use it. Running tabs are untouched.",
            5000, OmoStyle.CREAM);
    }

    private void cycle(int dir) {
        JsonArray tabs = tabs();
        if (tabs.isEmpty()) {
            return;
        }
        int idx = Math.max(0, indexOf(currentTab));
        OmoStyle.click();
        selectTab(tabs.get(Math.floorMod(idx + dir, tabs.size())).getAsJsonObject().get("id").getAsString());
    }

    /** Alt+1..8: that tab; Alt+9: the last one. */
    private void jump(int n) {
        JsonArray tabs = tabs();
        int idx = n == 9 ? tabs.size() - 1 : n - 1;
        if (idx >= 0 && idx < tabs.size()) {
            OmoStyle.click();
            selectTab(tabs.get(idx).getAsJsonObject().get("id").getAsString());
        }
    }

    // -- input (routed here first by StationScreenMixin) --------------------------------

    /** Key handling; true when consumed (the terminal never sees it). */
    public boolean keyPressed(KeyEvent event) {
        int key = event.key();
        boolean ctrl = event.hasControlDown();
        boolean shift = event.hasShiftDown();
        if (picker != null) {
            if (ctrl && shift && key == 79 && popup == Popup.WORKSPACES) {
                closePopup();                      // Ctrl+Shift+O again: close the switcher
                return true;
            }
            return picker.keyPressed(event);
        }
        if (ctrl && key == KEY_TAB) {
            cycle(shift ? -1 : 1);
            return true;
        }
        if (event.hasAltDown() && !ctrl && key >= KEY_1 && key <= KEY_9) {
            jump(key - KEY_1 + 1);
            return true;
        }
        if (!ctrl || !shift) {
            return false;
        }
        switch (key) {
            case 79 -> openPopup(Popup.WORKSPACES);    // O
            case 84 -> newTab("shell");                // T
            case 72 -> newTab("hermes");               // H
            case 87 -> closeTab(currentTab);           // W
            case 82 -> reconnect();                    // R
            case 78 -> openPopup(Popup.RENAME_WORKSPACE); // N
            case 69 -> beginRenameTab(currentTab);     // E
            case 80 -> openPopup(Popup.PROFILE);       // P
            default -> {
                return false;
            }
        }
        return true;
    }

    /** What should have keyboard focus after a header click; null = the terminal. */
    public GuiEventListener focusAfterClick() {
        return picker == null ? null : picker.focusTarget();
    }

    /** Mouse clicks before CC:T sees them: an open popup is modal. */
    public boolean mouseClicked(MouseButtonEvent event) {
        return picker != null && picker.mouseClicked(event);
    }

    public boolean mouseScrolled(double mx, double my, double dy) {
        return picker != null && picker.mouseScrolled(mx, my, dy);
    }

    /** Once per client tick while the screen is open. */
    public void tick() {
        ticks++;
        BridgeClient bridge = BridgeClient.get();
        if (!bridge.connected()) {
            bridge.ensureConnected();
        } else if (ticks % ("connected".equals(screenState) ? 40 : 10) == 0) {
            // Poll while open: tab states change on the host (a shell exits,
            // Hermes starts) without any click here; quicker until the screen is live.
            requestWorkspace();
            if (popup == Popup.WORKSPACES) {
                bridge.send("ws_list");
            }
        }
        if (confirmTab != null && System.currentTimeMillis() > confirmUntil) {
            confirmTab = null;
            rebuild();
        }
    }

    // -- drawing -----------------------------------------------------------------------------

    private record Status(int colour, String label, String tip) {
    }

    private Status status() {
        if (!BridgeLink.available()) {
            return new Status(OmoStyle.RED, "Offline", "The Omo bridge is not set up on this computer.");
        }
        BridgeClient bridge = BridgeClient.get();
        if (!bridge.connected()) {
            String why = bridge.lastError();
            return new Status(OmoStyle.AMBER, "Reconnecting",
                "Reconnecting to the Omo bridge" + (why.isEmpty() ? "." : " (" + why + ")."));
        }
        if (workspace.isEmpty()) {
            return new Status(OmoStyle.AMBER, "Creating", "Creating this station's workspace.");
        }
        switch (wsState()) {
            case "interrupted" -> {
                return new Status(OmoStyle.RED, "Interrupted",
                    "Interrupted: the session server or host restarted. Nothing is running; revive a tab.");
            }
            case "ended" -> {
                return new Status(OmoStyle.AMBER, "Ended", "Every tab has exited. Revive one to continue.");
            }
            default -> { }
        }
        return switch (screenState) {
            case "connected" -> new Status(OmoStyle.GREEN, "Live", "Live: keys go straight to the host.");
            case "offline" -> new Status(OmoStyle.AMBER, "Detached", "This screen is not attached to its tab.");
            default -> new Status(OmoStyle.AMBER, "Connecting", "Connecting this screen to its workspace.");
        };
    }

    /**
     * The card behind everything: the header strip, the bezel around the
     * terminal (painted over CC:T's own frame, so header, tabs and terminal
     * read as one object), the tab baseline, the toast and the key hints.
     * Added first so every control draws on top; never focusable or clickable.
     */
    private final class Backdrop extends AbstractWidget {
        Backdrop() {
            super(left, top, cardW, HEIGHT, Component.empty());
        }

        @Override
        protected void extractWidgetRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
            int accent = accent();
            int x0 = left, x1 = left + cardW, y0 = top;
            int yf = top + HEIGHT;                                  // where the header meets the bezel
            int yb = termY1 + BEZEL;
            g.fill(x0 + 2, yb, x1 + 2, yb + 2, 0x66000000);        // shadow, outside the card only
            g.fill(x1, y0 + 2, x1 + 2, yb, 0x66000000);
            g.fill(x0, y0, x1, yf, OmoStyle.ESPRESSO_DEEP);
            int bezel = StationTab.FILL;
            g.fill(x0, yf, x1, termY0, bezel);
            g.fill(x0, termY1, x1, yb, bezel);
            g.fill(x0, termY0, termX0, termY1, bezel);
            g.fill(termX1, termY0, x1, termY1, bezel);
            OmoStyle.frame(g, termX0 - 1, termY0 - 1, termX1 + 1, termY1 + 1, OmoStyle.alpha(accent, 0x38));
            OmoStyle.frame(g, x0, y0, x1, yb, OmoStyle.alpha(accent, 0x90));
            g.fill(x0 + 1, y0 + 1, x1 - 1, y0 + 2, accent);
            g.fill(x0 + 1, yf - 1, x1 - 1, yf, OmoStyle.alpha(accent, 0x70));

            if (System.currentTimeMillis() < toastUntil && !toastText.isEmpty() && toastX1 - toastX0 > 30) {
                int ty = top + 5 + 3;
                OmoStyle.dot(g, toastX0, ty + 1, toastColour);
                int colour = toastColour == OmoStyle.CREAM ? OmoStyle.CREAM_BRIGHT : toastColour;
                g.text(font, OmoStyle.fit(font, toastText, toastX1 - toastX0 - 9), toastX0 + 9, ty, colour, false);
            }
            String hints = programRunning()
                ? "Ctrl+[ sends Esc  ·  Esc closes  ·  Ctrl+Shift+O workspaces"
                : "Ctrl+Shift+O workspaces  ·  Alt+1-9 tabs  ·  Ctrl+Shift+T new shell";
            if (font.width(hints) < cardW - 2 * PAD) {
                g.text(font, hints, x1 - PAD - font.width(hints), termY1 + 2, OmoStyle.FAINT, false);
            }
        }

        @Override
        public boolean isMouseOver(double mx, double my) {
            return false;
        }

        @Override
        public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
            return false;
        }

        @Override
        public ComponentPath nextFocusPath(FocusNavigationEvent event) {
            return null;
        }

        @Override
        protected void updateWidgetNarration(NarrationElementOutput out) {
        }
    }

    /** For the test driver and bug reports: what the panel currently believes. */
    public JsonObject debugState() {
        JsonObject o = new JsonObject();
        o.addProperty("target", target.block().isPresent() ? "block" : "pocket");
        o.addProperty("workspace", workspace);
        o.addProperty("name", workspaceName);
        o.addProperty("station", station());
        o.addProperty("currentTab", currentTab);
        o.addProperty("screenState", screenState);
        o.addProperty("mode", popup.name());
        o.addProperty("profile", profile());
        o.addProperty("bridgeConnected", BridgeClient.get().connected());
        o.addProperty("message", toastText);
        if (ws != null) {
            o.add("ws", ws);
        }
        if (workspaces != null) {
            o.add("workspaces", workspaces);
        }
        return o;
    }
}

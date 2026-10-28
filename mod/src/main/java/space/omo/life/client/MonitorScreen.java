package space.omo.life.client;

import java.util.function.Consumer;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import com.mojang.blaze3d.textures.GpuTextureView;

import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.CharacterEvent;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.core.BlockPos;
import net.minecraft.network.chat.Component;

import space.omo.life.block.OmoMonitorBlockEntity;
import space.omo.life.block.StationBlockEntity;
import space.omo.life.net.LinkPackets;
import space.omo.life.net.OmoNetworking;

/**
 * The monitor's controls: choose a source, pin a page, zoom, and interact.
 *
 * <p>Opened with Shift+right-click on the wall (a plain right-click takes
 * control of the wall from where you stand instead; see {@code RemoteWall}).
 *
 * <p>Two modes. <b>Settings</b> is a small card: source, address, zoom.
 * <b>Interact</b> fills the window with the same page the wall shows, so
 * clicking, scrolling and typing go to the page; the page is sized to the
 * window while it is open and goes back to the wall's size afterwards. The
 * page lives in {@link WebPages}, keyed by the wall's origin panel; this
 * screen is only a window onto it, so closing it changes nothing.
 */
public final class MonitorScreen extends Screen {

    private static final int BAR_H = 20;
    /** The tab strip under the address bar while interacting. */
    private static final int TAB_H = 14;
    /** Where the page starts while interacting. */
    private static final int TOP = BAR_H + TAB_H;
    private static final int CARD_W = 320;
    private static final int CARD_H = 128;
    /** The settings card of a web wall has one more row: Sound. */
    private static final int WEB_CARD_H = 152;
    private static final int ROW_H = 16;

    private final BlockPos origin;
    private final int cols;
    private final int rows;
    private String source;
    private String url;
    private int zoom;
    private boolean interact;
    private EditBox urlBox;
    private int lastW = -1;
    private int lastH = -1;
    private String note = "";
    private int cardX;
    private int cardY;
    private int zoomCentre;
    private boolean dirty;
    private OmoButton interactButton;
    /** The address the bar last showed for the active tab, to follow navigation. */
    private String shownUrl;
    private OmoButton soundButton;

    public MonitorScreen(BlockPos origin, String source, String url, int zoom, int cols, int rows) {
        super(Component.literal("Omo Monitor"));
        this.origin = origin;
        this.source = source;
        this.url = url;
        this.zoom = zoom <= 0 ? 100 : zoom;
        this.cols = cols;
        this.rows = rows;
    }

    private int cardH() {
        return web() ? WEB_CARD_H : CARD_H;
    }

    private boolean web() {
        return OmoMonitorBlockEntity.SOURCE_WEB.equals(source);
    }

    /** The station this terminal wall mirrors (as the client last heard), or null. */
    private BlockPos linkedTo() {
        return !web() && minecraft.level.getBlockEntity(origin) instanceof OmoMonitorBlockEntity wall
            ? wall.linkedStation() : null;
    }

    private WebPages.Page page() {
        return WebPages.get(origin);
    }

    private WebView view() {
        WebPages.Page p = page();
        return p == null ? null : p.view();
    }

    @Override
    protected void init() {
        clearWidgets();
        if (interact) {
            initInteract();
        } else {
            initSettings();
        }
    }

    private OmoButton button(OmoButton.Style style, String label, int x, int y, int h, Runnable action) {
        return addRenderableWidget(OmoButton.of(font, style, OmoStyle.GOLD, label, x, y, h, action));
    }

    private void initSettings() {
        cardX = (width - CARD_W) / 2;
        cardY = (height - cardH()) / 2;
        int x = cardX + 10;
        int y = cardY + 26;

        interactButton = null;
        soundButton = null;
        OmoButton term = button(OmoButton.Style.TAB, "Terminal", x + 52, y, ROW_H, () -> {
            source = OmoMonitorBlockEntity.SOURCE_TERMINAL;
            apply();
            dirty = true;
        }).selected(!web()).tip("Show the terminal of the computer next to this monitor (a normal CC:T monitor).");
        button(OmoButton.Style.TAB, "Web page", term.getRight() + 3, y, ROW_H, () -> {
            source = OmoMonitorBlockEntity.SOURCE_WEB;
            dirty = true;
        }).selected(web()).tip("Pin a web page to this monitor. It keeps running while the game is open.");

        if (web()) {
            int ay = y + ROW_H + 8;
            urlBox = new EditBox(font, x + 56, ay + 4, CARD_W - 20 - 60, 10, Component.literal("Address"));
            urlBox.setBordered(false);
            urlBox.setMaxLength(2000);
            urlBox.setTextColor(OmoStyle.CREAM_BRIGHT);
            urlBox.setValue(url == null ? "" : url);
            urlBox.setHint(Component.literal("https://example.com or localhost:3000").withColor(OmoStyle.FAINT));
            addRenderableWidget(urlBox);
            setInitialFocus(urlBox);

            int by = ay + ROW_H + 8;
            OmoButton pin = button(OmoButton.Style.ACTION, "Pin page", x + 52, by, ROW_H, this::pinTyped)
                .tip("Show this address on the monitor (Enter)");
            OmoButton minus = button(OmoButton.Style.ACTION, "-", pin.getRight() + 16, by, ROW_H, () -> zoomBy(-25))
                .tip("Zoom out");
            minus.setWidth(16);
            OmoButton plus = button(OmoButton.Style.ACTION, "+", minus.getRight() + 38, by, ROW_H, () -> zoomBy(25))
                .tip("Zoom in");
            plus.setWidth(16);
            zoomCentre = (minus.getRight() + plus.getX()) / 2;
            interactButton = button(OmoButton.Style.ACTION, "Interact", plus.getRight() + 16, by, ROW_H, () -> {
                interact = true;
                dirty = true;
            }).tip("Click, scroll and type in the page (Esc returns)");
            interactButton.active = page() != null && page().ready();
            soundControls(x + 52, by + ROW_H + 8, ROW_H);
        } else if (linkedTo() != null) {
            button(OmoButton.Style.ACTION, "Unlink", x + 52, y + ROW_H + 40, ROW_H, () -> {
                ClientPlayNetworking.send(new LinkPackets.Unlink(origin));
                note = "Unlinked";
            }).tip("Stop mirroring the station; a station next to the wall can use it again.");
        }
        OmoButton done = button(OmoButton.Style.ACTION, "Done", 0, cardY + cardH() - ROW_H - 8, ROW_H, this::onClose);
        done.setX(cardX + CARD_W - 10 - done.getWidth());
    }

    private void initInteract() {
        interactButton = null;
        soundButton = null;
        int h = BAR_H - 6;
        int y = 3;
        OmoButton back = button(OmoButton.Style.ACTION, "‹", 4, y, h, () -> act(WebView::back)).tip("Back");
        back.setWidth(16);
        OmoButton fwd = button(OmoButton.Style.ACTION, "›", back.getRight() + 2, y, h, () -> act(WebView::forward))
            .tip("Forward");
        fwd.setWidth(16);
        OmoButton reload = button(OmoButton.Style.ACTION, "↻", fwd.getRight() + 2, y, h, () -> act(WebView::reload))
            .tip("Reload");
        reload.setWidth(16);

        OmoButton done = button(OmoButton.Style.ACTION, "Back", 0, y, h, () -> {
            interact = false;
            dirty = true;
        }).tip("Back to the monitor settings (Esc)");
        done.setX(width - 4 - done.getWidth());
        OmoButton pin = button(OmoButton.Style.ACTION, "Pin this", 0, y, h, this::pinCurrent)
            .tip("Keep this page on the monitor");
        pin.setX(done.getX() - 3 - pin.getWidth());
        OmoButton go = button(OmoButton.Style.ACTION, "Go", 0, y, h, this::navigate).tip("Open the address (Enter)");
        go.setX(pin.getX() - 3 - go.getWidth());

        int bx = soundControls(reload.getRight() + 6, y, h) + 8;
        urlBox = new EditBox(font, bx + 4, y + 3, go.getX() - bx - 14, 10, Component.literal("Address"));
        urlBox.setBordered(false);
        urlBox.setMaxLength(2000);
        urlBox.setTextColor(OmoStyle.CREAM_BRIGHT);
        WebPages.Page p = page();
        shownUrl = p == null ? url : p.currentUrl();
        urlBox.setValue(barText(shownUrl));
        addRenderableWidget(urlBox);
        if (p != null) {
            p.setInteractive(true);
        }
        lastW = lastH = -1;
    }

    private void act(Consumer<WebView> action) {
        WebView v = view();
        if (v != null) {
            action.accept(v);
        }
    }

    // -- sound ------------------------------------------------------------------------------

    private static final String[] SOUND_LABELS = {"♪ 100%", "Muted"};

    /** [−] [♪ 80%] [+] at x; returns the right edge. Click the middle to mute, scroll on it to change. */
    private int soundControls(int x, int y, int h) {
        OmoButton minus = button(OmoButton.Style.ACTION, "−", x, y, h, () -> nudgeVolume(-10)).tip("Quieter");
        minus.setWidth(16);
        int w = 0;
        for (String l : SOUND_LABELS) {
            w = Math.max(w, font.width(l) + 10);
        }
        soundButton = button(OmoButton.Style.ACTION, SOUND_LABELS[0], minus.getRight() + 2, y, h, this::toggleMute)
            .tip("This wall's sound: click to mute, scroll to change (Ctrl+M mutes). "
                + "Also follows Minecraft's Jukebox/Note Blocks volume, and fades with distance.");
        soundButton.setWidth(w);
        OmoButton plus = button(OmoButton.Style.ACTION, "+", soundButton.getRight() + 2, y, h, () -> nudgeVolume(10))
            .tip("Louder");
        plus.setWidth(16);
        refreshSound();
        return plus.getRight();
    }

    private void refreshSound() {
        if (soundButton == null) {
            return;
        }
        WebPages.Page p = page();
        soundButton.active = p != null;
        String label = p == null ? "♪ –" : p.muted() || p.volume() == 0 ? "Muted" : "♪ " + p.volume() + "%";
        if (!label.equals(soundButton.getMessage().getString())) {
            soundButton.setMessage(Component.literal(label));
        }
        soundButton.selected(p != null && (p.muted() || p.volume() == 0));
    }

    private void nudgeVolume(int delta) {
        WebPages.Page p = page();
        if (p != null) {
            int base = p.muted() ? 0 : p.volume();
            p.setVolume(Math.round((base + delta) / 10f) * 10);
            refreshSound();
        }
    }

    private void toggleMute() {
        WebPages.Page p = page();
        if (p != null) {
            if (p.volume() == 0) {
                p.setVolume(50);                // unmuting a wall turned all the way down
            } else {
                p.toggleMute();
            }
            refreshSound();
        }
    }

    private static String barText(String u) {
        return u == null || u.startsWith("about:") ? "" : u;
    }

    // -- tabs -------------------------------------------------------------------------------

    private int tabWidth(int n) {
        return Math.max(48, Math.min(170, (width - 8 - 20) / Math.max(1, n)));
    }

    private int tabX(int i, int n) {
        return 4 + i * tabWidth(n);
    }

    /** Show tab {@code i}: the bar follows it and typing goes to the page. */
    private void showTab(WebPages.Page p, int i) {
        p.selectTab(i);
        lastW = lastH = -1;
        followTab(p);
        setFocused(null);
        if (p.view() != null) {
            p.view().focus(true);
        }
    }

    private void followTab(WebPages.Page p) {
        shownUrl = p.currentUrl();
        if (urlBox != null) {
            urlBox.setValue(barText(shownUrl));
        }
    }

    private void newTab() {
        WebPages.Page p = page();
        if (p == null || p.tabCount() == 0) {
            return;
        }
        if (p.tabCount() >= WebPages.MAX_TABS) {
            return;
        }
        p.openTab("about:blank");
        lastW = lastH = -1;
        shownUrl = p.currentUrl();
        if (urlBox != null) {
            urlBox.setValue("");
            setFocused(urlBox);                 // like a browser: a new tab starts in the address bar
        }
    }

    private void closeTab(int i) {
        WebPages.Page p = page();
        if (p != null && p.tabCount() > 1) {
            p.closeTab(i);
            lastW = lastH = -1;
            followTab(p);
        }
    }

    /** A click in the tab strip: switch, close (× or middle button), or new (+). */
    private boolean clickTabs(MouseButtonEvent event) {
        WebPages.Page p = page();
        if (p == null || p.tabCount() == 0) {
            return true;
        }
        int n = p.tabCount();
        int x = (int) event.x();
        for (int i = 0; i < n; i++) {
            int x0 = tabX(i, n);
            int x1 = x0 + tabWidth(n) - 2;
            if (x >= x0 && x < x1) {
                if (event.button() == 2 || (n > 1 && x >= x1 - 11)) {
                    closeTab(i);
                } else {
                    showTab(p, i);
                }
                OmoStyle.click();
                return true;
            }
        }
        int px = tabX(n, n);
        if (n < WebPages.MAX_TABS && x >= px && x < px + 16) {
            newTab();
            OmoStyle.click();
        }
        return true;
    }

    private void extractTabs(GuiGraphicsExtractor g, int mouseX, int mouseY) {
        g.fill(0, BAR_H, width, TOP, OmoStyle.ESPRESSO_DEEP);
        g.fill(0, TOP - 1, width, TOP, OmoStyle.GOLD);
        WebPages.Page p = page();
        if (p == null || p.tabCount() == 0) {
            return;
        }
        int n = p.tabCount();
        int tw = tabWidth(n);
        int y0 = BAR_H + 1;
        int y1 = TOP - 1;
        for (int i = 0; i < n; i++) {
            int x0 = tabX(i, n);
            int x1 = x0 + tw - 2;
            boolean on = i == p.activeTab();
            boolean hover = mouseY >= y0 && mouseY < y1 && mouseX >= x0 && mouseX < x1;
            int bg = on ? OmoStyle.mix(OmoStyle.ESPRESSO, OmoStyle.GOLD, 0.25F) : hover ? OmoStyle.ESPRESSO_HOVER : OmoStyle.ESPRESSO;
            g.fill(x0, y0, x1, y1, bg);
            if (on) {
                g.fill(x0, y0, x1, y0 + 1, OmoStyle.GOLD);
            }
            int textRoom = tw - 8 - (n > 1 ? 12 : 0);
            g.text(font, OmoStyle.fit(font, p.tabLabel(i), textRoom), x0 + 4, y0 + 3,
                on ? OmoStyle.CREAM_BRIGHT : OmoStyle.MUTED, false);
            if (n > 1) {
                boolean overX = hover && mouseX >= x1 - 11;
                g.text(font, "×", x1 - 9, y0 + 3, overX ? OmoStyle.RED : OmoStyle.FAINT, false);
            }
        }
        if (n < WebPages.MAX_TABS) {
            int px = tabX(n, n);
            boolean hover = mouseY >= y0 && mouseY < y1 && mouseX >= px && mouseX < px + 16;
            g.fill(px, y0, px + 16, y1, hover ? OmoStyle.ESPRESSO_HOVER : OmoStyle.ESPRESSO);
            g.centeredText(font, "+", px + 8, y0 + 3, hover ? OmoStyle.GOLD_LIGHT : OmoStyle.GOLD);
        }
    }

    private void zoomBy(int delta) {
        zoom = Math.max(OmoMonitorBlockEntity.ZOOM_MIN, Math.min(OmoMonitorBlockEntity.ZOOM_MAX, zoom + delta));
        apply();
    }

    private void pinTyped() {
        String typed = urlBox.getValue().trim();
        String safe = OmoMonitorBlockEntity.safeUrl(typed);
        url = safe == null ? typed : safe;
        apply();
        if (safe != null) {
            urlBox.setValue(safe);
            note = "Pinned " + OmoMonitorBlockEntity.host(safe);
        }
    }

    private void pinCurrent() {
        WebPages.Page pg = page();
        if (pg != null) {
            url = pg.currentUrl();
            apply();
            note = "Pinned " + OmoMonitorBlockEntity.host(url);
        }
    }

    /** Send the binding to the server and open / close the page right away. */
    private void apply() {
        ClientPlayNetworking.send(new OmoNetworking.SetMonitor(origin, source, url == null ? "" : url, zoom));
        String safe = web() ? OmoMonitorBlockEntity.safeUrl(url) : null;
        if (safe != null) {
            WebPages.open(origin, safe);
        } else if (!web()) {
            WebPages.close(origin);
        } else if (url != null && !url.isBlank()) {
            note = "That address is not allowed on a monitor.";
        }
    }

    private void navigate() {
        WebPages.Page p = page();
        String u = urlBox == null ? null : OmoMonitorBlockEntity.safeUrl(urlBox.getValue());
        if (p != null && u != null) {
            p.navigate(u);
            setFocused(null);
            if (p.view() != null) {
                p.view().focus(true);
            }
        }
    }

    private String title() {
        String t = "Omo Monitor · " + cols + "×" + rows;
        if (web() && url != null && !url.isBlank()) {
            t += " · " + OmoMonitorBlockEntity.host(url);
        }
        return t;
    }

    // -- render -----------------------------------------------------------------------------

    @Override
    public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
        if (interact) {
            extractInteract(g, mouseX, mouseY, partial);
            return;
        }
        OmoStyle.card(g, cardX, cardY, cardX + CARD_W, cardY + cardH(), OmoStyle.GOLD);
        g.fill(cardX + 1, cardY + 1, cardX + CARD_W - 1, cardY + 2, OmoStyle.GOLD);
        g.text(font, OmoStyle.fit(font, title(), CARD_W - 80), cardX + 10, cardY + 9, OmoStyle.CREAM_BRIGHT, false);
        String pill = WebPages.count() + "/" + WebPages.MAX_PAGES + " pages";
        int pillColour = WebPages.count() >= WebPages.MAX_PAGES ? OmoStyle.AMBER : OmoStyle.GOLD;
        int pw = font.width(pill) + 10;
        int px = cardX + CARD_W - 10 - pw;
        g.fill(px, cardY + 6, px + pw, cardY + 18, OmoStyle.mix(OmoStyle.ESPRESSO, pillColour, 0.22F));
        OmoStyle.frame(g, px, cardY + 6, px + pw, cardY + 18, OmoStyle.alpha(pillColour, 0x90));
        g.text(font, pill, px + 5, cardY + 8, OmoStyle.mix(OmoStyle.CREAM_BRIGHT, pillColour, 0.6F), false);

        int x = cardX + 10;
        int y = cardY + 26;
        g.text(font, "Source", x, y + 4, OmoStyle.MUTED, false);
        if (web()) {
            int ay = y + ROW_H + 8;
            g.text(font, "Address", x, ay + 4, OmoStyle.MUTED, false);
            if (urlBox != null) {
                int bx0 = urlBox.getX() - 4, bx1 = urlBox.getRight() + 2;
                g.fill(bx0, ay, bx1, ay + ROW_H, OmoStyle.ESPRESSO);
                OmoStyle.frame(g, bx0, ay, bx1, ay + ROW_H, OmoStyle.alpha(OmoStyle.GOLD, urlBox.isFocused() ? 0xF0 : 0x80));
            }
            int by = ay + ROW_H + 8;
            g.text(font, "Page", x, by + 4, OmoStyle.MUTED, false);
            g.text(font, "Sound", x, by + ROW_H + 12, OmoStyle.MUTED, false);
            g.centeredText(font, zoom + "%", zoomCentre, by + 4, OmoStyle.CREAM);
        } else if (linkedTo() != null) {
            BlockPos at = linkedTo();
            String name = minecraft.level.getBlockEntity(at) instanceof StationBlockEntity st
                && !st.displayName().isEmpty() ? st.displayName() : "the station at " + at.toShortString();
            g.text(font, "Linked", x, y + ROW_H + 12, OmoStyle.MUTED, false);
            g.text(font, OmoStyle.fit(font, name, CARD_W - 72), x + 52, y + ROW_H + 12, OmoStyle.GOLD_LIGHT, false);
            g.text(font, OmoStyle.fit(font, "mirrors its screen from " + (int) Math.sqrt(at.distSqr(origin))
                + " blocks away", CARD_W - 72), x + 52, y + ROW_H + 24, OmoStyle.MUTED, false);
        } else {
            g.textWithWordWrap(font, Component.literal(
                "This wall is a CC:T monitor: the station next to it can show a terminal tab on it, or link "
                    + "it to any station with the Omo Linker. Choose Web page to pin a site instead."),
                x, y + ROW_H + 10, CARD_W - 20, OmoStyle.MUTED, false);
        }
        g.text(font, web() ? "Tip: right-click the wall to use its page from afar."
            : "Tip: right-click the wall to type into it from afar.", x, cardY + cardH() - 35, OmoStyle.FAINT, false);
        String line = !note.isEmpty() ? note : WebPages.runtimeStatus();
        if (!line.isEmpty()) {
            int colour = note.isEmpty() ? OmoStyle.AMBER : OmoStyle.GREEN;
            OmoStyle.dot(g, x, cardY + cardH() - 19, colour);
            g.text(font, OmoStyle.fit(font, line, CARD_W - 90), x + 9, cardY + cardH() - 20, colour, false);
        }
        super.extractRenderState(g, mouseX, mouseY, partial);
    }

    private void extractInteract(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
        g.fill(0, 0, width, BAR_H, OmoStyle.ESPRESSO_DEEP);
        g.fill(0, BAR_H - 1, width, BAR_H, OmoStyle.GOLD);
        if (urlBox != null) {
            int bx0 = urlBox.getX() - 4, bx1 = urlBox.getRight() + 4;
            g.fill(bx0, 3, bx1, BAR_H - 3, OmoStyle.ESPRESSO);
            OmoStyle.frame(g, bx0, 3, bx1, BAR_H - 3, OmoStyle.alpha(OmoStyle.GOLD, urlBox.isFocused() ? 0xF0 : 0x70));
        }
        extractTabs(g, mouseX, mouseY);
        super.extractRenderState(g, mouseX, mouseY, partial);
        WebPages.Page p = page();
        String wait = p == null ? "No page pinned" : p.view() == null ? p.status() : null;
        if (wait != null) {
            g.centeredText(font, wait, width / 2, height / 2, OmoStyle.CREAM);
            return;
        }
        p.markVisible();
        // The page is sized to the window in real pixels while interacting, so it is sharp.
        int scale = Math.max(1, minecraft.getWindow().getGuiScale());
        int pw = width * scale;
        int ph = (height - TOP) * scale;
        if (pw != lastW || ph != lastH) {
            p.resize(pw, ph);
            lastW = pw;
            lastH = ph;
        }
        GpuTextureView tex = p.view().texture();
        if (tex == null) {
            g.centeredText(font, "Loading...", width / 2, height / 2, OmoStyle.MUTED);
            return;
        }
        g.blit(tex, RenderSystem.getSamplerCache().getClampToEdge(FilterMode.LINEAR),
            0, TOP, width, height, 0.0F, 1.0F, 0.0F, 1.0F);              // x0, y0, x1, y1
    }

    /** Rebuilds requested by a button happen here, after the click that asked for them is over. */
    @Override
    public void tick() {
        if (dirty) {
            dirty = false;
            rebuildWidgets();
        }
        if (interactButton != null) {
            WebPages.Page p = page();
            interactButton.active = p != null && p.ready();
        }
        refreshSound();
        if (interact && urlBox != null && getFocused() != urlBox) {
            WebPages.Page p = page();
            if (p != null && p.tabCount() > 0 && !p.currentUrl().equals(shownUrl)) {
                followTab(p);                   // links, redirects, back/forward
            }
        }
    }

    // -- input ------------------------------------------------------------------------------

    private boolean inPage(double y) {
        return interact && y >= TOP && view() != null;
    }

    private int px(double v) {
        return (int) (v * Math.max(1, minecraft.getWindow().getGuiScale()));
    }

    private int py(double y) {
        return (int) ((y - TOP) * Math.max(1, minecraft.getWindow().getGuiScale()));
    }

    /** The same click in page pixels: MCEF takes the position from the event itself. */
    private MouseButtonEvent pageEvent(MouseButtonEvent event) {
        return new MouseButtonEvent(px(event.x()), py(event.y()), event.buttonInfo());
    }

    @Override
    public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (super.mouseClicked(event, doubleClick)) {
            if (getFocused() instanceof OmoButton) {
                setFocused(interact ? null : urlBox);    // typing goes to the address, never to a stale button
            }
            return true;
        }
        if (interact && event.y() >= BAR_H && event.y() < TOP) {
            return clickTabs(event);
        }
        if (inPage(event.y())) {
            WebView v = view();
            setFocused(null);
            v.focus(true);
            v.mouseMoved(px(event.x()), py(event.y()));
            v.mouseClicked(pageEvent(event), doubleClick);
            return true;
        }
        return false;
    }

    @Override
    public boolean mouseReleased(MouseButtonEvent event) {
        if (inPage(event.y())) {
            view().mouseReleased(pageEvent(event));
            return true;
        }
        return super.mouseReleased(event);
    }

    @Override
    public void mouseMoved(double x, double y) {
        if (inPage(y)) {
            view().mouseMoved(px(x), py(y));
        }
        super.mouseMoved(x, y);
    }

    @Override
    public boolean mouseScrolled(double x, double y, double dx, double dy) {
        if (soundButton != null && soundButton.isMouseOver(x, y) && dy != 0) {
            nudgeVolume(dy > 0 ? 10 : -10);
            return true;
        }
        if (inPage(y)) {
            view().mouseScrolled(px(x), py(y), dy);
            return true;
        }
        return super.mouseScrolled(x, y, dx, dy);
    }

    @Override
    public boolean keyPressed(KeyEvent event) {
        int key = event.key();
        if (interact && event.hasControlDown() && tabKey(event)) {
            return true;
        }
        if (urlBox != null && getFocused() == urlBox) {
            if (key == 257 || key == 335) {
                if (interact) {
                    navigate();
                } else {
                    pinTyped();
                }
                return true;
            }
            if (key == 256) {
                setFocused(null);
                return true;
            }
            return urlBox.keyPressed(event) || urlBox.canConsumeInput() || super.keyPressed(event);
        }
        if (key == 256) {
            if (interact) {
                interact = false;
                rebuildWidgets();
            } else {
                onClose();
            }
            return true;
        }
        if (interact && key == 76 && event.hasControlDown()) {      // Ctrl+L: address bar
            setFocused(urlBox);
            return true;
        }
        if (interact && view() != null) {
            view().keyPressed(event);
            return true;
        }
        return super.keyPressed(event);
    }

    /** Ctrl+T new tab, Ctrl+W close tab, Ctrl+(Shift+)Tab next/previous. */
    private boolean tabKey(KeyEvent event) {
        WebPages.Page p = page();
        if (p == null || p.tabCount() == 0) {
            return false;
        }
        int n = p.tabCount();
        switch (event.key()) {
            case 84 -> newTab();                                                     // T
            case 87 -> closeTab(p.activeTab());                                      // W
            case 258 -> showTab(p, (p.activeTab() + (event.hasShiftDown() ? n - 1 : 1)) % n);  // Tab
            case 77 -> toggleMute();                                                 // M
            default -> {
                return false;
            }
        }
        return true;
    }

    @Override
    public boolean keyReleased(KeyEvent event) {
        if (interact && getFocused() != urlBox && view() != null) {
            view().keyReleased(event);
            return true;
        }
        return super.keyReleased(event);
    }

    @Override
    public boolean charTyped(CharacterEvent event) {
        if (getFocused() == urlBox) {
            return super.charTyped(event);
        }
        if (interact && view() != null) {
            view().charTyped(event);
            return true;
        }
        return super.charTyped(event);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }

    @Override
    public void removed() {
        WebPages.Page p = page();
        if (p != null) {
            p.setInteractive(false);
            if (p.view() != null) {
                p.view().focus(false);
            }
        }
    }

    /**
     * The address bar or a button taking the keyboard from the page: the page
     * gets key-up for keys still held, whose real key-up now goes elsewhere.
     */
    @Override
    public void setFocused(GuiEventListener focused) {
        super.setFocused(focused);
        if (interact && focused != null) {
            act(v -> v.focus(false));
        }
    }

    @Override
    public void rebuildWidgets() {
        if (!interact) {
            WebPages.Page p = page();
            if (p != null) {
                p.setInteractive(false);
                if (p.view() != null) {
                    p.view().focus(false);      // back to settings: no key stays held in the page
                }
            }
        }
        super.rebuildWidgets();
    }
}

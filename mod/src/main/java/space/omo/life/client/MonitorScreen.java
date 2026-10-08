package space.omo.life.client;

import java.util.function.Consumer;

import com.mojang.blaze3d.systems.RenderSystem;
import com.mojang.blaze3d.textures.FilterMode;
import com.mojang.blaze3d.textures.GpuTextureView;

import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.EditBox;
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
    private static final int CARD_W = 320;
    private static final int CARD_H = 128;
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

    public MonitorScreen(BlockPos origin, String source, String url, int zoom, int cols, int rows) {
        super(Component.literal("Omo Monitor"));
        this.origin = origin;
        this.source = source;
        this.url = url;
        this.zoom = zoom <= 0 ? 100 : zoom;
        this.cols = cols;
        this.rows = rows;
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
        cardY = (height - CARD_H) / 2;
        int x = cardX + 10;
        int y = cardY + 26;

        interactButton = null;
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
        } else if (linkedTo() != null) {
            button(OmoButton.Style.ACTION, "Unlink", x + 52, y + ROW_H + 40, ROW_H, () -> {
                ClientPlayNetworking.send(new LinkPackets.Unlink(origin));
                note = "Unlinked";
            }).tip("Stop mirroring the station; a station next to the wall can use it again.");
        }
        OmoButton done = button(OmoButton.Style.ACTION, "Done", 0, cardY + CARD_H - ROW_H - 8, ROW_H, this::onClose);
        done.setX(cardX + CARD_W - 10 - done.getWidth());
    }

    private void initInteract() {
        interactButton = null;
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

        int bx = reload.getRight() + 8;
        urlBox = new EditBox(font, bx + 4, y + 3, go.getX() - bx - 14, 10, Component.literal("Address"));
        urlBox.setBordered(false);
        urlBox.setMaxLength(2000);
        urlBox.setTextColor(OmoStyle.CREAM_BRIGHT);
        WebPages.Page p = page();
        urlBox.setValue(p == null ? url : p.currentUrl());
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
        OmoStyle.card(g, cardX, cardY, cardX + CARD_W, cardY + CARD_H, OmoStyle.GOLD);
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
            : "Tip: right-click the wall to type into it from afar.", x, cardY + CARD_H - 35, OmoStyle.FAINT, false);
        String line = !note.isEmpty() ? note : WebPages.runtimeStatus();
        if (!line.isEmpty()) {
            int colour = note.isEmpty() ? OmoStyle.AMBER : OmoStyle.GREEN;
            OmoStyle.dot(g, x, cardY + CARD_H - 19, colour);
            g.text(font, OmoStyle.fit(font, line, CARD_W - 90), x + 9, cardY + CARD_H - 20, colour, false);
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
        int ph = (height - BAR_H) * scale;
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
            0, BAR_H, width, height, 0.0F, 1.0F, 0.0F, 1.0F);              // x0, y0, x1, y1
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
    }

    // -- input ------------------------------------------------------------------------------

    private boolean inPage(double y) {
        return interact && y >= BAR_H && view() != null;
    }

    private int px(double v) {
        return (int) (v * Math.max(1, minecraft.getWindow().getGuiScale()));
    }

    private int py(double y) {
        return (int) ((y - BAR_H) * Math.max(1, minecraft.getWindow().getGuiScale()));
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
        if (inPage(y)) {
            view().mouseScrolled(px(x), py(y), dy);
            return true;
        }
        return super.mouseScrolled(x, y, dx, dy);
    }

    @Override
    public boolean keyPressed(KeyEvent event) {
        int key = event.key();
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

    @Override
    public void rebuildWidgets() {
        if (!interact) {
            WebPages.Page p = page();
            if (p != null) {
                p.setInteractive(false);
            }
        }
        super.rebuildWidgets();
    }
}

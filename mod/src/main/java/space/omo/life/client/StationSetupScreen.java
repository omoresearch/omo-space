package space.omo.life.client;

import java.util.ArrayList;
import java.util.List;

import net.fabricmc.fabric.api.client.networking.v1.ClientPlayNetworking;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;

import space.omo.life.net.LinkPackets;
import space.omo.life.net.LinkPackets.Setup;

/**
 * The Station Setup: what a station is, set in game. Name, kind (Hermes or
 * Terminal), Hermes profile (the header's profile picker), the tabs a fresh
 * workspace starts with, and a lock for map makers (players can use the
 * station, but outside creative mode not rename, rebind or re-profile it).
 *
 * <p>Opened with Shift+right-click on a station, with an empty hand or the
 * Omo Linker. The server saves it as the station's preset (what a copy of
 * the map on another PC is built from) and, when the station already has a
 * workspace, renames and re-profiles that at once ({@code StationSetup}).
 */
public final class StationSetupScreen extends Screen {

    private static final int CARD_W = 316;
    private static final int BASE_H = 172;          // grows when the tab chips wrap
    private static final int ROW_H = 16;
    private static final int LABEL_W = 56;
    private static final int MAX_TABS = 8;

    private final LinkPackets.OpenSetup open;
    private String name;
    private boolean hermes;
    private String profile;
    private final List<String> tabs;
    private boolean locked;

    private int cardX;
    private int cardY;
    private EditBox nameBox;
    private EditBox tabBox;
    private Picker picker;
    private boolean dirty;
    private int lockY;                               // below the tab chips, which may wrap
    private int cardH = BASE_H;

    public StationSetupScreen(LinkPackets.OpenSetup open) {
        super(Component.literal("Station Setup"));
        this.open = open;
        Setup s = open.setup();
        this.name = s.name();
        this.hermes = s.hermes();
        this.profile = s.profile().isEmpty() ? "default" : s.profile();
        this.tabs = new ArrayList<>(s.tabs());
        this.locked = s.locked();
    }

    private int accent() {
        return OmoStyle.accent(hermes);
    }

    private boolean bound() {
        return !open.workspace().isEmpty();
    }

    private OmoButton button(OmoButton.Style style, String label, int x, int y, Runnable action) {
        return addRenderableWidget(OmoButton.of(font, style, accent(), label, x, y, ROW_H, () -> {
            action.run();
            dirty = true;
        }));
    }

    private int row(int i) {
        return cardY + 34 + i * (ROW_H + 6);
    }

    @Override
    protected void init() {
        cardX = (width - CARD_W) / 2;
        cardY = (height - BASE_H) / 2;
        int x = cardX + 10 + LABEL_W;

        nameBox = box(x + 4, row(0), CARD_W - 28 - LABEL_W, "Name", bound() ? "workspace name" : "named by the bridge");
        nameBox.setMaxLength(40);
        nameBox.setValue(name);
        nameBox.setResponder(v -> name = v);
        addRenderableWidget(nameBox);

        OmoButton h = button(OmoButton.Style.TAB, "Hermes", x, row(1), () -> hermes = true)
            .glyph(OmoStyle.Glyph.SPARK, OmoStyle.GOLD).selected(hermes)
            .tip("The first tab starts a Hermes conversation; gold accent.");
        button(OmoButton.Style.TAB, "Terminal", h.getRight() + 3, row(1), () -> hermes = false)
            .glyph(OmoStyle.Glyph.PROMPT, OmoStyle.COPPER).selected(!hermes)
            .tip("A plain persistent shell; copper accent.");

        OmoButton chip = button(OmoButton.Style.CHIP, "◆ " + profile, x, row(2), this::openPicker).caret()
            .tip("The Hermes profile its Hermes tabs run as.");
        chip.active = !open.profiles().isEmpty();

        int tx = x;
        int ty = row(3);
        for (int i = 0; i < tabs.size(); i++) {
            int idx = i;
            String t = tabs.get(i);
            boolean th = t.startsWith("hermes:");
            OmoButton b = OmoButton.of(font, OmoButton.Style.TAB, accent(), t.substring(t.indexOf(':') + 1) + "  ✕",
                0, 0, ROW_H, () -> {
                    tabs.remove(idx);
                    dirty = true;
                }).glyph(OmoStyle.kind(th), OmoStyle.accent(th)).tip("Click to remove this tab");
            if (tx + b.getWidth() > cardX + CARD_W - 10) {
                tx = x;
                ty += ROW_H + 3;
            }
            b.setPosition(tx, ty);
            addRenderableWidget(b);
            tx = b.getRight() + 3;
        }
        if (tabs.size() < MAX_TABS) {
            if (tx + 210 > cardX + CARD_W - 10) {         // the name box and both add buttons stay together
                tx = x;
                ty += ROW_H + 3;
            }
            tabBox = box(tx + 4, ty, 58, "Tab name", "tab name");
            tabBox.setMaxLength(24);
            addRenderableWidget(tabBox);
            OmoButton shell = button(OmoButton.Style.ACTION, "Shell tab", tabBox.getRight() + 5, ty,
                () -> addTab(false)).glyph(OmoStyle.Glyph.PLUS, OmoStyle.CREAM).tip("Add a shell tab (Enter)");
            button(OmoButton.Style.ACTION, "Hermes tab", shell.getRight() + 3, ty, () -> addTab(true))
                .glyph(OmoStyle.Glyph.PLUS, OmoStyle.CREAM).tip("Add a Hermes tab");
        } else {
            tabBox = null;
        }
        lockY = ty + ROW_H + 6;
        cardH = Math.max(BASE_H, lockY + ROW_H + 34 - cardY);

        button(OmoButton.Style.TAB, locked ? "Locked" : "Unlocked", x, lockY, () -> locked = !locked)
            .selected(locked).tip("Locked: players can use this station, but outside creative mode cannot "
                + "rename it, switch its workspace or change its profile.");

        OmoButton save = button(OmoButton.Style.ACTION, "Save", 0, cardY + cardH - ROW_H - 8, this::save)
            .tip("Save (Enter)");
        save.setX(cardX + CARD_W - 10 - save.getWidth());
        OmoButton cancel = button(OmoButton.Style.GHOST, "Cancel", 0, save.getY(), this::onClose);
        cancel.setX(save.getX() - 4 - cancel.getWidth());

        if (picker != null) {
            picker.place(chip.getX(), chip.getBottom() + 2, width, height);
            picker.widgets().forEach(this::addRenderableWidget);
            setFocused(picker.focusTarget());
        } else {
            setInitialFocus(nameBox);
        }
    }

    private EditBox box(int x, int y, int w, String narration, String hint) {
        EditBox b = new EditBox(font, x, y + 4, w, 10, Component.literal(narration));
        b.setBordered(false);
        b.setTextColor(OmoStyle.CREAM_BRIGHT);
        b.setHint(Component.literal(hint).withColor(OmoStyle.FAINT));
        return b;
    }

    private void addTab(boolean h) {
        String typed = tabBox == null ? "" : tabBox.getValue().strip();
        String base = typed.isEmpty() ? (h ? "Hermes" : "Shell") : typed;
        String n = base;
        for (int i = 2; exists(n); i++) {
            n = base + " " + i;
        }
        tabs.add((h ? "hermes:" : "shell:") + n);
    }

    private boolean exists(String tabName) {
        return tabs.stream().anyMatch(t -> t.substring(t.indexOf(':') + 1).equals(tabName));
    }

    private void openPicker() {
        List<Picker.Row> rows = new ArrayList<>();
        for (String p : open.profiles()) {
            rows.add(new Picker.Row(p, p.equals(profile) ? "chosen" : "", null, 0, 0, p.equals(profile), () -> {
                profile = p;
                dirty = true;
            }));
        }
        picker = new Picker(font, accent(), "Hermes profile", 170, "filter profiles", () -> {
            picker = null;
            dirty = true;
        }).rows(rows).empty("no profile matches").note("For its Hermes tabs.");
    }

    private void save() {
        ClientPlayNetworking.send(new LinkPackets.SaveSetup(open.pos(), open.station(),
            new Setup(name.strip(), hermes, "default".equals(profile) ? "" : profile, List.copyOf(tabs), locked)));
        onClose();
    }

    // -- render -----------------------------------------------------------------------------

    @Override
    public void extractRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
        int a = accent();
        int x0 = cardX, y0 = cardY, x1 = cardX + CARD_W, y1 = cardY + cardH;
        OmoStyle.card(g, x0, y0, x1, y1, a);
        g.fill(x0 + 1, y0 + 1, x1 - 1, y0 + 2, a);
        OmoStyle.glyph(g, OmoStyle.kind(hermes), x0 + 10, y0 + 8, a);
        g.text(font, "Station Setup", x0 + 22, y0 + 8, OmoStyle.CREAM_BRIGHT, false);
        String where = open.pos().toShortString();
        g.text(font, where, x1 - 10 - font.width(where), y0 + 8, OmoStyle.FAINT, false);
        g.text(font, OmoStyle.fit(font, bound() ? "Workspace " + open.workspace() + " · saving renames it now"
            : "No workspace yet · it is made from this setup", CARD_W - 20), x0 + 10, y0 + 20, OmoStyle.MUTED, false);

        int lx = x0 + 10;
        String[] labels = {"Name", "Kind", "Profile", "Tabs"};
        for (int i = 0; i < labels.length; i++) {
            g.text(font, labels[i], lx, row(i) + 4, OmoStyle.MUTED, false);
        }
        g.text(font, "Lock", lx, lockY + 4, OmoStyle.MUTED, false);
        well(g, nameBox, a);
        well(g, tabBox, a);
        g.text(font, OmoStyle.fit(font, locked ? "use only; creative mode edits"
            : "players may rename and rebind", CARD_W - LABEL_W - 90),
            lx + LABEL_W + 70, lockY + 4, OmoStyle.FAINT, false);
        String note = !open.note().isEmpty() ? open.note()
            : bound() ? "Tabs: for a fresh workspace (map copies)." : "";
        if (!note.isEmpty()) {
            g.text(font, OmoStyle.fit(font, note, CARD_W - 110), lx, y1 - 20, open.note().isEmpty()
                ? OmoStyle.FAINT : OmoStyle.AMBER, false);
        }
        super.extractRenderState(g, mouseX, mouseY, partial);
    }

    /** The dark well behind a borderless text box. */
    private void well(GuiGraphicsExtractor g, EditBox b, int a) {
        if (b != null) {
            int bx0 = b.getX() - 4, by0 = b.getY() - 4, bx1 = b.getRight() + 2, by1 = by0 + ROW_H;
            g.fill(bx0, by0, bx1, by1, OmoStyle.ESPRESSO);
            OmoStyle.frame(g, bx0, by0, bx1, by1, OmoStyle.alpha(a, b.isFocused() ? 0xF0 : 0x80));
        }
    }

    /** Rebuilds requested by a button happen here, after the click that asked for them is over. */
    @Override
    public void tick() {
        if (dirty) {
            dirty = false;
            rebuildWidgets();
        }
    }

    // -- input ------------------------------------------------------------------------------

    @Override
    public boolean keyPressed(KeyEvent event) {
        if (picker != null) {
            return picker.keyPressed(event);
        }
        int key = event.key();
        if ((key == 257 || key == 335) && getFocused() == tabBox && tabBox != null && !tabBox.getValue().isBlank()) {
            addTab(false);
            dirty = true;
            return true;
        }
        if (key == 257 || key == 335) {
            save();
            return true;
        }
        if (getFocused() instanceof EditBox b && key != 256) {
            return b.keyPressed(event) || b.canConsumeInput() || super.keyPressed(event);
        }
        return super.keyPressed(event);
    }

    @Override
    public boolean mouseClicked(MouseButtonEvent event, boolean doubleClick) {
        if (picker != null) {
            return picker.mouseClicked(event);
        }
        return super.mouseClicked(event, doubleClick);
    }

    @Override
    public boolean mouseScrolled(double x, double y, double dx, double dy) {
        if (picker != null) {
            return picker.mouseScrolled(x, y, dy);
        }
        return super.mouseScrolled(x, y, dx, dy);
    }

    @Override
    public boolean isPauseScreen() {
        return false;
    }
}

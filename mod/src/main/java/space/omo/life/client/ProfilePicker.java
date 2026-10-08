package space.omo.life.client;

import java.util.ArrayList;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.narration.NarratedElementType;
import net.minecraft.client.gui.narration.NarrationElementOutput;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;

/**
 * The Hermes profile picker: a filter box over a scrollable list, drawn as a
 * small card over the terminal. Keyboard first: type to filter, Up/Down (or
 * PageUp/PageDown/Home/End) to move, Enter to pick, Esc to cancel. Click a
 * row to pick it; click outside to cancel; the wheel scrolls.
 *
 * <p>Two widgets: {@link #surface} (card, list, hints) and {@link #filter}
 * (a real EditBox on top of it). The station panel routes keys and mouse
 * events here first while the picker is open, so the terminal underneath
 * never sees them.
 */
final class ProfilePicker {

    static final int WIDTH = 164;
    private static final int ROW = 11;
    private static final int ROWS = 9;
    private static final int LIST_TOP = 30;
    private static final int HEIGHT = LIST_TOP + ROWS * ROW + 14;

    private final Font font;
    private final int accent;
    private final String current;
    private final Consumer<String> onPick;
    private final Runnable onCancel;

    private List<String> all;                 // null while loading
    private List<String> shown = List.of();
    private int selected;
    private int scroll;
    private int x;
    private int y;

    final EditBox filter;
    final Surface surface;

    ProfilePicker(Font font, int accent, String current, Consumer<String> onPick, Runnable onCancel) {
        this.font = font;
        this.accent = accent;
        this.current = current == null || current.isEmpty() ? "default" : current;
        this.onPick = onPick;
        this.onCancel = onCancel;
        this.filter = new EditBox(font, 0, 0, WIDTH - 16, 10, Component.literal("Filter profiles"));
        filter.setBordered(false);
        filter.setMaxLength(64);
        filter.setTextColor(OmoStyle.CREAM_BRIGHT);
        filter.setHint(Component.literal("type to filter").withColor(OmoStyle.FAINT));
        filter.setResponder(s -> refilter());
        this.surface = new Surface();
    }

    /** Place the card with its top-right corner at (right, top). */
    void place(int right, int top, int screenHeight) {
        x = right - WIDTH;
        y = Math.max(2, Math.min(top, screenHeight - HEIGHT - 2));
        surface.setPosition(x, y);
        filter.setX(x + 9);
        filter.setY(y + 17);
    }

    void setItems(List<String> items) {
        all = new ArrayList<>(items);
        refilter();
        int at = shown.indexOf(current);
        if (at >= 0) {
            select(at);
        }
    }

    private void refilter() {
        if (all == null) {
            return;
        }
        String q = filter.getValue().trim().toLowerCase(Locale.ROOT);
        List<String> out = new ArrayList<>();
        for (String name : all) {
            if (q.isEmpty() || name.toLowerCase(Locale.ROOT).contains(q)) {
                out.add(name);
            }
        }
        shown = out;
        selected = 0;
        scroll = 0;
    }

    private void select(int i) {
        if (shown.isEmpty()) {
            return;
        }
        selected = Math.max(0, Math.min(shown.size() - 1, i));
        if (selected < scroll) {
            scroll = selected;
        } else if (selected >= scroll + ROWS) {
            scroll = selected - ROWS + 1;
        }
    }

    private void scrollBy(int rows) {
        scroll = Math.max(0, Math.min(Math.max(0, shown.size() - ROWS), scroll + rows));
    }

    private void pick() {
        if (!shown.isEmpty()) {
            onPick.accept(shown.get(selected));
        }
    }

    boolean keyPressed(KeyEvent e) {
        switch (e.key()) {
            case 256 -> onCancel.run();                                  // Esc
            case 257, 335 -> pick();                                     // Enter
            case 264 -> select(selected + 1);                            // Down
            case 265 -> select(selected - 1);                            // Up
            case 267 -> select(selected + ROWS);                         // PageDown
            case 266 -> select(selected - ROWS);                         // PageUp
            case 258 -> select(e.hasShiftDown() ? selected - 1 : selected + 1);   // Tab
            default -> {
                return filter.keyPressed(e) || filter.canConsumeInput();
            }
        }
        return true;
    }

    /** Modal: every click is ours while open. */
    boolean mouseClicked(MouseButtonEvent e) {
        double mx = e.x(), my = e.y();
        if (!surface.isMouseOver(mx, my)) {
            onCancel.run();
            return true;
        }
        if (my >= y + 13 && my < y + LIST_TOP - 2) {
            filter.mouseClicked(e, false);
            return true;
        }
        int row = (int) ((my - (y + LIST_TOP)) / ROW);
        if (my >= y + LIST_TOP && row >= 0 && row < ROWS && scroll + row < shown.size()) {
            select(scroll + row);
            OmoStyle.click();
            pick();
        }
        return true;
    }

    boolean mouseScrolled(double mx, double my, double dy) {
        if (surface.isMouseOver(mx, my)) {
            scrollBy(dy > 0 ? -2 : 2);
        }
        return true;
    }

    /** The card: frame, title, list rows, scrollbar and key hints. */
    final class Surface extends AbstractWidget {
        Surface() {
            super(0, 0, WIDTH, HEIGHT, Component.literal("Hermes profiles"));
        }

        @Override
        protected void extractWidgetRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
            int x0 = getX(), y0 = getY(), x1 = getRight(), y1 = getBottom();
            OmoStyle.card(g, x0, y0, x1, y1, accent);
            g.fill(x0 + 1, y0 + 1, x1 - 1, y0 + 2, accent);
            g.text(font, "Hermes profile", x0 + 6, y0 + 5, accent, false);
            String count = all == null ? "" : shown.size() + "/" + all.size();
            g.text(font, count, x1 - 6 - font.width(count), y0 + 5, OmoStyle.FAINT, false);
            // filter field
            g.fill(x0 + 5, y0 + 14, x1 - 5, y0 + 27, OmoStyle.ESPRESSO);
            OmoStyle.frame(g, x0 + 5, y0 + 14, x1 - 5, y0 + 27, OmoStyle.alpha(accent, 0x90));
            int ly = y0 + LIST_TOP;
            if (all == null) {
                g.text(font, "loading profiles...", x0 + 8, ly + 2, OmoStyle.MUTED, false);
            } else if (shown.isEmpty()) {
                g.text(font, "no profile matches", x0 + 8, ly + 2, OmoStyle.MUTED, false);
            }
            for (int i = 0; i < ROWS && scroll + i < shown.size(); i++) {
                int idx = scroll + i;
                String name = shown.get(idx);
                int ry = ly + i * ROW;
                boolean hover = mouseX >= x0 && mouseX < x1 - 6 && mouseY >= ry && mouseY < ry + ROW;
                if (idx == selected) {
                    g.fill(x0 + 3, ry, x1 - 7, ry + ROW, OmoStyle.mix(OmoStyle.ESPRESSO, accent, 0.32F));
                    g.fill(x0 + 3, ry, x0 + 4, ry + ROW, accent);
                } else if (hover) {
                    g.fill(x0 + 3, ry, x1 - 7, ry + ROW, OmoStyle.ESPRESSO_HOVER);
                }
                boolean isCurrent = name.equals(current);
                if (isCurrent) {
                    OmoStyle.dot(g, x0 + 8, ry + 3, accent);
                }
                int colour = idx == selected ? OmoStyle.CREAM_BRIGHT : isCurrent ? accent : OmoStyle.CREAM;
                g.text(font, OmoStyle.fit(font, name, WIDTH - 36), x0 + 17, ry + 2, colour, false);
            }
            if (shown.size() > ROWS) {
                int trackH = ROWS * ROW;
                int thumbH = Math.max(8, trackH * ROWS / shown.size());
                int thumbY = ly + (trackH - thumbH) * scroll / Math.max(1, shown.size() - ROWS);
                g.fill(x1 - 5, ly, x1 - 3, ly + trackH, OmoStyle.alpha(OmoStyle.CREAM, 0x20));
                g.fill(x1 - 5, thumbY, x1 - 3, thumbY + thumbH, OmoStyle.alpha(accent, 0xC0));
            }
            g.text(font, "↑↓ move · Enter pick · Esc", x0 + 6, y1 - 11, OmoStyle.FAINT, false);
        }

        @Override
        protected void updateWidgetNarration(NarrationElementOutput out) {
            out.add(NarratedElementType.TITLE, Component.literal("Hermes profile picker"));
        }
    }
}

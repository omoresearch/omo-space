package space.omo.life.client;

import java.util.ArrayList;
import java.util.Comparator;
import java.util.List;
import java.util.Locale;
import java.util.function.Consumer;
import java.util.function.Function;

import net.minecraft.client.gui.Font;
import net.minecraft.client.gui.GuiGraphicsExtractor;
import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.EditBox;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.narration.NarratedElementType;
import net.minecraft.client.gui.narration.NarrationElementOutput;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;
import net.minecraft.network.chat.Component;

/**
 * Every popup of the station header, so they all look and behave alike: a
 * small card with an optional text box over a list. The workspace switcher
 * (search as you type, create from the query), the new-tab and "more" menus,
 * the tab list, the Hermes profile list and the rename prompts are all one
 * of these.
 *
 * <p>Keyboard first: type to filter (or to edit, in a prompt), Up/Down,
 * PageUp/PageDown or Tab to move, Enter to choose, Esc to close. Mouse: click
 * a row, click outside to close, the wheel scrolls. The panel routes keys and
 * clicks here first while a popup is open, so the terminal never sees them.
 */
final class Picker {

    /** One choice. {@code icon} and {@code dot} are optional (null / 0); {@code current} marks where you are. */
    record Row(String label, String detail, OmoStyle.Glyph icon, int iconColour, int dot, boolean current,
               Runnable action) {
        static Row of(String label, String detail, Runnable action) {
            return new Row(label, detail, null, 0, 0, false, action);
        }

        /** A row marked with a kind's glyph: the Hermes spark or the shell prompt. */
        static Row kind(boolean hermes, String label, String detail, Runnable action) {
            return new Row(label, detail, OmoStyle.kind(hermes), OmoStyle.accent(hermes), 0, false, action);
        }
    }

    private static final int ROW = 12;
    private static final int MAX_ROWS = 9;
    private static final int PAD = 4;
    private static final int TITLE_H = 13;
    private static final int INPUT_H = 15;
    private static final int NOTE_H = 12;
    private static final int RULE = 3;

    private final Font font;
    private final int accent;
    private final String title;
    private final int cardW;
    private final Runnable onClose;
    private final EditBox input;                     // null: a plain menu

    private List<Row> rows;                          // null while loading
    private Function<String, List<Row>> pinnedFor = q -> List.of();
    private Consumer<String> onSubmit;
    private String note = "";
    private String empty = "nothing matches";

    private List<Row> shown = List.of();             // the filtered rows; they scroll
    private List<Row> pinned = List.of();            // always visible below them
    private int selected;                            // over shown, then pinned
    private int scroll;
    private int anchorX;
    private int anchorY;
    private int screenW = Integer.MAX_VALUE;
    private int screenH = Integer.MAX_VALUE;
    private int x;
    private int y;

    final Surface surface;

    Picker(Font font, int accent, String title, int cardW, String inputHint, Runnable onClose) {
        this.font = font;
        this.accent = accent;
        this.title = title;
        this.cardW = cardW;
        this.onClose = onClose;
        this.surface = new Surface();
        if (inputHint == null) {
            input = null;
        } else {
            input = new EditBox(font, 0, 0, cardW - 18, 10, Component.literal(inputHint));
            input.setBordered(false);
            input.setMaxLength(40);
            input.setTextColor(OmoStyle.CREAM_BRIGHT);
            input.setHint(Component.literal(inputHint).withColor(OmoStyle.FAINT));
            input.setResponder(s -> refilter(false));
        }
    }

    Picker rows(List<Row> items) {
        rows = List.copyOf(items);
        refilter(true);
        return this;
    }

    /** Rows pinned below the list, built from what is typed (e.g. "New workspace 'x'"). */
    Picker pinned(Function<String, List<Row>> rowsFor) {
        pinnedFor = rowsFor;
        refilter(true);
        return this;
    }

    /** Enter with nothing to choose submits the text: a prompt. */
    Picker onSubmit(Consumer<String> submit) {
        onSubmit = submit;
        return this;
    }

    Picker value(String text) {
        if (input != null) {
            input.setValue(text);
        }
        return this;
    }

    Picker note(String text) {
        note = text;
        relayout();
        return this;
    }

    Picker empty(String text) {
        empty = text;
        return this;
    }

    List<AbstractWidget> widgets() {
        return input == null ? List.of(surface) : List.of(surface, input);
    }

    /** Who gets the keyboard while open: the text box, or the card itself (so typing never reaches the terminal). */
    GuiEventListener focusTarget() {
        return input != null ? input : surface;
    }

    private int count() {
        return shown.size() + pinned.size();
    }

    private Row row(int i) {
        return i < shown.size() ? shown.get(i) : pinned.get(i - shown.size());
    }

    private void refilter(boolean keepSelection) {
        String was = keepSelection && selected < count() ? row(selected).label() : null;
        String typed = input == null ? "" : input.getValue().trim();
        String q = onSubmit != null ? "" : typed.toLowerCase(Locale.ROOT);
        List<Row> out = new ArrayList<>();
        if (rows != null) {
            for (Row r : rows) {
                if (q.isEmpty() || (r.label() + " " + r.detail()).toLowerCase(Locale.ROOT).contains(q)) {
                    out.add(r);
                }
            }
        }
        if (!q.isEmpty()) {
            out.sort(Comparator.comparing(r -> !r.label().equalsIgnoreCase(q)));   // an exact name first
        }
        shown = out;
        pinned = rows == null ? List.of() : pinnedFor.apply(typed);
        selected = 0;
        for (int i = 0; i < count(); i++) {
            if (was != null ? row(i).label().equals(was) : q.isEmpty() && row(i).current()) {
                selected = i;
            }
        }
        scroll = 0;
        select(selected);
        relayout();
    }

    /** Lines of the scrolling list: its rows, or one line saying why there are none. */
    private int listRows() {
        if (rows == null) {
            return 1;                                // "loading..."
        }
        if (shown.isEmpty()) {
            return onSubmit != null || !pinned.isEmpty() ? 0 : 1;
        }
        return Math.min(MAX_ROWS, shown.size());
    }

    private int listTop() {
        return y + PAD + (title == null ? 0 : TITLE_H) + (input == null ? 0 : INPUT_H);
    }

    private int pinnedTop() {
        return listTop() + listRows() * ROW + (listRows() > 0 ? RULE : 0);
    }

    private int height() {
        int h = pinnedTop() - y + pinned.size() * ROW + PAD;
        return note.isEmpty() ? h : h + NOTE_H;
    }

    /** Top-left at (px, py), kept on a screen of this size as the list grows and shrinks. */
    void place(int px, int py, int w, int h) {
        anchorX = px;
        anchorY = py;
        screenW = w;
        screenH = h;
        relayout();
    }

    private void relayout() {
        surface.setHeight(height());
        x = Math.max(2, Math.min(anchorX, screenW - cardW - 2));
        y = Math.max(2, Math.min(anchorY, screenH - height() - 2));
        surface.setPosition(x, y);
        if (input != null) {
            input.setPosition(x + 9, y + PAD + (title == null ? 0 : TITLE_H) + 3);
        }
    }

    /** Top of row i, or -1 when it is scrolled out of view. */
    private int rowY(int i) {
        if (i >= shown.size()) {
            return pinnedTop() + (i - shown.size()) * ROW;
        }
        return i < scroll || i >= scroll + MAX_ROWS ? -1 : listTop() + (i - scroll) * ROW;
    }

    private void select(int i) {
        if (count() == 0) {
            return;
        }
        selected = Math.max(0, Math.min(count() - 1, i));
        if (selected < shown.size()) {
            if (selected < scroll) {
                scroll = selected;
            } else if (selected >= scroll + MAX_ROWS) {
                scroll = selected - MAX_ROWS + 1;
            }
        }
    }

    private void choose() {
        if (count() > 0) {
            Row r = row(selected);
            onClose.run();
            r.action().run();
        } else if (onSubmit != null && input != null) {
            onClose.run();
            onSubmit.accept(input.getValue().strip());
        }
    }

    boolean keyPressed(KeyEvent e) {
        switch (e.key()) {
            case 256 -> onClose.run();                                             // Esc
            case 257, 335 -> choose();                                             // Enter
            case 264 -> select(selected + 1);                                      // Down
            case 265 -> select(selected - 1);                                      // Up
            case 267 -> select(selected + MAX_ROWS);                               // PageDown
            case 266 -> select(selected - MAX_ROWS);                               // PageUp
            case 258 -> select(e.hasShiftDown() ? selected - 1 : selected + 1);    // Tab
            default -> {
                // A focused text box owns every other key ("e" must not close the
                // screen); a plain menu swallows them.
                return input == null || input.keyPressed(e) || input.canConsumeInput();
            }
        }
        return true;
    }

    /** Modal: every click is ours while open. */
    boolean mouseClicked(MouseButtonEvent e) {
        double mx = e.x(), my = e.y();
        if (!surface.isMouseOver(mx, my)) {
            onClose.run();
            return true;
        }
        if (input != null && my < listTop()) {
            input.mouseClicked(e, false);
            return true;
        }
        for (int i = 0; i < count(); i++) {
            int ry = rowY(i);
            if (ry >= 0 && my >= ry && my < ry + ROW) {
                select(i);
                OmoStyle.click();
                choose();
                break;
            }
        }
        return true;
    }

    boolean mouseScrolled(double mx, double my, double dy) {
        if (surface.isMouseOver(mx, my)) {
            scroll = Math.max(0, Math.min(Math.max(0, shown.size() - MAX_ROWS), scroll + (dy > 0 ? -2 : 2)));
        }
        return true;
    }

    /** The card: frame, title, text box well, rows, scrollbar, pinned rows and note. */
    final class Surface extends AbstractWidget {
        Surface() {
            super(0, 0, cardW, PAD * 2 + ROW, Component.literal(title == null ? "menu" : title));
        }

        @Override
        protected void extractWidgetRenderState(GuiGraphicsExtractor g, int mouseX, int mouseY, float partial) {
            int x0 = getX(), y0 = getY(), x1 = getRight(), y1 = getBottom();
            OmoStyle.card(g, x0, y0, x1, y1, accent);
            g.fill(x0 + 1, y0 + 1, x1 - 1, y0 + 2, accent);
            int ty = y0 + PAD;
            if (title != null) {
                g.text(font, title, x0 + 7, ty + 2, accent, false);
                if (rows != null && input != null && onSubmit == null) {
                    String n = shown.size() + "/" + rows.size();
                    g.text(font, n, x1 - 7 - font.width(n), ty + 2, OmoStyle.FAINT, false);
                }
                ty += TITLE_H;
            }
            if (input != null) {
                g.fill(x0 + 5, ty, x1 - 5, ty + 13, OmoStyle.ESPRESSO);
                OmoStyle.frame(g, x0 + 5, ty, x1 - 5, ty + 13, OmoStyle.alpha(accent, 0x90));
            }
            int ly = listTop();
            if (rows == null || shown.isEmpty() && listRows() > 0) {
                g.text(font, rows == null ? "loading..." : empty, x0 + 8, ly + 2, OmoStyle.MUTED, false);
            }
            boolean icons = false;
            for (int i = 0; i < count(); i++) {
                icons |= row(i).icon() != null;
            }
            for (int i = 0; i < count(); i++) {
                int ry = rowY(i);
                if (ry >= 0) {
                    drawRow(g, row(i), i, ry, icons, mouseX, mouseY);
                }
            }
            if (!pinned.isEmpty() && listRows() > 0) {
                int ry = pinnedTop() - 2;
                g.fill(x0 + 6, ry, x1 - 6, ry + 1, OmoStyle.alpha(accent, 0x40));
            }
            if (shown.size() > MAX_ROWS) {
                int trackH = MAX_ROWS * ROW;
                int thumbH = Math.max(8, trackH * MAX_ROWS / shown.size());
                int thumbY = ly + (trackH - thumbH) * scroll / Math.max(1, shown.size() - MAX_ROWS);
                g.fill(x1 - 5, ly, x1 - 3, ly + trackH, OmoStyle.alpha(OmoStyle.CREAM, 0x20));
                g.fill(x1 - 5, thumbY, x1 - 3, thumbY + thumbH, OmoStyle.alpha(accent, 0xC0));
            }
            if (!note.isEmpty()) {
                g.text(font, OmoStyle.fit(font, note, cardW - 14), x0 + 7, y1 - PAD - 8, OmoStyle.FAINT, false);
            }
        }

        private void drawRow(GuiGraphicsExtractor g, Row r, int i, int ry, boolean icons, int mouseX, int mouseY) {
            int x0 = getX(), x1 = getRight() - (i < shown.size() && shown.size() > MAX_ROWS ? 6 : 0);
            boolean hover = mouseX >= x0 && mouseX < x1 && mouseY >= ry && mouseY < ry + ROW;
            if (i == selected) {
                g.fill(x0 + 3, ry, x1 - 3, ry + ROW, OmoStyle.mix(OmoStyle.ESPRESSO, accent, 0.30F));
            } else if (hover) {
                g.fill(x0 + 3, ry, x1 - 3, ry + ROW, OmoStyle.ESPRESSO_HOVER);
            }
            if (r.current()) {
                g.fill(x0 + 3, ry + 1, x0 + 4, ry + ROW - 1, accent);
            }
            int lx = x0 + 8;
            if (r.icon() != null) {
                OmoStyle.glyph(g, r.icon(), lx, ry + (ROW - r.icon().height()) / 2, r.iconColour());
            }
            if (icons) {
                lx += 12;                              // labels line up whether or not a row has an icon
            }
            int rx = x1 - 8;
            if (!r.detail().isEmpty()) {
                String d = OmoStyle.fit(font, r.detail(), (x1 - x0) / 2 - 8);
                rx -= font.width(d);
                g.text(font, d, rx, ry + 2, i == selected ? OmoStyle.CREAM : OmoStyle.FAINT, false);
                rx -= 6;
            }
            if (r.dot() != 0) {
                rx -= 5;
                OmoStyle.dot(g, rx, ry + 3, r.dot());
                rx -= 5;
            }
            int colour = i == selected ? OmoStyle.CREAM_BRIGHT
                : r.current() ? OmoStyle.mix(OmoStyle.CREAM, accent, 0.5F) : OmoStyle.CREAM;
            g.text(font, OmoStyle.fit(font, r.label(), rx - lx - 2), lx, ry + 2, colour, false);
        }

        @Override
        protected void updateWidgetNarration(NarrationElementOutput out) {
            out.add(NarratedElementType.TITLE, getMessage());
        }
    }
}

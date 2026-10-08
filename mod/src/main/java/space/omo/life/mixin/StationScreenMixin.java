package space.omo.life.mixin;

import java.util.ArrayList;
import java.util.List;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.Unique;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.client.gui.AbstractComputerScreen;
import dan200.computercraft.client.gui.widgets.TerminalWidget;

import net.minecraft.client.gui.components.AbstractWidget;
import net.minecraft.client.gui.components.events.GuiEventListener;
import net.minecraft.client.gui.screens.Screen;
import net.minecraft.client.input.KeyEvent;
import net.minecraft.client.input.MouseButtonEvent;

import space.omo.life.client.StationPanel;
import space.omo.life.client.StationScreens;

/**
 * Turns CC:T's computer screen into a station screen when the computer is
 * an Omo station.
 *
 * <p>The display stack the server hands the client carries the station's
 * binding (see {@code StationBlockEntity.screenStack}); that is how the
 * client knows this screen is a station and which workspace it shows.
 * Everything terminal-shaped stays CC:T's: this mixin adds the workspace
 * header above the terminal and lets the header see keys and clicks first,
 * so its shortcuts, its text boxes and its popups never leak into the
 * terminal (or into "e closes the inventory"). Block stations and Omo
 * Pockets get the same screen: the panel never asks which it is.
 */
@Mixin(AbstractComputerScreen.class)
public abstract class StationScreenMixin {

    @Shadow(remap = false)
    protected TerminalWidget terminal;

    @Unique
    private StationPanel omolife$panel;

    /**
     * Before anything is laid out: a station is an 80-column terminal plus
     * the header, which does not fit a short window at the GUI scale
     * Minecraft picks (a 1920x800 window gets scale 3). Step the scale down
     * until it fits; the player's own scale comes back when the screen closes.
     */
    @Inject(method = "init", at = @At("HEAD"))
    private void omolife$fitWindow(CallbackInfo ci) {
        AbstractComputerScreen<?> screen = (AbstractComputerScreen<?>) (Object) this;
        if (StationPanel.Binding.from(screen.getMenu().getDisplayStack()) == null) {
            return;
        }
        ContainerScreenAccessor acc = (ContainerScreenAccessor) this;
        StationScreens.fitGuiScale(screen, acc.omolife$imageWidth() + 8,
            acc.omolife$imageHeight() + StationPanel.HEIGHT + 8);
    }

    /**
     * Before CC:T lays out its terminal: move the GUI down far enough for the
     * header, as far as the window allows. (On a window too short for both,
     * the header overlaps the top of the frame rather than hiding.)
     */
    @Inject(method = "init", at = @At(value = "INVOKE", shift = At.Shift.AFTER,
        target = "Lnet/minecraft/client/gui/screens/inventory/AbstractContainerScreen;init()V"))
    private void omolife$makeRoom(CallbackInfo ci) {
        AbstractComputerScreen<?> screen = (AbstractComputerScreen<?>) (Object) this;
        if (StationPanel.Binding.from(screen.getMenu().getDisplayStack()) == null) {
            return;
        }
        ContainerScreenAccessor acc = (ContainerScreenAccessor) this;
        int need = StationPanel.HEIGHT + 4;
        int top = acc.omolife$topPos();
        if (top < need) {
            acc.omolife$setTopPos(Math.max(top, Math.min(need, screen.height - acc.omolife$imageHeight())));
        }
    }

    @Inject(method = "init", at = @At("TAIL"))
    private void omolife$addPanel(CallbackInfo ci) {
        Screen self = (Screen) (Object) this;
        AbstractComputerScreen<?> screen = (AbstractComputerScreen<?>) (Object) this;
        StationPanel.Binding binding = StationPanel.Binding.from(screen.getMenu().getDisplayStack());
        if (binding == null) {
            return;
        }
        ScreenAccessor widgets = (ScreenAccessor) self;
        List<AbstractWidget> mine = new ArrayList<>();
        omolife$panel = new StationPanel(binding, self.getFont(),
            w -> {
                mine.add(w);
                widgets.omolife$addRenderableWidget(w);
            },
            () -> {
                mine.forEach(widgets::omolife$removeWidget);
                mine.clear();
            },
            l -> self.setFocused(l == null ? terminal : l));
        // A re-init (resize, GUI scale) replaces the panel; the old one stops here.
        StationScreens.register(self, omolife$panel);
        omolife$panel.layout(terminal.getX(), terminal.getY(), terminal.getRight(), terminal.getBottom(),
            self.width, self.height);
        omolife$panel.open();
        self.setFocused(terminal);
    }

    @Inject(method = "containerTick", at = @At("TAIL"))
    private void omolife$tick(CallbackInfo ci) {
        if (omolife$panel != null) {
            omolife$panel.tick();
        }
        omolife$claimKeyboard();
    }

    /**
     * The header's keys first. Esc is never a terminal key here: it closes
     * the screen, always, as on any Minecraft screen (a running program gets
     * its Escape from Ctrl+[, which the station client sends as ESC).
     */
    @Inject(method = "keyPressed", at = @At("HEAD"), cancellable = true)
    private void omolife$keys(KeyEvent event, CallbackInfoReturnable<Boolean> cir) {
        if (omolife$panel != null && omolife$panel.keyPressed(event)) {
            cir.setReturnValue(true);
        } else {
            omolife$claimKeyboard();
        }
    }

    /**
     * Keys nobody has go to the terminal. Some modpacks
     * leave a computer screen with nothing focused once it opens: every key
     * then missed the terminal and "e" closed the screen as an inventory
     * would. Any computer screen, station or not.
     */
    @Unique
    private void omolife$claimKeyboard() {
        Screen self = (Screen) (Object) this;
        if (self.getFocused() == null) {
            self.setFocused(terminal);
        }
    }

    @Inject(method = "mouseClicked", at = @At("HEAD"), cancellable = true)
    private void omolife$click(MouseButtonEvent event, boolean doubleClick, CallbackInfoReturnable<Boolean> cir) {
        if (omolife$panel != null && omolife$panel.mouseClicked(event)) {
            cir.setReturnValue(true);
        }
    }

    /**
     * A clicked header control must not keep keyboard focus (vanilla focuses
     * whatever was clicked, after its action ran): typing belongs to the
     * terminal, or to the open popup.
     */
    @Inject(method = "mouseClicked", at = @At("RETURN"))
    private void omolife$refocus(MouseButtonEvent event, boolean doubleClick, CallbackInfoReturnable<Boolean> cir) {
        Screen self = (Screen) (Object) this;
        if (omolife$panel != null && omolife$panel.owns(self.getFocused())) {
            GuiEventListener next = omolife$panel.focusAfterClick();
            self.setFocused(next == null ? terminal : next);
        }
    }

    @Inject(method = "mouseScrolled", at = @At("HEAD"), cancellable = true)
    private void omolife$scroll(double x, double y, double dx, double dy, CallbackInfoReturnable<Boolean> cir) {
        if (omolife$panel != null && omolife$panel.mouseScrolled(x, y, dy)) {
            cir.setReturnValue(true);
        }
    }
}

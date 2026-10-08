package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import dan200.computercraft.core.input.UserComputerInput;

/**
 * Let the mouse work on a BASIC computer.
 *
 * <p>{@code UserComputerInput} gates every mouse method behind
 * {@code if (mouseSupport)}, and {@code mouseSupport} is set from
 * {@code terminal.isColour()} in the constructor. On a non-Advanced computer
 * CC:T therefore drops clicks, drags, releases and wheel events silently —
 * nothing happens and nothing is logged. That kills drag-to-select and
 * copy/paste in the terminal, not just scrolling.
 *
 * <p>Each injection re-dispatches through the raw {@code ComputerInput}
 * delegate, which has no family gate, then cancels so CC:T's own early return
 * is skipped. Advanced computers are left entirely alone.
 */
@Mixin(UserComputerInput.class)
public abstract class MouseSupportMixin {

    private boolean omolife$gated() {
        return !((ComputerInputAccessor) (Object) this).omolife$mouseSupport();
    }

    @Inject(method = "mouseScroll(III)V", at = @At("HEAD"), cancellable = true)
    private void omolife$allowScrollOnBasic(int direction, int x, int y,
                                            CallbackInfo ci) {
        if (!omolife$gated()) {
            return;                     // Advanced: CC:T handles it already
        }
        ((ComputerInputAccessor) (Object) this).omolife$delegate()
            .mouseScroll(direction, x, y);
        ci.cancel();
    }

    @Inject(method = "mouseClick(III)V", at = @At("HEAD"), cancellable = true)
    private void omolife$allowClickOnBasic(int button, int x, int y,
                                           CallbackInfo ci) {
        if (!omolife$gated()) {
            return;
        }
        ((ComputerInputAccessor) (Object) this).omolife$delegate()
            .mouseClick(button, x, y);
        ci.cancel();
    }

    @Inject(method = "mouseUp(III)V", at = @At("HEAD"), cancellable = true)
    private void omolife$allowUpOnBasic(int button, int x, int y,
                                        CallbackInfo ci) {
        if (!omolife$gated()) {
            return;
        }
        ((ComputerInputAccessor) (Object) this).omolife$delegate()
            .mouseUp(button, x, y);
        ci.cancel();
    }

    @Inject(method = "mouseDrag(III)V", at = @At("HEAD"), cancellable = true)
    private void omolife$allowDragOnBasic(int button, int x, int y,
                                          CallbackInfo ci) {
        if (!omolife$gated()) {
            return;
        }
        ((ComputerInputAccessor) (Object) this).omolife$delegate()
            .mouseDrag(button, x, y);
        ci.cancel();
    }
}

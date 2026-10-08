package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.pocket.core.PocketBrain;
import dan200.computercraft.shared.pocket.core.PocketHolder;
import dan200.computercraft.shared.pocket.items.PocketComputerItem;

import net.minecraft.world.InteractionHand;
import net.minecraft.world.InteractionResult;
import net.minecraft.world.entity.player.Player;
import net.minecraft.world.item.ItemStack;
import net.minecraft.world.level.Level;

import space.omo.life.pocket.Pockets;

/**
 * CC:T's pocket computers (its own and the Omo Pockets, which extend it)
 * are stations: their computer is built, ticked and opened through
 * {@link Pockets}.
 */
@Mixin(value = PocketComputerItem.class, remap = false)
public abstract class PocketComputerMixin {

    @Inject(method = "getOrCreateBrain", at = @At("HEAD"), cancellable = true)
    private void omolife$buildStation(PocketHolder holder, ItemStack stack, CallbackInfoReturnable<PocketBrain> cir) {
        PocketBrain brain = Pockets.build((PocketComputerItem) (Object) this, holder, stack);
        if (brain != null) {
            cir.setReturnValue(brain);
        }
    }

    @Inject(method = "tick", at = @At("TAIL"))
    private void omolife$tickStation(ItemStack stack, PocketHolder holder, PocketBrain brain, CallbackInfo ci) {
        Pockets.tick(stack, holder, brain);
    }

    @Inject(method = "use", at = @At("HEAD"), cancellable = true)
    private void omolife$openStation(Level level, Player player, InteractionHand hand,
                                     CallbackInfoReturnable<InteractionResult> cir) {
        InteractionResult result = Pockets.use((PocketComputerItem) (Object) this, level, player, hand);
        if (result != null) {
            cir.setReturnValue(result);
        }
    }
}

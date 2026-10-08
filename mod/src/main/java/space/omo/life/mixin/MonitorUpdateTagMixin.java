package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.peripheral.monitor.MonitorBlockEntity;

import net.minecraft.core.HolderLookup;
import net.minecraft.nbt.CompoundTag;

import space.omo.life.block.OmoMonitorBlockEntity;

/**
 * Put the Omo monitor's page binding into the client update tag.
 *
 * <p>{@code MonitorBlockEntity.getUpdateTag} is final and only carries CC:T's
 * own geometry, so without this the client would never learn which page a
 * monitor shows. The tag is read back by {@code loadAdditional} on the client.
 */
@Mixin(MonitorBlockEntity.class)
public abstract class MonitorUpdateTagMixin {

    @Inject(method = "getUpdateTag(Lnet/minecraft/core/HolderLookup$Provider;)Lnet/minecraft/nbt/CompoundTag;",
            at = @At("RETURN"), remap = true)
    private void omolife$addBinding(HolderLookup.Provider registries, CallbackInfoReturnable<CompoundTag> cir) {
        if ((Object) this instanceof OmoMonitorBlockEntity be) {
            be.writeBinding(cir.getReturnValue());
        }
    }
}

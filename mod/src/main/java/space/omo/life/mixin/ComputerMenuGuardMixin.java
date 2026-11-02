package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.Shadow;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.computer.core.ServerComputer;
import dan200.computercraft.shared.computer.inventory.AbstractComputerMenu;

import net.minecraft.server.level.ServerPlayer;
import net.minecraft.world.entity.player.Player;

import space.omo.life.station.StationComputers;
import space.omo.life.station.Stations;

/**
 * A CC:T computer GUI on a station stays open only for players who may use
 * stations: the server closes it for anyone else on the next tick
 * ({@code stillValid}), so the screen never shows the host shell to them.
 */
@Mixin(value = AbstractComputerMenu.class, remap = false)
public abstract class ComputerMenuGuardMixin {

    @Shadow
    public abstract ServerComputer getComputer();

    @Inject(method = "stillValid", at = @At("RETURN"), cancellable = true, remap = true)
    private void omolife$stationOnly(Player player, CallbackInfoReturnable<Boolean> cir) {
        if (cir.getReturnValueZ() && player instanceof ServerPlayer sp && getComputer() != null
            && StationComputers.of(getComputer()) != null && !Stations.allowed(sp)) {
            cir.setReturnValue(false);
        }
    }
}

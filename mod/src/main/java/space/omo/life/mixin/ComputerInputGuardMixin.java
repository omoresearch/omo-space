package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import dan200.computercraft.shared.computer.menu.ComputerMenu;
import dan200.computercraft.shared.network.server.ComputerServerMessage;
import dan200.computercraft.shared.network.server.ServerNetworkContext;

import net.minecraft.server.level.ServerPlayer;

import space.omo.life.station.CommandGate;

/**
 * Every CC:T computer-GUI packet (keys, mouse, paste, file upload, turn
 * on/shutdown/terminate) goes through {@code ComputerServerMessage.handle}.
 * For a station computer it is dropped unless the sender may use stations,
 * however the GUI was opened ({@code /computercraft view}, a held pocket,
 * a menu left open after being deopped). A use by the owner counts as a
 * live owner interaction.
 */
@Mixin(value = ComputerServerMessage.class, remap = false)
public abstract class ComputerInputGuardMixin {

    @Inject(method = "handle(Ldan200/computercraft/shared/network/server/ServerNetworkContext;)V",
            at = @At("HEAD"), cancellable = true)
    private void omolife$guard(ServerNetworkContext context, CallbackInfo ci) {
        ServerPlayer player = context.getSender();
        if (player != null && player.containerMenu instanceof ComputerMenu menu
            && !CommandGate.mayUseComputer(player, menu.getComputer())) {
            ci.cancel();
        }
    }
}

package space.omo.life.mixin;

import java.util.Collection;
import java.util.List;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import dan200.computercraft.shared.command.CommandComputerCraft;
import dan200.computercraft.shared.computer.core.ServerComputer;

import net.minecraft.commands.CommandSourceStack;

import space.omo.life.station.CommandGate;

/**
 * CC:T's {@code /computercraft view} opens any computer's GUI for an
 * operator (in a LAN world with cheats on, every joiner), which on a station
 * means typing into the host shell; {@code shutdown}/{@code turn-on} match
 * the owner by name. On station computers these follow the station rule
 * instead ({@link CommandGate}). Plain CC:T computers are untouched.
 */
@Mixin(value = CommandComputerCraft.class, remap = false)
public abstract class ComputerCraftCommandMixin {

    @Inject(method = "view", at = @At("HEAD"), cancellable = true)
    private static void omolife$view(CommandSourceStack src, ServerComputer computer,
                                     CallbackInfoReturnable<Integer> cir) {
        if (CommandGate.refuseStations(src, List.of(computer))) {
            cir.setReturnValue(0);
        }
    }

    @Inject(method = "teleport", at = @At("HEAD"), cancellable = true)
    private static void omolife$teleport(CommandSourceStack src, ServerComputer computer,
                                         CallbackInfoReturnable<Integer> cir) {
        if (CommandGate.refuseStations(src, List.of(computer))) {
            cir.setReturnValue(0);
        }
    }

    @Inject(method = "shutdown", at = @At("HEAD"), cancellable = true)
    private static void omolife$shutdown(CommandSourceStack src, Collection<ServerComputer> computers,
                                         CallbackInfoReturnable<Integer> cir) {
        if (CommandGate.refuseStations(src, computers)) {
            cir.setReturnValue(0);
        }
    }

    @Inject(method = "turnOn", at = @At("HEAD"), cancellable = true)
    private static void omolife$turnOn(CommandSourceStack src, Collection<ServerComputer> computers,
                                       CallbackInfoReturnable<Integer> cir) {
        if (CommandGate.refuseStations(src, computers)) {
            cir.setReturnValue(0);
        }
    }
}

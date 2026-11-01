package space.omo.life.mixin;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfoReturnable;

import net.minecraft.server.MinecraftServer;
import net.minecraft.server.permissions.LevelBasedPermissionSet;
import net.minecraft.server.permissions.PermissionLevel;
import net.minecraft.server.players.NameAndId;

import space.omo.life.client.AgentLanFlags;

/**
 * Agent LAN publishes the world with commands OFF for joiners. Only the Omo
 * agent bodies (names {@link AgentLanFlags#isAgent} knows), and only in a
 * world with cheats on, get gamemaster level so they can /tp and
 * /gamemode, without an ops-list entry (which would also give them
 * host access through {@code Stations.allowed}). Station computers stay
 * closed to them either way ({@code CommandGate}).
 */
@Mixin(MinecraftServer.class)
public abstract class AgentPermissionsMixin {

    @Inject(method = "getProfilePermissions", at = @At("RETURN"), cancellable = true)
    private void omolife$agentCommands(NameAndId who, CallbackInfoReturnable<LevelBasedPermissionSet> cir) {
        if (AgentLanFlags.agentCommands && who != null && AgentLanFlags.isAgent(who.name())
            && !cir.getReturnValue().level().isEqualOrHigherThan(PermissionLevel.GAMEMASTERS)) {
            cir.setReturnValue(LevelBasedPermissionSet.GAMEMASTER);
        }
    }
}

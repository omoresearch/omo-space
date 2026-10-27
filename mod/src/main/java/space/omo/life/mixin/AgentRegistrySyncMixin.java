package space.omo.life.mixin;

import java.util.Locale;
import java.util.Set;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.Inject;
import org.spongepowered.asm.mixin.injection.callback.CallbackInfo;

import net.fabricmc.fabric.impl.registry.sync.RegistrySyncManager;
import net.minecraft.server.MinecraftServer;
import net.minecraft.server.network.ServerConfigurationPacketListenerImpl;

/**
 * Lets the local Omo agent body (a plain Mineflayer client, no Fabric) past
 * Fabric's registry-sync check, which otherwise kicks any client without
 * Fabric API from a modded world.
 *
 * Only for the agent names below, and only while the world runs in agent LAN
 * mode (authentication off, see AgentLan). AgentLan's join guard still kicks
 * the same name if it connects from another computer. Fabric keeps vanilla ids
 * stable and appends modded ones, so the bot reads vanilla blocks correctly
 * and simply sees modded blocks as unknown ids.
 */
@Mixin(value = RegistrySyncManager.class, remap = false)
public abstract class AgentRegistrySyncMixin {
    private static final Set<String> OMO_AGENT_NAMES = Set.of(
        System.getProperty("omo.agents.names", "teacher").toLowerCase(Locale.ROOT).split(","));

    @Inject(method = "configureClient", at = @At("HEAD"), cancellable = true)
    private static void omolife$skipForLocalAgents(ServerConfigurationPacketListenerImpl handler,
            MinecraftServer server, CallbackInfo ci) {
        if (server.usesAuthentication() || handler.getOwner() == null) return;
        String name = handler.getOwner().name();
        if (name != null && OMO_AGENT_NAMES.contains(name.toLowerCase(Locale.ROOT))) ci.cancel();
    }
}

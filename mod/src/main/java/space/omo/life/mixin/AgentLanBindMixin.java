package space.omo.life.mixin;

import java.net.InetAddress;

import org.spongepowered.asm.mixin.Mixin;
import org.spongepowered.asm.mixin.injection.At;
import org.spongepowered.asm.mixin.injection.ModifyArg;
import org.spongepowered.asm.mixin.injection.Redirect;

import net.minecraft.client.server.IntegratedServer;
import net.minecraft.client.server.LanServerPinger;
import space.omo.life.client.AgentLan;

/**
 * Vanilla's publishServer always listens on every interface and broadcasts the
 * world on the LAN. While {@link AgentLan} publishes for local agent bodies,
 * the listener binds to 127.0.0.1 instead and the broadcast is skipped, so
 * the offline-mode world is not reachable from any other computer. The Open
 * to LAN screen is unaffected.
 */
@Mixin(IntegratedServer.class)
public abstract class AgentLanBindMixin {

    @ModifyArg(method = "publishServer(Lnet/minecraft/server/MinecraftServer$MultiplayerScope;I)Z",
        at = @At(value = "INVOKE",
            target = "Lnet/minecraft/server/network/ServerConnectionListener;startTcpServerListener(Ljava/net/InetAddress;I)V"),
        index = 0)
    private InetAddress omolife$agentBind(InetAddress requested) {
        return AgentLan.bindAddress(requested);
    }

    @Redirect(method = "publishServer(Lnet/minecraft/server/MinecraftServer$MultiplayerScope;I)Z",
        at = @At(value = "INVOKE", target = "Lnet/minecraft/client/server/LanServerPinger;start()V"))
    private void omolife$agentAnnounce(LanServerPinger pinger) {
        if (AgentLan.announceOnLan()) {
            pinger.start();
        }
    }
}
